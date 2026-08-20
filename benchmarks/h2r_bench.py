"""h2r benchmark: publisher and subscriber in one process, one asyncio event loop.

Unlike :mod:`benchmarks.zmq_bench`, :mod:`benchmarks.grpc_bench`, and
:mod:`benchmarks.ros2_bench` (each launched as two OS processes via
:mod:`benchmarks.common.runner`), h2r-py has no separate broker or server binary to
launch — a "publisher" and a "subscriber" are just two Python objects
(:class:`h2r.publisher.Publisher`, :class:`h2r.subscriber.Subscriber`), so this benchmark
drives both directly in the same process and event loop, following the
``_running_publisher`` pattern in ``tests/test_publisher.py``.

This makes h2r's reported CPU/memory figures a *combined* single-process value, not
directly comparable to the other middlewares' two-process sums — see
:mod:`benchmarks.run_bench` and the generated report for that caveat. It also means the
publish loop below must periodically ``await`` (something the two-process benchmarks get
for free from OS scheduling) so the event loop gets a chance to actually drain and send
queued frames — see :func:`_publish_real_messages`.

h2r only broadcasts to subscribers already connected when `publish()` is called (no
replay), so — like :mod:`benchmarks.zmq_bench` and :mod:`benchmarks.ros2_bench`, and
unlike :mod:`benchmarks.grpc_bench`, whose RPC dispatch is itself the synchronization
point — the publisher sends ``seq=-1`` handshake probes until the subscriber has actually
received one, before switching to the real, measured message stream.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import time
from typing import TYPE_CHECKING

from benchmarks.common import payload
from benchmarks.common import runner
from benchmarks.common import stats
from benchmarks.common.resource import ResourceSampler
from h2r import publisher as publisher_module
from h2r import subscriber as subscriber_module

if TYPE_CHECKING:
    from benchmarks.common.result import BenchmarkResult

_TOPIC = "/bench/topic"
_MESSAGE_TYPE = "benchmarks/Payload"
_PROBE_INTERVAL_S = 0.001
_DEFAULT_TIMEOUT_S = 60.0


async def _wait_for_listening(server: publisher_module.Publisher) -> None:
    """Spin until *server*'s ephemeral port has been assigned by `asyncio.start_server`."""
    while not server.port:
        await asyncio.sleep(0)


async def _probe_until_cancelled(server: publisher_module.Publisher, size: int) -> None:
    """Publish handshake probes to *server* until this task is cancelled."""
    while True:
        server.publish(_TOPIC, payload.build_payload(payload.HANDSHAKE_SEQ, size))
        await asyncio.sleep(_PROBE_INTERVAL_S)


async def _await_first_probe(subscriber: subscriber_module.Subscriber) -> None:
    """Consume frames from *subscriber* until a handshake probe (``seq == -1``) arrives."""
    async for frame_payload in subscriber:
        seq, _send_ns = payload.parse_payload(frame_payload)
        if seq == payload.HANDSHAKE_SEQ:
            return


async def _publish_real_messages(server: publisher_module.Publisher, size: int, count: int) -> None:
    """Publish *count* real (non-probe) messages of *size* bytes, fastest first.

    `Publisher.publish` is synchronous and only enqueues onto each subscriber's bounded
    queue (see `h2r.publisher`'s module docstring); nothing actually reaches the socket
    until the connection's drain task runs, which — on this single event loop shared with
    the subscriber side — only happens when this loop yields. `await asyncio.sleep(0)`
    does the minimum possible yield (one `call_soon` round trip) each iteration, which is
    this benchmark's single-process stand-in for the concurrency two real OS processes get
    from the scheduler for free.
    """
    for seq in range(count):
        server.publish(_TOPIC, payload.build_payload(seq, size))
        await asyncio.sleep(0)


async def _consume_real_messages(
    subscriber: subscriber_module.Subscriber,
    count: int,
    gap_tracker: stats.SequenceGapTracker,
    latencies_ns: list[int],
) -> tuple[float, float]:
    """Collect up to *count* real messages, returning ``(recv_start_s, recv_end_s)``.

    Both are `time.perf_counter()` timestamps of the first and last real message
    received; ``(0.0, 0.0)`` if none arrived at all.
    """
    recv_start_s = 0.0
    recv_end_s = 0.0
    async for frame_payload in subscriber:
        seq, send_ns = payload.parse_payload(frame_payload)
        if seq < 0:
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
    return recv_start_s, recv_end_s


async def run_scenario(
    size: int,
    count: int,
    *,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
    notes: str = "",
) -> BenchmarkResult:
    """Run one (size, count) scenario against h2r and return its `BenchmarkResult`.

    See this module's docstring for why h2r is measured single-process (publisher and
    subscriber sharing one event loop) while the other middlewares are measured as two
    OS processes.
    """
    server = publisher_module.Publisher("127.0.0.1", 0)
    server.advertise(_TOPIC, _MESSAGE_TYPE)
    serve_task = asyncio.create_task(server.serve())
    await _wait_for_listening(server)

    sampler = ResourceSampler(os.getpid())
    sampler.start()

    subscriber = subscriber_module.Subscriber(f"127.0.0.1:{server.port}", _TOPIC)
    probe_task = asyncio.create_task(_probe_until_cancelled(server, size))

    handshake_start = time.perf_counter()
    await asyncio.wait_for(_await_first_probe(subscriber), timeout=timeout_s)
    startup_connect_s = time.perf_counter() - handshake_start

    probe_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await probe_task

    gap_tracker = stats.SequenceGapTracker()
    latencies_ns: list[int] = []
    try:
        _recv_start_s, _recv_end_s = (0.0, 0.0)
        results = await asyncio.wait_for(
            asyncio.gather(
                _publish_real_messages(server, size, count),
                _consume_real_messages(subscriber, count, gap_tracker, latencies_ns),
            ),
            timeout=timeout_s,
        )
        _recv_start_s, _recv_end_s = results[1]
    except TimeoutError:
        pass

    usage = sampler.stop()
    await subscriber.aclose()
    await server.aclose()
    await serve_task

    recv_duration_s = max(0.0, _recv_end_s - _recv_start_s)
    report = runner.build_subscriber_report(
        message_size_bytes=size,
        latencies_ns=latencies_ns,
        dropped_messages=gap_tracker.dropped_messages,
        received_count=gap_tracker.received_count,
        recv_duration_s=recv_duration_s,
        startup_connect_s=startup_connect_s,
    )
    return runner.build_benchmark_result(
        middleware="h2r",
        message_size_bytes=size,
        message_count=count,
        report=report,
        usage=usage,
        notes=notes,
    )
