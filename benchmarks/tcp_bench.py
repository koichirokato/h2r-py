"""Raw TCP benchmark: publisher and subscriber as two OS processes, no pub/sub library.

Run standalone as ``python -m benchmarks.tcp_bench --role publisher|subscriber ...``
(see :func:`benchmarks.common.runner.build_common_arg_parser` for the shared CLI), or —
normally — via :func:`benchmarks.common.runner.run_subprocess_scenario` from
:mod:`benchmarks.run_bench`.

This is the "floor" of the comparison: a plain :mod:`socket` stream with nothing on top —
no pub/sub semantics, no discovery, no broker, just a length-prefixed byte stream. It
exists to measure the overhead every other middleware in this suite adds on top of raw
TCP, the same transport h2r and gRPC (HTTP/2) and WebSocket (HTTP/1.1 Upgrade) all
ultimately run over.

Message framing is a 4-byte big-endian length prefix followed by that many payload bytes
— conceptually the same job :mod:`h2r.frame` does for h2r's own wire protocol, but
reimplemented from scratch here with :mod:`struct` rather than imported, so this
benchmark stays a genuinely independent, dependency-free baseline rather than exercising
h2r's own framing code.

Structurally like :mod:`benchmarks.grpc_bench`, not :mod:`benchmarks.zmq_bench`: the
publisher ``bind()``s, ``listen()``s, and blocks on ``accept()``, which — like gRPC's RPC
dispatch — *is* the synchronization point. A subscriber that connects before the listener
is up simply gets ``ConnectionRefusedError`` and retries the ``connect()`` call (ordinary
TCP client retry, not a payload-level probe); once ``accept()`` returns, every byte the
publisher writes is guaranteed delivered in order, so there is no slow-joiner race and no
``seq=-1`` handshake-probe phase (``supports_handshake=False``). TCP is a reliable,
ordered stream, so dropped messages should be zero here barring a crash or timeout — any
non-zero drop count is a benchmark bug, not TCP behavior, unlike the UDP/ZeroMQ/Zenoh/
ROS 2/MQTT benchmarks where some drops are expected.
"""

from __future__ import annotations

import socket
import struct
import time
from typing import TYPE_CHECKING

from benchmarks.common import payload
from benchmarks.common import runner
from benchmarks.common import stats

if TYPE_CHECKING:
    import pathlib

_LENGTH_STRUCT = struct.Struct("!I")
_LENGTH_PREFIX_SIZE = _LENGTH_STRUCT.size
_CONNECT_RETRY_INTERVAL_S = 0.01
_OVERALL_TIMEOUT_S = 60.0
_RECV_CHUNK_SIZE = 65536


def _send_framed(sock: socket.socket, data: bytes) -> None:
    """Write *data* prefixed with its 4-byte big-endian length onto *sock*."""
    sock.sendall(_LENGTH_STRUCT.pack(len(data)) + data)


def _recv_exact(sock: socket.socket, size: int) -> bytes | None:
    """Read exactly *size* bytes from *sock*, or ``None`` if it closes early."""
    chunks: list[bytes] = []
    remaining = size
    while remaining > 0:
        chunk = sock.recv(min(remaining, _RECV_CHUNK_SIZE))
        if not chunk:
            return None
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _recv_framed(sock: socket.socket) -> bytes | None:
    """Read one length-prefixed frame from *sock*, or ``None`` if it closes early."""
    header = _recv_exact(sock, _LENGTH_PREFIX_SIZE)
    if header is None:
        return None
    (length,) = _LENGTH_STRUCT.unpack(header)
    return _recv_exact(sock, length)


def _run_publisher(*, host: str, port: int, size: int, count: int) -> None:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.settimeout(_OVERALL_TIMEOUT_S)
    listener.bind((host, port))
    listener.listen(1)
    try:
        try:
            conn, _addr = listener.accept()
        except TimeoutError:
            return
        conn.settimeout(_OVERALL_TIMEOUT_S)
        try:
            for seq in range(count):
                _send_framed(conn, payload.build_payload(seq, size))
        finally:
            conn.close()
    finally:
        listener.close()


def _connect_with_retry(host: str, port: int, *, deadline: float) -> socket.socket:
    """Connect to ``host:port``, retrying past ``ConnectionRefusedError`` until *deadline*.

    The publisher and subscriber processes are launched at roughly the same time (see
    :func:`benchmarks.common.runner.run_subprocess_scenario`), so the listener may not be
    up yet when this first tries to connect — an ordinary TCP client concern, not the
    handshake-probe pattern other benchmarks in this suite use (see module docstring).
    """
    while True:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.connect((host, port))
        except OSError:
            sock.close()
            if time.perf_counter() > deadline:
                raise
            time.sleep(_CONNECT_RETRY_INTERVAL_S)
        else:
            return sock


def _run_subscriber(
    *,
    host: str,
    port: int,
    size: int,
    count: int,
    result_file: pathlib.Path,
) -> None:
    connect_start = time.perf_counter()
    sock = _connect_with_retry(host, port, deadline=connect_start + _OVERALL_TIMEOUT_S)
    sock.settimeout(_OVERALL_TIMEOUT_S)

    gap_tracker = stats.SequenceGapTracker()
    latencies_ns: list[int] = []
    recv_start_s = 0.0
    recv_end_s = 0.0
    startup_connect_s = 0.0
    try:
        while gap_tracker.received_count < count:
            try:
                frame = _recv_framed(sock)
            except TimeoutError:
                break
            if frame is None:
                break
            seq, send_ns = payload.parse_payload(frame)
            recv_ns = time.perf_counter_ns()
            now_s = time.perf_counter()
            if recv_start_s == 0.0:
                startup_connect_s = now_s - connect_start
                recv_start_s = now_s
            recv_end_s = now_s
            gap_tracker.observe(seq)
            latencies_ns.append(recv_ns - send_ns)
    finally:
        sock.close()

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
        "Raw TCP benchmark process",
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
