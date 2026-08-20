"""Background CPU%/RSS sampling for a running process, via ``psutil``.

Not stdlib-only (needs the ``bench`` extra) and not covered by ``make check``'s pytest
run; exercised only by actually running a benchmark. See :mod:`benchmarks.common.runner`.
"""

from __future__ import annotations

import contextlib
import dataclasses
import statistics
import threading

import psutil

_DEFAULT_INTERVAL_S = 0.05


@dataclasses.dataclass(frozen=True)
class ResourceUsage:
    """Summary of CPU/memory usage sampled over a process's (or processes') lifetime."""

    cpu_percent_mean: float
    cpu_percent_peak: float
    rss_bytes_mean: float
    rss_bytes_peak: float


_EMPTY_USAGE = ResourceUsage(
    cpu_percent_mean=0.0,
    cpu_percent_peak=0.0,
    rss_bytes_mean=0.0,
    rss_bytes_peak=0.0,
)


def combine_usage(usages: list[ResourceUsage]) -> ResourceUsage:
    """Sum per-process :class:`ResourceUsage` values into one combined-process figure.

    Used to report a single-process-equivalent CPU/memory number for middlewares (zmq,
    gRPC, ROS 2) benchmarked as two OS processes — see the scope note in
    :mod:`benchmarks.run_bench` about h2r's single-process measurement not being strictly
    comparable to these. Summing means/peaks independently is an approximation (it doesn't
    account for the two processes' peaks occurring at different times), not a precise
    time-aligned combination.
    """
    if not usages:
        return _EMPTY_USAGE
    return ResourceUsage(
        cpu_percent_mean=sum(usage.cpu_percent_mean for usage in usages),
        cpu_percent_peak=sum(usage.cpu_percent_peak for usage in usages),
        rss_bytes_mean=sum(usage.rss_bytes_mean for usage in usages),
        rss_bytes_peak=sum(usage.rss_bytes_peak for usage in usages),
    )


class ResourceSampler:
    """Samples one process's CPU% and RSS on a background thread until :meth:`stop`.

    Not `asyncio`-based (unlike the rest of h2r): this needs to keep sampling while the
    process it's watching is busy running its own event loop, so it uses a plain OS thread
    instead of a coroutine that would have to share that loop.
    """

    def __init__(self, pid: int, interval_s: float = _DEFAULT_INTERVAL_S) -> None:
        """Prepare to sample *pid* every *interval_s* seconds once :meth:`start` is called."""
        self._process = psutil.Process(pid)
        self._interval_s = interval_s
        self._cpu_samples: list[float] = []
        self._rss_samples: list[float] = []
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Start the background sampling thread."""
        # Primes psutil's internal CPU-time baseline; without this the first real call
        # to `cpu_percent()` below would report 0.0 regardless of actual usage.
        with contextlib.suppress(psutil.NoSuchProcess, psutil.AccessDenied):
            self._process.cpu_percent()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop_event.wait(self._interval_s):
            with contextlib.suppress(psutil.NoSuchProcess, psutil.AccessDenied):
                self._cpu_samples.append(self._process.cpu_percent())
                self._rss_samples.append(float(self._process.memory_info().rss))

    def stop(self) -> ResourceUsage:
        """Stop sampling and return the summary; safe to call even if never started."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=self._interval_s * 10)
        if not self._cpu_samples:
            return _EMPTY_USAGE
        return ResourceUsage(
            cpu_percent_mean=statistics.fmean(self._cpu_samples),
            cpu_percent_peak=max(self._cpu_samples),
            rss_bytes_mean=statistics.fmean(self._rss_samples),
            rss_bytes_peak=max(self._rss_samples),
        )
