"""Zenoh (eclipse-zenoh) PUB/SUB benchmark: publisher and subscriber as two OS processes.

Run standalone as ``python -m benchmarks.zenoh_bench --role publisher|subscriber ...``
(see :func:`benchmarks.common.runner.build_common_arg_parser` for the shared CLI), or —
normally — via :func:`benchmarks.common.runner.run_subprocess_scenario` from
:mod:`benchmarks.run_bench`. Zenoh is included alongside ZeroMQ/gRPC/ROS 2 because it
shares h2r's core design stance — brokerless, no separate discovery daemon to run — and
has been gaining traction in robotics as a DDS alternative (e.g. ROS 2's ``rmw_zenoh``).

Each scenario's publisher and subscriber sessions are wired point-to-point over a fixed
TCP endpoint (publisher listens, subscriber connects) with multicast scouting disabled,
so pairing is explicit and doesn't depend on multicast/UDP being reachable in whatever
environment this runs in — the same explicit host/port addressing every other middleware
in this suite uses.

Zenoh PUB/SUB has the same "late joiner" behavior h2r has (a subscriber that hasn't
finished matching yet misses whatever was published in the meantime, with no replay), so
this uses the same ``seq=-1`` handshake-probe pattern as :mod:`benchmarks.h2r_bench` and
:mod:`benchmarks.zmq_bench`: the publisher probes until the subscriber's result directory
shows a ready file, which the subscriber writes the moment it sees a live probe.

The subscriber pulls with :meth:`zenoh.Subscriber.try_recv`, Zenoh's non-blocking receive
(there's no receive-with-timeout in its API), polling on the same short interval the
publisher uses for its handshake probes. No artificial queue limits are applied — the
subscriber's handler is Zenoh's own default, so drops (if any) reflect Zenoh's own default
backpressure behavior, per this benchmark suite's scope decision to measure each
middleware's stock behavior.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import zenoh

from benchmarks.common import payload
from benchmarks.common import runner
from benchmarks.common import stats

if TYPE_CHECKING:
    import pathlib

_KEY_EXPR = "bench/topic"
_PROBE_INTERVAL_S = 0.001
_OVERALL_TIMEOUT_S = 60.0


def _build_config(*, host: str, port: int, listen: bool) -> zenoh.Config:
    """Point-to-point Zenoh config over a fixed ``host:port`` TCP endpoint.

    *listen* selects which side of the pair this session is: the publisher listens (binds)
    and the subscriber connects, mirroring :mod:`benchmarks.zmq_bench`'s PUB-binds/
    SUB-connects layout. Multicast scouting is disabled on both sides — see module
    docstring.
    """
    config = zenoh.Config()
    endpoint = f'["tcp/{host}:{port}"]'
    if listen:
        config.insert_json5("listen/endpoints", endpoint)
    else:
        config.insert_json5("listen/endpoints", "[]")
        config.insert_json5("connect/endpoints", endpoint)
    config.insert_json5("scouting/multicast/enabled", "false")
    return config


def _run_publisher(
    *,
    host: str,
    port: int,
    size: int,
    count: int,
    ready_file: pathlib.Path,
) -> None:
    session = zenoh.open(_build_config(host=host, port=port, listen=True))
    publisher = session.declare_publisher(_KEY_EXPR)
    try:
        while not ready_file.exists():
            publisher.put(payload.build_payload(payload.HANDSHAKE_SEQ, size))
            time.sleep(_PROBE_INTERVAL_S)

        for seq in range(count):
            publisher.put(payload.build_payload(seq, size))
    finally:
        publisher.undeclare()
        session.close()


def _run_subscriber(
    *,
    host: str,
    port: int,
    size: int,
    count: int,
    ready_file: pathlib.Path,
    result_file: pathlib.Path,
) -> None:
    session = zenoh.open(_build_config(host=host, port=port, listen=False))
    subscriber = session.declare_subscriber(_KEY_EXPR)
    try:
        gap_tracker = stats.SequenceGapTracker()
        latencies_ns: list[int] = []
        recv_start_s = 0.0
        recv_end_s = 0.0
        startup_connect_s = 0.0

        handshake_start = time.perf_counter()
        deadline = time.perf_counter() + _OVERALL_TIMEOUT_S
        while time.perf_counter() < deadline:
            sample = subscriber.try_recv()
            if sample is None:
                time.sleep(_PROBE_INTERVAL_S)
                continue
            seq, send_ns = payload.parse_payload(sample.payload.to_bytes())
            if seq == payload.HANDSHAKE_SEQ:
                if not ready_file.exists():
                    startup_connect_s = time.perf_counter() - handshake_start
                    ready_file.write_text(str(startup_connect_s))
                continue
            recv_ns = time.perf_counter_ns()
            now_s = time.perf_counter()
            if recv_start_s == 0.0:
                recv_start_s = now_s
            recv_end_s = now_s
            gap_tracker.observe(seq)
            latencies_ns.append(recv_ns - send_ns)
            if gap_tracker.received_count >= count:
                break
    finally:
        subscriber.undeclare()
        session.close()

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
        "Zenoh PUB/SUB benchmark process",
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
