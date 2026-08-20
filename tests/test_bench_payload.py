"""Unit tests for benchmarks.common.payload (stdlib-only, part of `make check`)."""

from __future__ import annotations

import pytest

from benchmarks.common import payload


def test_build_payload_has_requested_size() -> None:
    assert len(payload.build_payload(seq=0, size=64)) == 64


def test_build_payload_minimum_size_is_header_size() -> None:
    assert len(payload.build_payload(seq=0, size=payload.HEADER_SIZE)) == payload.HEADER_SIZE


def test_build_payload_too_small_raises() -> None:
    with pytest.raises(ValueError, match="size must be"):
        payload.build_payload(seq=0, size=payload.HEADER_SIZE - 1)


def test_round_trip_preserves_seq() -> None:
    encoded = payload.build_payload(seq=42, size=128)
    seq, _send_ns = payload.parse_payload(encoded)
    assert seq == 42


def test_round_trip_preserves_handshake_seq() -> None:
    encoded = payload.build_payload(seq=payload.HANDSHAKE_SEQ, size=payload.HEADER_SIZE)
    seq, _send_ns = payload.parse_payload(encoded)
    assert seq == payload.HANDSHAKE_SEQ == -1


def test_round_trip_send_ns_is_positive() -> None:
    encoded = payload.build_payload(seq=0, size=payload.HEADER_SIZE)
    _seq, send_ns = payload.parse_payload(encoded)
    assert send_ns > 0


def test_padding_is_zero_filled() -> None:
    encoded = payload.build_payload(seq=0, size=32)
    assert encoded[payload.HEADER_SIZE :] == b"\x00" * (32 - payload.HEADER_SIZE)


def test_parse_payload_too_short_raises() -> None:
    with pytest.raises(ValueError, match="payload must be"):
        payload.parse_payload(b"\x00" * (payload.HEADER_SIZE - 1))


def test_parse_payload_ignores_trailing_padding() -> None:
    encoded = payload.build_payload(seq=7, size=256)
    seq, send_ns = payload.parse_payload(encoded)
    assert (seq, send_ns) == payload.parse_payload(encoded[: payload.HEADER_SIZE])
