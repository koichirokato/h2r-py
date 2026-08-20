"""Unit tests for benchmarks.common.stats (stdlib-only, part of `make check`)."""

from __future__ import annotations

import pytest

from benchmarks.common.stats import LatencyStats
from benchmarks.common.stats import SequenceGapTracker


def test_latency_stats_single_sample_all_equal() -> None:
    stats = LatencyStats.from_samples_ns([100])
    assert stats.p50_ns == stats.p95_ns == stats.p99_ns == stats.mean_ns == 100.0


def test_latency_stats_mean() -> None:
    stats = LatencyStats.from_samples_ns([100, 200, 300])
    assert stats.mean_ns == 200.0


def test_latency_stats_p50_of_uniform_range() -> None:
    # Linear-interpolation percentile of 1..101 (101 samples): p50 sits exactly on rank 50,
    # i.e. the 51st smallest value.
    stats = LatencyStats.from_samples_ns(list(range(1, 102)))
    assert stats.p50_ns == 51.0


def test_latency_stats_p99_of_uniform_range() -> None:
    stats = LatencyStats.from_samples_ns(list(range(1, 102)))
    assert stats.p99_ns == pytest.approx(100.0)


def test_latency_stats_unordered_input_is_sorted_first() -> None:
    ordered = LatencyStats.from_samples_ns([300, 100, 200])
    assert ordered == LatencyStats.from_samples_ns([100, 200, 300])


def test_latency_stats_empty_raises() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        LatencyStats.from_samples_ns([])


def test_sequence_gap_tracker_no_gaps() -> None:
    tracker = SequenceGapTracker()
    for seq in range(10):
        tracker.observe(seq)
    assert tracker.dropped_messages == 0
    assert tracker.received_count == 10


def test_sequence_gap_tracker_counts_missing_seqs() -> None:
    tracker = SequenceGapTracker()
    for seq in (0, 1, 3, 4):  # seq 2 never arrived
        tracker.observe(seq)
    assert tracker.dropped_messages == 1
    assert tracker.received_count == 4


def test_sequence_gap_tracker_ignores_handshake_probes() -> None:
    tracker = SequenceGapTracker()
    tracker.observe(-1)
    tracker.observe(-1)
    tracker.observe(0)
    tracker.observe(1)
    assert tracker.dropped_messages == 0
    assert tracker.received_count == 2


def test_sequence_gap_tracker_no_observations_has_no_drops() -> None:
    assert SequenceGapTracker().dropped_messages == 0


def test_sequence_gap_tracker_duplicate_observations_counted_once() -> None:
    tracker = SequenceGapTracker()
    tracker.observe(5)
    tracker.observe(5)
    assert tracker.received_count == 1
    assert tracker.dropped_messages == 5


def test_sequence_gap_tracker_out_of_order_observations() -> None:
    tracker = SequenceGapTracker()
    for seq in (4, 2, 0, 1, 3):
        tracker.observe(seq)
    assert tracker.dropped_messages == 0
    assert tracker.received_count == 5
