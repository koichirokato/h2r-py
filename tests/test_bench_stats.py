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


def test_latency_stats_p95_known_interpolated_value() -> None:
    # 5 samples, ranks 0..4. p95 rank = 0.95 * 4 = 3.8, strictly between indices 3 and 4
    # (values 4 and 5), so this exercises the linear-interpolation branch (lower != upper)
    # against a value worked out by hand, not just self-consistency with sorted input.
    stats = LatencyStats.from_samples_ns([1, 2, 3, 4, 5])
    assert stats.p95_ns == pytest.approx(4.8)
    assert stats.p50_ns == pytest.approx(3.0)
    assert stats.p99_ns == pytest.approx(4.96)


def test_latency_stats_two_samples_p50_is_midpoint() -> None:
    # Smallest input where len > 1: p50 rank = 0.5 * (2 - 1) = 0.5, forcing interpolation
    # between the only two samples rather than hitting the len == 1 special case.
    stats = LatencyStats.from_samples_ns([10, 20])
    assert stats.p50_ns == pytest.approx(15.0)


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


def test_sequence_gap_tracker_ignores_any_negative_seq_not_just_handshake() -> None:
    # observe()'s guard is `seq < 0`, not `seq == HANDSHAKE_SEQ`: every negative value
    # must be excluded from both received_count and the drop calculation, not just -1.
    tracker = SequenceGapTracker()
    tracker.observe(-1)
    tracker.observe(-2)
    tracker.observe(-1000)
    tracker.observe(0)
    tracker.observe(1)
    assert tracker.received_count == 2
    assert tracker.dropped_messages == 0


def test_sequence_gap_tracker_first_real_message_is_not_seq_zero() -> None:
    # Every *_bench.py publisher numbers real messages from range(count) (i.e. always
    # starts at 0), so a run that never observes seq 0 means messages 0..4 were actually
    # dropped -- pin down that SequenceGapTracker always measures gaps from 0, not from
    # the lowest seq actually observed.
    tracker = SequenceGapTracker()
    for seq in range(5, 10):  # seq 0..4 never arrived
        tracker.observe(seq)
    assert tracker.received_count == 5
    assert tracker.dropped_messages == 5
