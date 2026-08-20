"""Two-process benchmark orchestration shared by the zmq/gRPC/ROS 2 bench scripts.

h2r is benchmarked differently (see :mod:`benchmarks.h2r_bench`'s module docstring): as a
single process running publisher and subscriber in the same asyncio event loop, since
h2r-py has no separate server binary to launch. Every other middleware here launches its
publisher and subscriber as two independent OS processes — this module is the common glue
for that: it runs ``python -m <module> --role publisher|subscriber ...``, waits for the
subscriber to finish and report its measurements, samples both processes' resource usage
concurrently, and merges everything into one :class:`~benchmarks.common.result.BenchmarkResult`.

Not stdlib-only (depends on :mod:`benchmarks.common.resource`, hence ``psutil``) and not
covered by ``make check``'s pytest run.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime
import json
import pathlib
import subprocess
import sys
from typing import TYPE_CHECKING
from typing import Any

from benchmarks.common.resource import ResourceSampler
from benchmarks.common.resource import combine_usage
from benchmarks.common.stats import LatencyStats

if TYPE_CHECKING:
    from collections.abc import Sequence

    from benchmarks.common.resource import ResourceUsage

from benchmarks.common.result import BenchmarkResult

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_DEFAULT_TIMEOUT_S = 120.0
_TERMINATE_GRACE_S = 5.0


@dataclasses.dataclass(frozen=True)
class SubscriberReport:
    """A subscriber process's self-measured outcome for one scenario, as written to JSON."""

    message_count: int
    dropped_messages: int
    throughput_msgs_per_s: float
    throughput_mb_per_s: float
    latency_p50_ms: float
    latency_p95_ms: float
    latency_p99_ms: float
    latency_mean_ms: float
    startup_connect_s: float

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable ``dict`` of every field."""
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SubscriberReport:
        """Build a :class:`SubscriberReport` from a ``dict`` shaped like :meth:`to_dict`."""
        return cls(**data)


def write_subscriber_report(report: SubscriberReport, path: str | pathlib.Path) -> None:
    """Write *report* to *path* as JSON; called by every ``*_bench.py`` subscriber role."""
    pathlib.Path(path).write_text(json.dumps(report.to_dict()))


def read_subscriber_report(path: str | pathlib.Path) -> SubscriberReport:
    """Read back a report previously written by :func:`write_subscriber_report`."""
    return SubscriberReport.from_dict(json.loads(pathlib.Path(path).read_text()))


def build_subscriber_report(
    *,
    message_size_bytes: int,
    latencies_ns: Sequence[int],
    dropped_messages: int,
    received_count: int,
    recv_duration_s: float,
    startup_connect_s: float,
) -> SubscriberReport:
    """Summarize one subscriber run's raw measurements into a :class:`SubscriberReport`.

    *latencies_ns* are one-way send-to-receive latencies for every real (non-probe)
    message received; *recv_duration_s* is the wall-clock time from the first to the last
    real message received, used for the throughput figures.
    """
    stats = (
        LatencyStats.from_samples_ns(latencies_ns)
        if latencies_ns
        else LatencyStats(p50_ns=0.0, p95_ns=0.0, p99_ns=0.0, mean_ns=0.0)
    )
    if recv_duration_s > 0:
        throughput_msgs_per_s = received_count / recv_duration_s
        throughput_mb_per_s = (received_count * message_size_bytes) / recv_duration_s / 1_000_000
    else:
        throughput_msgs_per_s = 0.0
        throughput_mb_per_s = 0.0
    return SubscriberReport(
        message_count=received_count,
        dropped_messages=dropped_messages,
        throughput_msgs_per_s=throughput_msgs_per_s,
        throughput_mb_per_s=throughput_mb_per_s,
        latency_p50_ms=stats.p50_ns / 1_000_000,
        latency_p95_ms=stats.p95_ns / 1_000_000,
        latency_p99_ms=stats.p99_ns / 1_000_000,
        latency_mean_ms=stats.mean_ns / 1_000_000,
        startup_connect_s=startup_connect_s,
    )


def build_benchmark_result(
    *,
    middleware: str,
    message_size_bytes: int,
    message_count: int,
    report: SubscriberReport,
    usage: ResourceUsage,
    notes: str = "",
) -> BenchmarkResult:
    """Merge a scenario, a :class:`SubscriberReport`, and resource usage into one result.

    *message_count* is the scenario's target count, not necessarily equal to
    ``report.message_count`` (the number actually received) when messages were dropped.
    """
    return BenchmarkResult(
        middleware=middleware,
        message_size_bytes=message_size_bytes,
        message_count=message_count,
        throughput_msgs_per_s=report.throughput_msgs_per_s,
        throughput_mb_per_s=report.throughput_mb_per_s,
        latency_p50_ms=report.latency_p50_ms,
        latency_p95_ms=report.latency_p95_ms,
        latency_p99_ms=report.latency_p99_ms,
        latency_mean_ms=report.latency_mean_ms,
        dropped_messages=report.dropped_messages,
        startup_connect_s=report.startup_connect_s,
        cpu_percent_mean=usage.cpu_percent_mean,
        cpu_percent_peak=usage.cpu_percent_peak,
        rss_bytes_mean=usage.rss_bytes_mean,
        rss_bytes_peak=usage.rss_bytes_peak,
        timestamp=datetime.datetime.now(tz=datetime.UTC).isoformat(),
        notes=notes,
    )


def build_common_arg_parser(
    description: str,
    *,
    supports_handshake: bool,
) -> argparse.ArgumentParser:
    """Build the ``argparse.ArgumentParser`` shared by every two-process ``*_bench.py``.

    *supports_handshake* adds ``--ready-file`` (see this module's docstring and
    :mod:`benchmarks.common.payload`'s handshake-probe note); gRPC doesn't need it, since
    its RPC dispatch is itself the synchronization point.
    """
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--role", choices=["publisher", "subscriber"], required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--size", type=int, required=True, help="payload size in bytes")
    parser.add_argument("--count", type=int, required=True, help="number of real messages")
    parser.add_argument("--result-file", type=pathlib.Path, required=True)
    if supports_handshake:
        parser.add_argument("--ready-file", type=pathlib.Path, required=True)
    return parser


def _role_argv(
    module: str,
    *,
    role: str,
    host: str,
    port: int,
    size: int,
    count: int,
    result_file: pathlib.Path,
    ready_file: pathlib.Path | None,
) -> list[str]:
    """Build the ``python -m <module> --role ...`` argv for one process's role."""
    argv = [
        sys.executable,
        "-m",
        module,
        "--role",
        role,
        "--host",
        host,
        "--port",
        str(port),
        "--size",
        str(size),
        "--count",
        str(count),
        "--result-file",
        str(result_file),
    ]
    if ready_file is not None:
        argv.extend(["--ready-file", str(ready_file)])
    return argv


def run_subprocess_scenario(
    *,
    middleware: str,
    module: str,
    host: str,
    port: int,
    size: int,
    count: int,
    needs_handshake: bool,
    result_dir: pathlib.Path,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
    notes: str = "",
) -> BenchmarkResult:
    """Run one (size, count) scenario for *middleware* as two subprocesses of *module*.

    *module* is dotted (e.g. ``"benchmarks.zmq_bench"``) and must accept the CLI built by
    :func:`build_common_arg_parser` for both ``--role publisher`` and ``--role subscriber``.
    The publisher is launched first (it idles/probes until the subscriber is ready, or —
    for gRPC — simply waits for the RPC that starts it), then the subscriber; this
    function blocks until the subscriber exits and has written its result file.
    """
    result_file = result_dir / "result.json"
    ready_file = result_dir / "ready.txt" if needs_handshake else None

    publisher_argv = _role_argv(
        module,
        role="publisher",
        host=host,
        port=port,
        size=size,
        count=count,
        result_file=result_file,
        ready_file=ready_file,
    )
    subscriber_argv = _role_argv(
        module,
        role="subscriber",
        host=host,
        port=port,
        size=size,
        count=count,
        result_file=result_file,
        ready_file=ready_file,
    )

    publisher_proc = subprocess.Popen(publisher_argv, cwd=_REPO_ROOT)
    subscriber_proc = subprocess.Popen(subscriber_argv, cwd=_REPO_ROOT)
    publisher_sampler = ResourceSampler(publisher_proc.pid)
    subscriber_sampler = ResourceSampler(subscriber_proc.pid)
    publisher_sampler.start()
    subscriber_sampler.start()

    def _kill_and_message(elapsed_s: float) -> str:
        publisher_proc.kill()
        subscriber_proc.kill()
        publisher_proc.wait()
        subscriber_proc.wait()
        return f"{middleware} scenario (size={size}, count={count}) timed out after {elapsed_s}s"

    try:
        try:
            subscriber_proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            raise RuntimeError(_kill_and_message(timeout_s)) from None

        try:
            publisher_proc.wait(timeout=_TERMINATE_GRACE_S)
        except subprocess.TimeoutExpired:
            raise RuntimeError(_kill_and_message(_TERMINATE_GRACE_S)) from None
    finally:
        usage = combine_usage([publisher_sampler.stop(), subscriber_sampler.stop()])

    if subscriber_proc.returncode != 0:
        message = f"{middleware} subscriber exited with code {subscriber_proc.returncode}"
        raise RuntimeError(message)
    if publisher_proc.returncode != 0:
        message = f"{middleware} publisher exited with code {publisher_proc.returncode}"
        raise RuntimeError(message)

    report = read_subscriber_report(result_file)
    return build_benchmark_result(
        middleware=middleware,
        message_size_bytes=size,
        message_count=count,
        report=report,
        usage=usage,
        notes=notes,
    )
