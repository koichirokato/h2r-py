"""The default (message size, message count) scenarios every ``bench-*`` target runs.

Stdlib-only. Sizes cover the range from small control/telemetry messages up to
image/point-cloud-sized frames; counts shrink as size grows to keep each scenario's
total wall-clock time roughly comparable.
"""

from __future__ import annotations

DEFAULT_SCENARIOS: list[tuple[int, int]] = [
    (64, 10_000),  # small control/telemetry message
    (1024, 10_000),  # 1 KiB, typical sensor sample (e.g. IMU)
    (65_536, 2_000),  # 64 KiB, e.g. a compressed image tile
    (1_048_576, 500),  # 1 MiB, e.g. a point cloud or camera frame
]
"""``(message_size_bytes, message_count)`` pairs; see :mod:`benchmarks.run_bench`."""

UDP_MAX_DATAGRAM_PAYLOAD_BYTES = 65_507
"""Largest payload a single IPv4 UDP datagram can carry (16-bit UDP length field minus the
UDP/IP headers); see :mod:`benchmarks.udp_bench`'s module docstring."""

UDP_DEFAULT_SCENARIOS: list[tuple[int, int]] = [
    (size, count) for size, count in DEFAULT_SCENARIOS if size <= UDP_MAX_DATAGRAM_PAYLOAD_BYTES
]
"""``DEFAULT_SCENARIOS`` with any scenario whose size exceeds a single UDP datagram's payload
cap removed — raw UDP (unlike every other middleware here) cannot send those at all, so
running them would just crash the publisher with ``OSError`` (``EMSGSIZE``) on the first
send. Used only for ``--middleware udp``'s *default* scenario set; explicit ``--sizes``/
``--counts`` are always run as given, oversized or not, since that's what the caller asked
for (see :mod:`benchmarks.run_bench`)."""
