"""Unit tests for benchmarks.common.scenarios (stdlib-only, part of `make check`)."""

from __future__ import annotations

from benchmarks.common.scenarios import DEFAULT_SCENARIOS
from benchmarks.common.scenarios import UDP_DEFAULT_SCENARIOS
from benchmarks.common.scenarios import UDP_MAX_DATAGRAM_PAYLOAD_BYTES
from benchmarks.run_bench import _parse_args
from benchmarks.run_bench import _scenarios


def test_udp_default_scenarios_all_fit_in_one_datagram() -> None:
    assert all(size <= UDP_MAX_DATAGRAM_PAYLOAD_BYTES for size, _count in UDP_DEFAULT_SCENARIOS)


def test_udp_default_scenarios_is_strict_subset_of_default_scenarios() -> None:
    # The regression this guards against: DEFAULT_SCENARIOS includes a 1 MiB scenario
    # that raw UDP cannot send at all (see benchmarks/udp_bench.py's module docstring).
    assert len(UDP_DEFAULT_SCENARIOS) < len(DEFAULT_SCENARIOS)
    assert set(UDP_DEFAULT_SCENARIOS) <= set(DEFAULT_SCENARIOS)


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
