"""Manual-only benchmark suite comparing h2r against ZeroMQ, gRPC, and ROS 2.

Not exercised by `make check` or CI — see the ``bench-*`` Makefile targets and
:mod:`benchmarks.run_bench`. Only :mod:`benchmarks.common` (stdlib-only pure logic) is
covered by the regular pytest suite, via ``tests/test_bench_payload.py`` and
``tests/test_bench_stats.py``.
"""

from __future__ import annotations
