"""MQTT (``paho-mqtt`` client + ``amqtt`` broker) benchmark, two OS processes.

Run standalone as ``python -m benchmarks.mqtt_bench --role publisher|subscriber ...``
(see :func:`benchmarks.common.runner.build_common_arg_parser` for the shared CLI), or —
normally — via :func:`benchmarks.common.runner.run_subprocess_scenario` from
:mod:`benchmarks.run_bench`. ``paho-mqtt`` (callback API v2) is the client, since it's the
most common MQTT client in real deployments; ``amqtt`` is a pure-Python, pip-installable,
asyncio-native broker, chosen so this suite doesn't need a new Docker image or apt
dependency just to have something for the client to talk to.

MQTT is the odd one out in this suite structurally: every other middleware here is
either brokerless point-to-point (h2r, ZeroMQ, gRPC, Zenoh, raw TCP/UDP, WebSocket) or a
peer-discovery mesh (ROS 2/DDS) — MQTT *requires* a broker as intermediary. There's no
separate broker process to launch here, though: to avoid adding a third role and a third
OS process to every scenario, the publisher process starts an in-process ``amqtt.Broker``
first and only then connects its own ``paho-mqtt`` client to it, so "start the broker" and
"publish" are both this process's job — the subscriber only ever sees an ordinary MQTT
broker at ``host:port``, no different from a real deployment's standalone broker.

Both the publisher's and subscriber's ``paho-mqtt`` clients open ordinary TCP connections
to that broker, and — like :mod:`benchmarks.tcp_bench` — the two OS processes here start
at roughly the same time (see :func:`benchmarks.common.runner.run_subprocess_scenario`),
so ``Client.connect()`` retries past ``ConnectionRefusedError`` until the broker is
listening. That's an ordinary TCP-level connection race, not this benchmark's
handshake-probe (below) — MQTT needs *both*.

Once connected, though, MQTT's pub/sub is itself broker-mediated store-and-forward: the
broker only forwards a publish to subscribers whose ``SUBSCRIBE`` it has already
processed, so a subscriber whose ``SUBSCRIBE`` hasn't reached the broker yet simply misses
whatever was published in the meantime — the same "late joiner" trait h2r, ZeroMQ, Zenoh,
and ROS 2 have (for a different reason: no broker to remember interest, here, versus no
replay there). So, like :mod:`benchmarks.zmq_bench`, this uses the ``seq=-1``
handshake-probe pattern (``supports_handshake=True``): the publisher probes until the
subscriber's result directory shows a ready file, which the subscriber writes the moment
it sees a live probe.

QoS 0 (at-most-once) throughout — the "no delivery guarantee" behavior every other
middleware in this suite has, and MQTT's default; QoS 1/2's broker-side retry and
deduplication are out of scope here, matching this suite's policy of measuring each
middleware's baseline behavior with no artificial reliability added on top.

The publisher's ``amqtt`` broker and its own ``paho-mqtt`` client (used for the probe and
real-message publish loop) share this process's single asyncio event loop, so — like
:mod:`benchmarks.h2r_bench`'s publish loop — every iteration ``await``s once to give the
broker's own internal tasks (accepting the subscriber's connection, relaying its queued
messages) a chance to run; without that, this process would starve the broker for however
long the whole send loop takes. A short fixed drain wait after the last real message gives
the broker time to relay whatever's still in flight before this process tears the broker
down — a finite wait for outstanding messages already in the pipe, the same purpose
:mod:`benchmarks.zmq_bench`'s PUB socket ``linger`` serves, not an artificial retry.
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import TYPE_CHECKING

import paho.mqtt.client as mqtt
from amqtt.broker import Broker
from amqtt.contexts import BrokerConfig
from amqtt.contexts import ListenerConfig
from amqtt.contexts import ListenerType

from benchmarks.common import payload
from benchmarks.common import runner
from benchmarks.common import stats

if TYPE_CHECKING:
    import pathlib

_TOPIC = "bench/topic"
_QOS = 0
_PROBE_INTERVAL_S = 0.001
_CONNECT_RETRY_INTERVAL_S = 0.01
_OVERALL_TIMEOUT_S = 60.0
_DRAIN_S = 2.0
"""How long the publisher keeps the broker (and its own client) alive after the last real
message, to let the broker relay whatever's still in flight; see module docstring."""


def _connect_with_retry(client: mqtt.Client, host: str, port: int, *, deadline: float) -> None:
    """Connect *client* to the broker at ``host:port``, retrying past a refused connection.

    See module docstring: an ordinary TCP-level connection race between the publisher
    (which starts the broker) and subscriber processes starting at roughly the same time,
    distinct from this benchmark's MQTT-level handshake-probe.
    """
    while True:
        try:
            client.connect(host, port)
        except OSError:
            if time.perf_counter() > deadline:
                raise
            time.sleep(_CONNECT_RETRY_INTERVAL_S)
        else:
            return


