"""Raw UDP benchmark: publisher and subscriber as two OS processes, no pub/sub library.

Run standalone as ``python -m benchmarks.udp_bench --role publisher|subscriber ...``
(see :func:`benchmarks.common.runner.build_common_arg_parser` for the shared CLI), or —
normally — via :func:`benchmarks.common.runner.run_subprocess_scenario` from
:mod:`benchmarks.run_bench`.

This is the other "floor" of the comparison, alongside :mod:`benchmarks.tcp_bench`: a
plain connectionless :class:`socket.SOCK_DGRAM` socket, nothing on top — no pub/sub
semantics, no discovery, no reliability. One datagram is one message: UDP already
preserves message boundaries, so unlike TCP (or h2r's own framing) there's no length
prefix to add. ROS 2/DDS is primarily UDP-based, so this is also the closest thing in
this suite to *its* floor.

UDP is connectionless, so — unlike :mod:`benchmarks.tcp_bench`'s ``accept()`` — there is
no connection-establishment step that could double as a synchronization point: the
subscriber's ``bind()`` on its receiving socket happens independently of anything the
publisher does, and any datagram the publisher sends before that ``bind()`` completes is
simply gone (the OS has nowhere to queue it). So, like :mod:`benchmarks.zmq_bench`,
:mod:`benchmarks.zenoh_bench`, and :mod:`benchmarks.ros2_bench`, this uses the ``seq=-1``
handshake-probe pattern (``supports_handshake=True``): the publisher probes until the
subscriber's result directory shows a ready file, which the subscriber writes the moment
it sees a live probe.

No artificial retransmission or reassembly is layered on top — UDP datagrams that the
kernel drops (buffer overrun, checksum failure, ...) are simply gone, and this benchmark
reports that as-is, same as every other middleware here. Note that a single UDP datagram
is capped by the protocol itself at 65,507 bytes of payload over IPv4 (the 16-bit UDP
length field minus the UDP/IP headers) regardless of MTU/fragmentation settings — larger
sizes fail with ``OSError`` (``EMSGSIZE``) on ``sendto()``, a genuine limitation of raw UDP
being measured, not a bug to work around here. ``--middleware udp``'s *default* scenario
set already excludes the suite's 1 MiB scenario for this reason (see
:data:`benchmarks.common.scenarios.UDP_DEFAULT_SCENARIOS`); explicit ``--sizes`` larger
than 65,507 bytes will still hit this and fail, deliberately.
"""

from __future__ import annotations

import socket
import time
from typing import TYPE_CHECKING

from benchmarks.common import payload
from benchmarks.common import runner
from benchmarks.common import stats

if TYPE_CHECKING:
    import pathlib

_PROBE_INTERVAL_S = 0.001
_RECV_TIMEOUT_S = 1.0
_OVERALL_TIMEOUT_S = 60.0
_RECV_BUFFER_SIZE = 2 * 1024 * 1024
"""Generous receive-buffer bound: real datagrams are always < 65,508 bytes (see module
docstring), so this is only ever a ceiling, never a truncation risk."""


def _run_publisher(
    *,
    host: str,
    port: int,
    size: int,
    count: int,
    ready_file: pathlib.Path,
) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    address = (host, port)
    try:
        while not ready_file.exists():
            sock.sendto(payload.build_payload(payload.HANDSHAKE_SEQ, size), address)
            time.sleep(_PROBE_INTERVAL_S)

        for seq in range(count):
            sock.sendto(payload.build_payload(seq, size), address)
    finally:
        sock.close()


def _run_subscriber(
    *,
    host: str,
    port: int,
    size: int,
    count: int,
    ready_file: pathlib.Path,
    result_file: pathlib.Path,
) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(_RECV_TIMEOUT_S)
    sock.bind((host, port))
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
                datagram, _addr = sock.recvfrom(_RECV_BUFFER_SIZE)
            except TimeoutError:
                continue
            seq, send_ns = payload.parse_payload(datagram)
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
        "Raw UDP benchmark process",
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
