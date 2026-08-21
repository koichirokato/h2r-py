"""WebSocket (``websockets``) benchmark: publisher and subscriber as two OS processes.

Run standalone as ``python -m benchmarks.websocket_bench --role publisher|subscriber ...``
(see :func:`benchmarks.common.runner.build_common_arg_parser` for the shared CLI), or —
normally — via :func:`benchmarks.common.runner.run_subprocess_scenario` from
:mod:`benchmarks.run_bench`. Uses the asyncio-native ``websockets`` package (its default,
non-legacy implementation as of 15.x: :func:`websockets.serve`/:func:`websockets.connect`).

WebSocket runs on top of a single, long-lived TCP connection established by an HTTP/1.1
``Upgrade`` handshake — h2r and gRPC instead run each publish over HTTP/2 streams
multiplexed on one connection, and this suite's :mod:`benchmarks.tcp_bench` skips the
HTTP layer entirely. All three, though, share the same trait this benchmark relies on:
structurally like :mod:`benchmarks.grpc_bench` and :mod:`benchmarks.tcp_bench`, not
:mod:`benchmarks.zmq_bench`, the publisher's connection handler only starts running once
a subscriber's Upgrade handshake completes — that dispatch *is* the synchronization
point, so there is no slow-joiner race and no ``seq=-1`` handshake-probe phase
(``supports_handshake=False``).

The subscriber's initial :func:`websockets.connect` call can still race the publisher's
:func:`websockets.serve` — the two processes are launched at roughly the same time (see
:func:`benchmarks.common.runner.run_subprocess_scenario`) — so, like
:mod:`benchmarks.tcp_bench`'s ``connect()`` retry, this retries past
``ConnectionRefusedError`` until the server is listening. That is an ordinary TCP-level
connection race, not the payload-level handshake-probe pattern.

No artificial message-queue limits are applied — ``max_queue`` and ``max_size`` are left
at the ``websockets`` package's own defaults, so drops or backpressure (if any) reflect
its stock behavior, per this benchmark suite's scope decision.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import TYPE_CHECKING

import websockets
import websockets.exceptions

from benchmarks.common import payload
from benchmarks.common import runner
from benchmarks.common import stats

if TYPE_CHECKING:
    import pathlib

    from websockets.asyncio.server import ServerConnection

_CONNECT_RETRY_INTERVAL_S = 0.01
_OVERALL_TIMEOUT_S = 60.0


async def _serve(*, host: str, port: int, size: int, count: int) -> None:
    done = asyncio.Event()

    async def _handler(connection: ServerConnection) -> None:
        try:
            for seq in range(count):
                await connection.send(payload.build_payload(seq, size))
        finally:
            done.set()

    async with websockets.serve(_handler, host, port):
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(done.wait(), timeout=_OVERALL_TIMEOUT_S)


def _run_publisher(*, host: str, port: int, size: int, count: int) -> None:
    asyncio.run(_serve(host=host, port=port, size=size, count=count))


async def _connect_with_retry(uri: str, *, deadline: float) -> websockets.ClientConnection:
    """Connect to *uri*, retrying past ``ConnectionRefusedError`` until *deadline*.

    See module docstring: an ordinary TCP-level connection race between the publisher and
    subscriber processes starting at roughly the same time, not a handshake-probe.
    """
    while True:
        try:
            return await websockets.connect(uri)
        except OSError:
            if time.perf_counter() > deadline:
                raise
            await asyncio.sleep(_CONNECT_RETRY_INTERVAL_S)


async def _subscribe(
    *,
    host: str,
    port: int,
    size: int,
    count: int,
    result_file: pathlib.Path,
) -> None:
    gap_tracker = stats.SequenceGapTracker()
    latencies_ns: list[int] = []
    recv_start_s = 0.0
    recv_end_s = 0.0
    startup_connect_s = 0.0

    connect_start = time.perf_counter()
    connection = await _connect_with_retry(
        f"ws://{host}:{port}",
        deadline=connect_start + _OVERALL_TIMEOUT_S,
    )
    try:
        async with asyncio.timeout(_OVERALL_TIMEOUT_S):
            async for message in connection:
                seq, send_ns = payload.parse_payload(message)
                recv_ns = time.perf_counter_ns()
                now_s = time.perf_counter()
                if recv_start_s == 0.0:
                    startup_connect_s = now_s - connect_start
                    recv_start_s = now_s
                recv_end_s = now_s
                gap_tracker.observe(seq)
                latencies_ns.append(recv_ns - send_ns)
                if gap_tracker.received_count >= count:
                    break
    except (TimeoutError, websockets.exceptions.ConnectionClosed):
        pass
    finally:
        await connection.close()

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


def _run_subscriber(
    *,
    host: str,
    port: int,
    size: int,
    count: int,
    result_file: pathlib.Path,
) -> None:
    asyncio.run(_subscribe(host=host, port=port, size=size, count=count, result_file=result_file))


def main() -> None:
    """Parse CLI arguments and run this process's role (publisher or subscriber)."""
    parser = runner.build_common_arg_parser(
        "WebSocket benchmark process",
        supports_handshake=False,
    )
    args = parser.parse_args()
    if args.role == "publisher":
        _run_publisher(host=args.host, port=args.port, size=args.size, count=args.count)
    else:
        _run_subscriber(
            host=args.host,
            port=args.port,
            size=args.size,
            count=args.count,
            result_file=args.result_file,
        )


if __name__ == "__main__":
    main()
