"""Middleware-agnostic pieces shared by every ``benchmarks/*_bench.py`` script.

Everything in here except :mod:`benchmarks.common.resource` and :mod:`benchmarks.common.runner`
is stdlib-only and covered by the regular pytest suite (unlike the rest of :mod:`benchmarks`).
"""

from __future__ import annotations