async def _run_broker_and_publish(
    *,
    host: str,
    port: int,
    size: int,
    count: int,
    ready_file: pathlib.Path,
) -> None:
    broker_config = BrokerConfig(
        listeners={"default": ListenerConfig(type=ListenerType.TCP, bind=f"{host}:{port}")},
    )
    broker = Broker(config=broker_config)
    await broker.start()
    try:
        client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
        _connect_with_retry(
            client,
            host,
            port,
            deadline=time.perf_counter() + _OVERALL_TIMEOUT_S,
        )
        client.loop_start()
        try:
            while not ready_file.exists():
                probe = payload.build_payload(payload.HANDSHAKE_SEQ, size)
                client.publish(_TOPIC, probe, qos=_QOS)
                await asyncio.sleep(_PROBE_INTERVAL_S)

            for seq in range(count):
                client.publish(_TOPIC, payload.build_payload(seq, size), qos=_QOS)
                await asyncio.sleep(0)

            await asyncio.sleep(_DRAIN_S)
        finally:
            client.loop_stop()
            client.disconnect()
    finally:
        await broker.shutdown()


def _run_publisher(
    *,
    host: str,
    port: int,
    size: int,
    count: int,
    ready_file: pathlib.Path,
) -> None:
    asyncio.run(
        _run_broker_and_publish(
            host=host,
            port=port,
            size=size,
            count=count,
            ready_file=ready_file,
        ),
    )


def _run_subscriber(
    *,
    host: str,
    port: int,
    size: int,
    count: int,
    ready_file: pathlib.Path,
    result_file: pathlib.Path,
) -> None:
    gap_tracker = stats.SequenceGapTracker()
    latencies_ns: list[int] = []
    recv_start_s = 0.0
    recv_end_s = 0.0
    startup_connect_s = 0.0
    handshake_start = time.perf_counter()
    done_event = threading.Event()

    def _on_connect(
        client: mqtt.Client,
        userdata: object,
        flags: mqtt.ConnectFlags,
        reason_code: mqtt.ReasonCode,
        properties: mqtt.Properties | None,
    ) -> None:
        del userdata, flags, reason_code, properties
        client.subscribe(_TOPIC, qos=_QOS)

    def _on_message(client: mqtt.Client, userdata: object, message: mqtt.MQTTMessage) -> None:
        del client, userdata
        nonlocal recv_start_s, recv_end_s, startup_connect_s
        seq, send_ns = payload.parse_payload(message.payload)
        if seq == payload.HANDSHAKE_SEQ:
            if not ready_file.exists():
                startup_connect_s = time.perf_counter() - handshake_start
                ready_file.write_text(str(startup_connect_s))
            return
        recv_ns = time.perf_counter_ns()
        now_s = time.perf_counter()
        if recv_start_s == 0.0:
            recv_start_s = now_s
        recv_end_s = now_s
        gap_tracker.observe(seq)
        latencies_ns.append(recv_ns - send_ns)
        if gap_tracker.received_count >= count:
            done_event.set()

    client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = _on_connect
    client.on_message = _on_message
    _connect_with_retry(client, host, port, deadline=time.perf_counter() + _OVERALL_TIMEOUT_S)
    client.loop_start()
    try:
        done_event.wait(timeout=_OVERALL_TIMEOUT_S)
    finally:
        client.loop_stop()
        client.disconnect()

    recv_duration_s = max(0.0, recv_end_s - recv_start_s)
    report = runner.build_subscriber_report(
        message_size_bytes=size,
        latencies_ns=latencies_ns,
        dropped_messages=gap_tracker.dropped_messages,
        received_count=gap_tracker.received_count,
        recv_duration_s=recv_duration_s,
        startup_connect_s=startup_connect_s,
    )
    runner.write_subscriber_report(report, result_file)


def main() -> None:
    """Parse CLI arguments and run this process's role (publisher or subscriber)."""
    parser = runner.build_common_arg_parser(
        "MQTT (paho-mqtt/amqtt) benchmark process",
        supports_handshake=True,
    )
    args = parser.parse_args()
    if args.role == "publisher":
        _run_publisher(
            host=args.host,
            port=args.port,
            size=args.size,
            count=args.count,
            ready_file=args.ready_file,
        )
    else:
        _run_subscriber(
            host=args.host,
            port=args.port,
            size=args.size,
            count=args.count,
            ready_file=args.ready_file,
            result_file=args.result_file,
        )


if __name__ == "__main__":
    main()
