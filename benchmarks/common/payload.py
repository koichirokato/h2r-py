"""Fixed-layout benchmark payload: a sequence number and send timestamp, then padding.

Every ``*_bench.py`` publisher builds messages with :func:`build_payload` and every
subscriber decodes them with :func:`parse_payload`, so latency (send-to-receive) and
sequence gaps (dropped messages) can be measured identically across middlewares.
Stdlib-only, and covered by ``tests/test_bench_payload.py``.

Wire layout (big-endian)::

    [8 bytes: seq, signed]  [8 bytes: send_ns, unsigned]  [padding, zero-filled]

``seq == -1`` marks a handshake probe frame (see :mod:`benchmarks.common.runner`): it
carries no meaningful timing data and benchmark scripts must not count it as a real
message.
"""

from __future__ import annotations

import struct
import time

_HEADER_STRUCT = struct.Struct("!qQ")
HEADER_SIZE = _HEADER_STRUCT.size
"""Number of header bytes (seq + send_ns) every payload reserves ahead of its padding."""

HANDSHAKE_SEQ = -1
"""Sequence number reserved for handshake probe frames; never a real message index."""


def build_payload(seq: int, size: int) -> bytes:
    """Build a *size*-byte payload stamped with *seq* and the current send time.

    Raises :class:`ValueError` if *size* is too small to hold the header.
    """
    if size < HEADER_SIZE:
        message = f"size must be >= {HEADER_SIZE} (header size), got {size}"
        raise ValueError(message)
    header = _HEADER_STRUCT.pack(seq, time.perf_counter_ns())
    return header + bytes(size - HEADER_SIZE)


def parse_payload(payload: bytes) -> tuple[int, int]:
    """Decode *payload*'s ``(seq, send_ns)`` header, ignoring any padding.

    Raises :class:`ValueError` if *payload* is shorter than the header.
    """
    if len(payload) < HEADER_SIZE:
        message = f"payload must be >= {HEADER_SIZE} bytes (header size), got {len(payload)}"
        raise ValueError(message)
    seq, send_ns = _HEADER_STRUCT.unpack_from(payload, 0)
    return seq, send_ns
