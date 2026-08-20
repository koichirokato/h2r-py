"""``BenchmarkResult``: one scenario's measurements for one middleware, plus JSON I/O.

Stdlib-only (``dataclasses`` + ``json``), and covered by ``tests/test_bench_result.py``.
"""

from __future__ import annotations

import dataclasses
import json
import pathlib
from typing import TYPE_CHECKING
from typing import Any

if TYPE_CHECKING:
    from collections.abc import Sequence


@dataclasses.dataclass(frozen=True)
class BenchmarkResult:
    """One (middleware, message size, message count) scenario's measured outcome.

    Durations are in seconds, latencies in milliseconds, memory in bytes — matching the
    units a human reads the comparison report in, not the nanosecond/byte-count units the
    lower-level :mod:`benchmarks.common.stats` and :mod:`benchmarks.common.payload` work in.
    """

    middleware: str
    message_size_bytes: int
    message_count: int
    throughput_msgs_per_s: float
    throughput_mb_per_s: float
    latency_p50_ms: float
    latency_p95_ms: float
    latency_p99_ms: float
    latency_mean_ms: float
    dropped_messages: int
    startup_connect_s: float
    cpu_percent_mean: float
    cpu_percent_peak: float
    rss_bytes_mean: float
    rss_bytes_peak: float
    timestamp: str
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable ``dict`` of every field."""
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BenchmarkResult:
        """Build a :class:`BenchmarkResult` from a ``dict`` shaped like :meth:`to_dict`."""
        return cls(**data)


def save_results(results: Sequence[BenchmarkResult], path: str | pathlib.Path) -> None:
    """Write *results* to *path* as a JSON array (overwrites any existing file)."""
    payload = [result.to_dict() for result in results]
    pathlib.Path(path).write_text(json.dumps(payload, indent=2) + "\n")


def load_results(path: str | pathlib.Path) -> list[BenchmarkResult]:
    """Read back a JSON array of results previously written by :func:`save_results`."""
    payload = json.loads(pathlib.Path(path).read_text())
    return [BenchmarkResult.from_dict(item) for item in payload]
