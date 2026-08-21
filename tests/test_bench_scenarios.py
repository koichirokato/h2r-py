"""Unit tests for benchmarks.common.scenarios (stdlib-only, part of `make check`)."""

from __future__ import annotations

from benchmarks.common.scenarios import DEFAULT_SCENARIOS
from benchmarks.common.scenarios import UDP_DEFAULT_SCENARIOS
from benchmarks.common.scenarios import UDP_MAX_DATAGRAM_PAYLOAD_BYTES
from benchmarks.run_bench import _parse_args
from benchmarks.run_bench import _scenarios


def test_udp_max_datagram_payload_bytes_is_the_ipv4_derived_value() -> None:
    # 65,535 is the largest value the IPv4 total-length field can hold; a UDP datagram's
    # payload is that minus a 20-byte IPv4 header and an 8-byte UDP header. Pinning the
    # arithmetic (not just the literal) catches a plausible-looking wrong constant, e.g.
    # 65,535 itself (the *packet*, not payload, cap) or 65,500 (a rounded guess).
    assert UDP_MAX_DATAGRAM_PAYLOAD_BYTES == 65_535 - 20 - 8


def test_udp_default_scenarios_all_fit_in_one_datagram() -> None:
    assert all(size <= UDP_MAX_DATAGRAM_PAYLOAD_BYTES for size, _count in UDP_DEFAULT_SCENARIOS)


def test_udp_default_scenarios_is_strict_subset_of_default_scenarios() -> None:
    # The regression this guards against: DEFAULT_SCENARIOS includes a 1 MiB scenario
    # that raw UDP cannot send at all (see benchmarks/udp_bench.py's module docstring).
    assert len(UDP_DEFAULT_SCENARIOS) < len(DEFAULT_SCENARIOS)
    assert set(UDP_DEFAULT_SCENARIOS) <= set(DEFAULT_SCENARIOS)


def test_udp_default_scenarios_excludes_exactly_the_oversized_scenarios() -> None:
    # Pins *which* scenarios the filter keeps/drops, not just the aggregate counts above:
    # 64 B and 1 KiB fit in a datagram and must survive; 64 KiB (65,536 B) is one byte over
    # the 65,507-byte cap (the boundary this fix is about) and 1 MiB is far over, so both
    # must be dropped.
    assert (64, 10_000) in UDP_DEFAULT_SCENARIOS
    assert (1024, 10_000) in UDP_DEFAULT_SCENARIOS
    assert (65_536, 2_000) not in UDP_DEFAULT_SCENARIOS
    assert (1_048_576, 500) not in UDP_DEFAULT_SCENARIOS


def test_udp_default_scenarios_is_non_empty() -> None:
    assert UDP_DEFAULT_SCENARIOS


def test_scenarios_uses_udp_default_scenarios_for_udp_middleware_with_no_explicit_sizes() -> None:
    args = _parse_args(["--middleware", "udp"])
    assert _scenarios(args) == UDP_DEFAULT_SCENARIOS


def test_scenarios_uses_default_scenarios_for_other_middleware_with_no_explicit_sizes() -> None:
    args = _parse_args(["--middleware", "tcp"])
    assert _scenarios(args) == DEFAULT_SCENARIOS


def test_scenarios_honors_explicit_sizes_for_udp_even_if_oversized() -> None:
    # Explicit --sizes/--counts are the caller's choice; they are run as given, not
    # silently filtered, even past the UDP datagram cap.
    args = _parse_args(
        ["--middleware", "udp", "--sizes", "1048576", "--counts", "10"],
    )
    assert _scenarios(args) == [(1_048_576, 10)]
