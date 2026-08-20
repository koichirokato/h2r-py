"""ROS 2 (rclpy) benchmark: publisher and subscriber as two OS processes.

Run standalone as ``python -m benchmarks.ros2_bench --role publisher|subscriber ...``
(see :func:`benchmarks.common.runner.build_common_arg_parser` for the shared CLI), or —
normally — via :func:`benchmarks.common.runner.run_subprocess_scenario` from
:mod:`benchmarks.run_bench`. Only runs inside the ``bench-ros2`` Docker image (see
``docker/Dockerfile.ros2``), which has ``rclpy`` on its path; the ``bench`` image does not.

Uses BEST_EFFORT reliability with a shallow (depth=64) queue — matching h2r's own
subscriber queue bound (see ``h2r.publisher``'s ``_SUBSCRIBER_QUEUE_MAXSIZE``) — as the
primary comparable profile, per this benchmark suite's scope decision. ROS 2's default
RELIABLE QoS is intentionally out of scope here: it isn't a fair drop-behavior comparison
against h2r/ZeroMQ/gRPC, none of which retransmit.

DDS topics have the same "late joiner" behavior h2r has (a subscriber that hasn't
finished matching yet misses whatever was published in the meantime, with BEST_EFFORT
QoS giving no replay), so this uses the same ``seq=-1`` handshake-probe pattern as
:mod:`benchmarks.h2r_bench` and :mod:`benchmarks.zmq_bench`.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import rclpy
from rclpy.qos import HistoryPolicy
from rclpy.qos import QoSProfile
from rclpy.qos import ReliabilityPolicy
from std_msgs.msg import UInt8MultiArray

from benchmarks.common import payload
from benchmarks.common import runner
from benchmarks.common import stats

if TYPE_CHECKING:
    import pathlib

_TOPIC = "/bench/topic"
_PROBE_INTERVAL_S = 0.001
_OVERALL_TIMEOUT_S = 60.0
_SPIN_TIMEOUT_S = 0.1

_QOS_DEPTH = 64
"""Matches h2r's `_SUBSCRIBER_QUEUE_MAXSIZE` (see `h2r.publisher`); see module docstring."""


def _build_qos() -> QoSProfile:
    """BEST_EFFORT reliability with a `_QOS_DEPTH`-deep KEEP_LAST history."""
    return QoSProfile(
        reliability=ReliabilityPolicy.BEST_EFFORT,
        history=HistoryPolicy.KEEP_LAST,
        depth=_QOS_DEPTH,
    )


def _run_publisher(*, size: int, count: int, ready_file: pathlib.Path) -> None:
    rclpy.init()
    node = rclpy.create_node("h2r_bench_publisher")
    publisher = node.create_publisher(UInt8MultiArray, _TOPIC, _build_qos())
    try:
        while not ready_file.exists():
            probe = UInt8MultiArray()
            probe.data = payload.build_payload(payload.HANDSHAKE_SEQ, size)
            publisher.publish(probe)
            rclpy.spin_once(node, timeout_sec=_PROBE_INTERVAL_S)

        for seq in range(count):
            message = UInt8MultiArray()
            message.data = payload.build_payload(seq, size)
            publisher.publish(message)
    finally:
        node.destroy_node()
        rclpy.shutdown()


def _run_subscriber(
    *,
    size: int,
    count: int,
    ready_file: pathlib.Path,
    result_file: pathlib.Path,
) -> None:
    rclpy.init()
    node = rclpy.create_node("h2r_bench_subscriber")

    gap_tracker = stats.SequenceGapTracker()
    latencies_ns: list[int] = []
    recv_start_s = 0.0
    recv_end_s = 0.0
    startup_connect_s = 0.0
    handshake_start = time.perf_counter()

    def _on_message(message: UInt8MultiArray) -> None:
        nonlocal recv_start_s, recv_end_s, startup_connect_s
        seq, send_ns = payload.parse_payload(bytes(message.data))
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

    node.create_subscription(UInt8MultiArray, _TOPIC, _on_message, _build_qos())

    try:
        deadline = time.perf_counter() + _OVERALL_TIMEOUT_S
        while gap_tracker.received_count < count and time.perf_counter() < deadline:
            rclpy.spin_once(node, timeout_sec=_SPIN_TIMEOUT_S)
    finally:
        node.destroy_node()
        rclpy.shutdown()

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
        "ROS 2 (rclpy) benchmark process",
        supports_handshake=True,
    )
    args = parser.parse_args()
    if args.role == "publisher":
        _run_publisher(size=args.size, count=args.count, ready_file=args.ready_file)
    else:
        _run_subscriber(
            size=args.size,
            count=args.count,
            ready_file=args.ready_file,
            result_file=args.result_file,
        )


if __name__ == "__main__":
    main()
