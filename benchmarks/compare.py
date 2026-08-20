"""Render every ``benchmarks/results/*.json`` file into a Markdown comparison report.

::

    uv run python -m benchmarks.compare

Prints the report to stdout and writes it to ``benchmarks/results/report.md``. Not part
of ``make check``/CI — see the ``bench-report`` Makefile target.
"""

from __future__ import annotations

import argparse
import itertools
import pathlib

from benchmarks.common.result import BenchmarkResult
from benchmarks.common.result import load_results

_RESULTS_DIR = pathlib.Path(__file__).resolve().parent / "results"
_REPORT_FILENAME = "report.md"

_METHODOLOGY_NOTE = """\
## Methodology notes

- **h2r** is measured single-process: publisher and subscriber share one asyncio event
  loop (see `benchmarks/h2r_bench.py`). Every other middleware below is measured as two
  separate OS processes. CPU/memory figures are summed across both roles either way, but
  the two setups aren't strictly comparable — see `benchmarks/h2r_bench.py`'s docstring.
- **gRPC** has no handshake-probe phase: its server-streaming RPC dispatch is itself the
  synchronization point, unlike the broadcast-with-no-replay middlewares (h2r, ZeroMQ,
  ROS 2), which all need one (see `benchmarks/grpc_bench.py`'s docstring).
- **ROS 2** is measured with BEST_EFFORT reliability and a 64-deep queue (matching h2r's
  own subscriber queue bound), not the default RELIABLE profile — see
  `benchmarks/ros2_bench.py`'s docstring.
- Drops/backpressure reflect each middleware's own stock behavior; no artificial queue
  limits were added beyond what's noted above.
"""


def _load_all_results(results_dir: pathlib.Path) -> list[BenchmarkResult]:
    """Load and flatten every ``*.json`` result file in *results_dir* (sorted by name)."""
    results: list[BenchmarkResult] = []
    for path in sorted(results_dir.glob("*.json")):
        results.extend(load_results(path))
    return results


def _scenario_key(result: BenchmarkResult) -> tuple[int, int]:
    """Group key: ``(message_size_bytes, message_count)``. Pure."""
    return (result.message_size_bytes, result.message_count)


def _format_row(result: BenchmarkResult) -> str:
    """Render one `BenchmarkResult` as a single Markdown table row. Pure."""
    fields = [
        result.middleware,
        f"{result.throughput_msgs_per_s:.0f}",
        f"{result.throughput_mb_per_s:.2f}",
        f"{result.latency_p50_ms:.3f}",
        f"{result.latency_p95_ms:.3f}",
        f"{result.latency_p99_ms:.3f}",
        f"{result.latency_mean_ms:.3f}",
        str(result.dropped_messages),
        f"{result.startup_connect_s:.4f}",
        f"{result.cpu_percent_mean:.1f}",
        f"{result.cpu_percent_peak:.1f}",
        f"{result.rss_bytes_mean / 1_000_000:.1f}",
        f"{result.rss_bytes_peak / 1_000_000:.1f}",
    ]
    return "| " + " | ".join(fields) + " |"


def _format_table(results: list[BenchmarkResult]) -> str:
    """Render one scenario's results (one row per middleware) as a Markdown table.

    Pure: the result depends only on *results*.
    """
    header = (
        "| Middleware | Throughput (msgs/s) | Throughput (MB/s) | p50 (ms) | p95 (ms) | "
        "p99 (ms) | Mean (ms) | Dropped | Connect (s) | CPU% mean | CPU% peak | "
        "RSS mean (MB) | RSS peak (MB) |"
    )
    separator = "|" + "---|" * 13
    rows = [header, separator]
    rows.extend(_format_row(result) for result in sorted(results, key=lambda r: r.middleware))
    return "\n".join(rows)


def build_report_markdown(results: list[BenchmarkResult]) -> str:
    """Build the full Markdown report for *results*.

    Methodology notes, then one table per (message size, message count) scenario, sorted
    by size then count. Pure.
    """
    sections = ["# h2r benchmark comparison", "", _METHODOLOGY_NOTE]
    grouped = itertools.groupby(sorted(results, key=_scenario_key), key=_scenario_key)
    for (size, count), group in grouped:
        sections.append(f"## size={size} bytes, count={count}")
        sections.append("")
        sections.append(_format_table(list(group)))
        sections.append("")
    return "\n".join(sections)


def main(argv: list[str] | None = None) -> None:
    """Load every result file, render the Markdown report, print it, and save it."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir", type=pathlib.Path, default=_RESULTS_DIR)
    args = parser.parse_args(argv)

    results = _load_all_results(args.results_dir)
    report = build_report_markdown(results)
    print(report)

    report_path = args.results_dir / _REPORT_FILENAME
    report_path.write_text(report + "\n")


if __name__ == "__main__":
    main()
