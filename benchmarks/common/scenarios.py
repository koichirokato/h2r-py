"""The default (message size, message count) scenarios every ``bench-*`` target runs.

Stdlib-only. Sizes cover the range from small control/telemetry messages up to
image/point-cloud-sized frames; counts shrink as size grows to keep each scenario's
total wall-clock time roughly comparable.
"""

from __future__ import annotations

DEFAULT_SCENARIOS: list[tuple[int, int]] = [
    (64, 10_000),  # small control/telemetry message
    (1024, 10_000),  # 1 KiB, typical sensor sample (e.g. IMU)
    (65_536, 2_000),  # 64 KiB, e.g. a compressed image tile
    (1_048_576, 500),  # 1 MiB, e.g. a point cloud or camera frame
]
"""``(message_size_bytes, message_count)`` pairs; see :mod:`benchmarks.run_bench`."""
