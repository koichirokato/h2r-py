"""ZeroMQ (pyzmq) PUB/SUB benchmark: publisher and subscriber as two OS processes.

Run standalone as ``python -m benchmarks.zmq_bench --role publisher|subscriber ...``
(see :func:`benchmarks.common.runner.build_common_arg_parser` for the shared CLI), or —
normally — via :func:`benchmarks.common.runner.run_subprocess_scenario` from
:mod:`benchmarks.run_bench`.

ZeroMQ PUB/SUB has the same "slow joiner" behavior h2r has (a SUB socket that hasn't
finished connecting yet misses whatever the PUB sends in the meantime, with no replay),
so this uses the same ``seq=-1`` handshake-probe pattern as :mod:`benchmarks.h2r_bench`
and :mod:`benchmarks.ros2_bench`: the publisher probes until the subscriber's result
directory shows a ready file, which the subscriber writes the moment it sees a live probe.

No artificial send/receive queue limits are applied — HWM (high-water mark) is left at
pyzmq's default, so drops (if any) reflect ZeroMQ's own default backpressure behavior,
per this benchmark suite's scope decision to measure each middleware's stock behavior.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import zmq

from benchmarks.common import payload
from benchmarks.common import runner
from benchmarks.common import stats

if TYPE_CHECKING:
    import pathlib

_PROBE_INTERVAL_S = 0.001
_RECV_TIMEOUT_MS = 1000
_OVERALL_TIMEOUT_S = 60.0


def _run_publisher(
    *,
    host: str,
    port: int,
    size: int,
    count: int,
    ready_file: pathlib.Path,
) -> None:
    context = zmq.Context()
    socket = context.socket(zmq.PUB)
    socket.bind(f"tcp://{host}:{port}")
    try:
        while not ready_file.exists():
            socket.send(payload.build_payload(payload.HANDSHAKE_SEQ, size))
            time.sleep(_PROBE_INTERVAL_S)

        for seq in range(count):
            socket.send(payload.build_payload(seq, size))
    finally:
        socket.close(linger=1000)
        context.term()


def _run_subscriber(
    *,
    host: str,
    port: int,
    size: int,
    count: int,
    ready_file: pathlib.Path,
    result_file: pathlib.Path,
) -> None:
    context = zmq.Context()
    socket = context.socket(zmq.SUB)
    socket.setsockopt(zmq.SUBSCRIBE, b"")
    socket.setsockopt(zmq.RCVTIMEO, _RECV_TIMEOUT_MS)
    socket.connect(f"tcp://{host}:{port}")
    try:
        gap_tracker = stats.SequenceGapTracker()
        latencies_ns: list[int] = []
        recv_start_s = 0.0
        recv_end_s = 0.0
        startup_connect_s = 0.0

        handshake_start = time.perf_counter()
        deadline = time.perf_counter() + _OVERALL_TIMEOUT_S
        while time.perf_counter() < deadline:
            try:
                message = socket.recv()
            except zmq.Again:
                continue
            seq, send_ns = payload.parse_payload(message)
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
        socket.close(linger=0)
        context.term()

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
        "ZeroMQ PUB/SUB benchmark process",
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
