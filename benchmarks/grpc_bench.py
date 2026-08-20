"""gRPC (grpcio) server-streaming benchmark: publisher (server), subscriber (client) processes.

Run standalone as ``python -m benchmarks.grpc_bench --role publisher|subscriber ...``
(see :func:`benchmarks.common.runner.build_common_arg_parser` for the shared CLI), or —
normally — via :func:`benchmarks.common.runner.run_subprocess_scenario` from
:mod:`benchmarks.run_bench`.

Structurally different from :mod:`benchmarks.h2r_bench`, :mod:`benchmarks.zmq_bench`, and
:mod:`benchmarks.ros2_bench`: those are broadcast with no replay, so a subscriber that
connects even slightly late misses messages — hence their ``seq=-1`` handshake-probe
dance. gRPC's server-streaming RPC dispatch *is* the synchronization point: the server
only starts producing frames once a client's ``Subscribe`` call arrives, so there is
structurally no slow-joiner race here and no probe phase is needed. This is a genuine
difference in what's being measured, not just an implementation detail — see the note in
the generated report.

``benchmarks/generated/`` (gitignored) holds the ``bench_pb2``/``bench_pb2_grpc`` stubs
generated from ``benchmarks/grpc_proto/bench.proto`` on first import, so there's no
separate codegen step to run or forget to re-run after editing the ``.proto`` file.
"""

from __future__ import annotations

import pathlib
import sys
import threading
import time
from concurrent import futures

import grpc

from benchmarks.common import payload
from benchmarks.common import runner
from benchmarks.common import stats

_PROTO_DIR = pathlib.Path(__file__).resolve().parent / "grpc_proto"
_GENERATED_DIR = pathlib.Path(__file__).resolve().parent / "generated"
_OVERALL_TIMEOUT_S = 60.0


def _ensure_generated_stubs() -> None:
    """Generate ``bench_pb2``/``bench_pb2_grpc`` into `_GENERATED_DIR` if not already there."""
    pb2_file = _GENERATED_DIR / "bench_pb2.py"
    grpc_file = _GENERATED_DIR / "bench_pb2_grpc.py"
    if pb2_file.exists() and grpc_file.exists():
        return
    _GENERATED_DIR.mkdir(exist_ok=True)
    from grpc_tools import protoc  # noqa: PLC0415 -- optional, only needed to (re)generate stubs

    args = [
        "protoc",
        f"-I{_PROTO_DIR}",
        f"--python_out={_GENERATED_DIR}",
        f"--grpc_python_out={_GENERATED_DIR}",
        str(_PROTO_DIR / "bench.proto"),
    ]
    if protoc.main(args) != 0:
        message = "failed to generate gRPC stubs from bench.proto"
        raise RuntimeError(message)


_ensure_generated_stubs()
if str(_GENERATED_DIR) not in sys.path:
    sys.path.insert(0, str(_GENERATED_DIR))

import bench_pb2
import bench_pb2_grpc


class _BenchServicer(bench_pb2_grpc.BenchServiceServicer):
    """Streams `request.message_count` framed payloads, then signals `done_event`."""

    def __init__(self, done_event: threading.Event) -> None:
        """Wrap *done_event*, set once a `Subscribe` call has streamed its last frame."""
        self._done_event = done_event

    def Subscribe(  # noqa: N802 -- gRPC-generated servicer method name, not ours to choose
        self,
        request: bench_pb2.SubscribeRequest,
        context: grpc.ServicerContext,
    ) -> object:
        """Yield `request.message_count` frames of `request.message_size` bytes each."""
        del context
        try:
            for seq in range(request.message_count):
                yield bench_pb2.Frame(payload=payload.build_payload(seq, request.message_size))
        finally:
            self._done_event.set()


def _run_publisher(*, host: str, port: int) -> None:
    done_event = threading.Event()
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=4))
    bench_pb2_grpc.add_BenchServiceServicer_to_server(_BenchServicer(done_event), server)
    server.add_insecure_port(f"{host}:{port}")
    server.start()
    done_event.wait(timeout=_OVERALL_TIMEOUT_S)
    server.stop(grace=1.0).wait()


def _run_subscriber(
    *,
    host: str,
    port: int,
    size: int,
    count: int,
    result_file: pathlib.Path,
) -> None:
    channel = grpc.insecure_channel(f"{host}:{port}")
    stub = bench_pb2_grpc.BenchServiceStub(channel)
    request = bench_pb2.SubscribeRequest(message_count=count, message_size=size)

    gap_tracker = stats.SequenceGapTracker()
    latencies_ns: list[int] = []
    recv_start_s = 0.0
    recv_end_s = 0.0
    startup_connect_s = 0.0

    connect_start = time.perf_counter()
    try:
        for frame in stub.Subscribe(request, timeout=_OVERALL_TIMEOUT_S):
            seq, send_ns = payload.parse_payload(frame.payload)
            recv_ns = time.perf_counter_ns()
            now_s = time.perf_counter()
            if recv_start_s == 0.0:
                startup_connect_s = now_s - connect_start
                recv_start_s = now_s
            recv_end_s = now_s
            gap_tracker.observe(seq)
            latencies_ns.append(recv_ns - send_ns)
    except grpc.RpcError:
        # Deadline exceeded or the server went away mid-stream: report whatever arrived.
        pass
    finally:
        channel.close()

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
        "gRPC server-streaming benchmark process",
        supports_handshake=False,
    )
    args = parser.parse_args()
    if args.role == "publisher":
        _run_publisher(host=args.host, port=args.port)
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
