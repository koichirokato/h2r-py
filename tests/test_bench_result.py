"""Unit tests for benchmarks.common.result (stdlib-only, part of `make check`)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from benchmarks.common.result import BenchmarkResult
from benchmarks.common.result import load_results
from benchmarks.common.result import save_results

if TYPE_CHECKING:
    import pathlib

_SAMPLE = BenchmarkResult(
    middleware="h2r",
    message_size_bytes=1024,
    message_count=10_000,
    throughput_msgs_per_s=50_000.0,
    throughput_mb_per_s=48.8,
    latency_p50_ms=0.2,
    latency_p95_ms=0.5,
    latency_p99_ms=0.9,
    latency_mean_ms=0.25,
    dropped_messages=3,
    startup_connect_s=0.01,
    cpu_percent_mean=12.5,
    cpu_percent_peak=40.0,
    rss_bytes_mean=1_000_000.0,
    rss_bytes_peak=1_500_000.0,
    timestamp="2026-08-20T00:00:00+00:00",
    notes="single-process measurement",
)


def test_to_dict_round_trips_through_from_dict() -> None:
    assert BenchmarkResult.from_dict(_SAMPLE.to_dict()) == _SAMPLE


def test_save_and_load_round_trip(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "results.json"
    save_results([_SAMPLE], path)
    assert load_results(path) == [_SAMPLE]


def test_save_results_writes_json_array(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "results.json"
    save_results([_SAMPLE, _SAMPLE], path)
    assert len(load_results(path)) == 2


def test_notes_defaults_to_empty_string() -> None:
    result = BenchmarkResult.from_dict(
        {k: v for k, v in _SAMPLE.to_dict().items() if k != "notes"},
    )
    assert result.notes == ""


def test_from_dict_missing_required_field_raises() -> None:
    # Unlike `notes`, every other field has no default: a result file from a version that
    # dropped a field (not just added one) must fail loudly rather than silently
    # constructing a partially-populated result.
    data = {k: v for k, v in _SAMPLE.to_dict().items() if k != "middleware"}
    with pytest.raises(TypeError):
        BenchmarkResult.from_dict(data)


def test_from_dict_unexpected_field_raises() -> None:
    # A result file from a newer schema version with an extra field must fail loudly
    # rather than silently being accepted and then dropped.
    data = {**_SAMPLE.to_dict(), "extra_unexpected_field": "surprise"}
    with pytest.raises(TypeError):
        BenchmarkResult.from_dict(data)


def test_save_results_empty_list_round_trips(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "results.json"
    save_results([], path)
    assert load_results(path) == []
