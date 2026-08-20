"""CLI entry point: run one middleware's benchmark across a set of scenarios.

::

    uv run python -m benchmarks.run_bench --middleware h2r
    uv run python -m benchmarks.run_bench --middleware zmq --sizes 1024 65536 --counts 5000 1000

Writes one JSON file (a list of :class:`~benchmarks.common.result.BenchmarkResult`,
see :mod:`benchmarks.common.result`) per invocation into ``benchmarks/results/`` — the
input :mod:`benchmarks.compare` reads. See the ``bench-*`` Makefile targets for the
normal way to invoke this (inside the ``bench`` / ``bench-ros2`` Docker images; never
from ``make check`` or CI).
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import pathlib
import tempfile

from benchmarks.common import runner
from benchmarks.common.result import BenchmarkResult
from benchmarks.common.result import save_results
from benchmarks.common.scenarios import DEFAULT_SCENARIOS

_RESULTS_DIR = pathlib.Path(__file__).resolve().parent / "results"
_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_BASE_PORT = 15_550

_SUBPROCESS_MODULES = {
    "zmq": "benchmarks.zmq_bench",
    "grpc": "benchmarks.grpc_bench",
    "ros2": "benchmarks.ros2_bench",
    "zenoh": "benchmarks.zenoh_bench",
}
_NEEDS_HANDSHAKE = {
    "zmq": True,
    "grpc": False,
    "ros2": True,
    "zenoh": True,
}
_NOTES = {
    "h2r": "single-process measurement: publisher and subscriber share one event loop, "
    "unlike the other middlewares' two-process measurement (see benchmarks/h2r_bench.py).",
    "grpc": "no handshake probe phase: RPC dispatch is itself the synchronization point "
    "(see benchmarks/grpc_bench.py).",
    "ros2": "BEST_EFFORT reliability with a 64-deep queue, matching h2r's own subscriber "
    "queue bound; the default RELIABLE QoS profile is out of scope (see benchmarks/ros2_bench.py).",
    "zenoh": "brokerless, no discovery daemon required, like h2r — a growing DDS "
    "alternative in robotics (see benchmarks/zenoh_bench.py).",
}


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--middleware",
        required=True,
        choices=["h2r", "zmq", "grpc", "ros2", "zenoh"],
    )
    parser.add_argument("--sizes", type=int, nargs="*", default=None, help="payload sizes, bytes")
    parser.add_argument("--counts", type=int, nargs="*", default=None, help="message counts")
    parser.add_argument("--host", default=_DEFAULT_HOST)
    parser.add_argument("--base-port", type=int, default=_DEFAULT_BASE_PORT)
    parser.add_argument("--output-dir", type=pathlib.Path, default=_RESULTS_DIR)
    args = parser.parse_args(argv)
    if (args.sizes is None) != (args.counts is None):
        parser.error("--sizes and --counts must be given together")
    if args.sizes is not None and len(args.sizes) != len(args.counts):
        parser.error("--sizes and --counts must have the same length")
    return args


def _scenarios(args: argparse.Namespace) -> list[tuple[int, int]]:
    if args.sizes is None:
        return DEFAULT_SCENARIOS
    return list(zip(args.sizes, args.counts, strict=True))


def _run_h2r(scenarios: list[tuple[int, int]]) -> list[BenchmarkResult]:
    # Imported lazily: h2r.subscriber uses `typing.Self` (Python 3.11+), which would break
    # --middleware ros2/zmq/grpc under the ROS 2 Humble image's system Python 3.10 if this
    # were a module-level import — those middlewares never touch the h2r package.
    from benchmarks import h2r_bench  # noqa: PLC0415

    notes = _NOTES["h2r"]
    results = []
    for size, count in scenarios:
        result = asyncio.run(h2r_bench.run_scenario(size, count, notes=notes))
        results.append(result)
        print(
            f"h2r: size={size} count={count} "
            f"throughput={result.throughput_msgs_per_s:.0f} msgs/s "
            f"p50={result.latency_p50_ms:.3f}ms dropped={result.dropped_messages}",
        )
    return results


def _run_subprocess_middleware(
    middleware: str,
    scenarios: list[tuple[int, int]],
    *,
    host: str,
    base_port: int,
) -> list[BenchmarkResult]:
    module = _SUBPROCESS_MODULES[middleware]
    needs_handshake = _NEEDS_HANDSHAKE[middleware]
    notes = _NOTES.get(middleware, "")
    results = []
    for index, (size, count) in enumerate(scenarios):
        with tempfile.TemporaryDirectory(prefix=f"h2r-bench-{middleware}-") as tmp_dir:
            result = runner.run_subprocess_scenario(
                middleware=middleware,
                module=module,
                host=host,
                port=base_port + index,
                size=size,
                count=count,
                needs_handshake=needs_handshake,
                result_dir=pathlib.Path(tmp_dir),
                notes=notes,
            )
        results.append(result)
        print(
            f"{middleware}: size={size} count={count} "
            f"throughput={result.throughput_msgs_per_s:.0f} msgs/s "
            f"p50={result.latency_p50_ms:.3f}ms dropped={result.dropped_messages}",
        )
    return results


def main(argv: list[str] | None = None) -> None:
    """Run every configured scenario for one middleware and save the results as JSON."""
    args = _parse_args(argv)
    scenarios = _scenarios(args)

    if args.middleware == "h2r":
        results = _run_h2r(scenarios)
    else:
        results = _run_subprocess_middleware(
            args.middleware,
            scenarios,
            host=args.host,
            base_port=args.base_port,
        )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.datetime.now(tz=datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_path = args.output_dir / f"{args.middleware}-{timestamp}.json"
    save_results(results, output_path)
    print(f"wrote {len(results)} result(s) to {output_path}")


if __name__ == "__main__":
    main()
