"""Latency percentiles and dropped-message accounting shared by every benchmark script.

Stdlib-only, and covered by ``tests/test_bench_stats.py``.
"""

from __future__ import annotations

import dataclasses
import math
import statistics
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence


def _percentile_ns(sorted_samples_ns: Sequence[int], percentile: float) -> float:
    """Linearly-interpolated *percentile* (0-100) of *sorted_samples_ns*.

    Pure: the result depends only on the arguments. *sorted_samples_ns* must already be
    sorted ascending and non-empty; :class:`LatencyStats.from_samples_ns` is responsible
    for both.
    """
    if len(sorted_samples_ns) == 1:
        return float(sorted_samples_ns[0])
    rank = (percentile / 100) * (len(sorted_samples_ns) - 1)
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return float(sorted_samples_ns[int(rank)])
    lower_weight = upper - rank
    upper_weight = rank - lower
    return sorted_samples_ns[lower] * lower_weight + sorted_samples_ns[upper] * upper_weight


@dataclasses.dataclass(frozen=True)
class LatencyStats:
    """Latency distribution summary, all fields in nanoseconds."""

    p50_ns: float
    p95_ns: float
    p99_ns: float
    mean_ns: float

    @classmethod
    def from_samples_ns(cls, samples_ns: Sequence[int]) -> LatencyStats:
        """Compute p50/p95/p99/mean from *samples_ns* (one-way send-to-receive latencies).

        Raises :class:`ValueError` if *samples_ns* is empty.
        """
        if not samples_ns:
            message = "samples_ns must not be empty"
            raise ValueError(message)
        ordered = sorted(samples_ns)
        return cls(
            p50_ns=_percentile_ns(ordered, 50),
            p95_ns=_percentile_ns(ordered, 95),
            p99_ns=_percentile_ns(ordered, 99),
            mean_ns=statistics.fmean(ordered),
        )


class SequenceGapTracker:
    """Derives a dropped-message count from the sequence numbers actually observed.

    Feed every received message's sequence number to :meth:`observe` (in receipt order;
    order doesn't matter for the count). :attr:`dropped_messages` is the number of
    sequence numbers below the highest one seen that never showed up — i.e. messages the
    middleware silently lost. Negative sequence numbers (handshake probes, see
    :mod:`benchmarks.common.payload`) are ignored.
    """

    def __init__(self) -> None:
        """Create a tracker that has observed nothing yet."""
        self._seen: set[int] = set()
        self._max_seq: int | None = None

    def observe(self, seq: int) -> None:
        """Record that a message with *seq* was received."""
        if seq < 0:
            return
        self._seen.add(seq)
        if self._max_seq is None or seq > self._max_seq:
            self._max_seq = seq

    @property
    def received_count(self) -> int:
        """How many distinct non-probe sequence numbers have been observed."""
        return len(self._seen)

    @property
    def dropped_messages(self) -> int:
        """Messages between 0 and the highest sequence number seen that never arrived."""
        if self._max_seq is None:
            return 0
        expected = self._max_seq + 1
        return expected - len(self._seen)
