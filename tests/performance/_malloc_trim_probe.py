"""Fresh-process probe: does ``_malloc_trim`` return memory a workload freed?

Each run builds one kind of allocation, frees it and collects garbage as the trim's
callers do, then reads the resident set before and after ``_malloc_trim``. The kinds
are the allocators a Haute process uses: a Polars frame (Polars' own allocator,
jemalloc on Linux and mimalloc on Windows), many mid-sized NumPy arrays (the C
runtime's heap, each below glibc's mmap threshold, as a model's buffers are) and
many small Python objects (Python's own arenas and the C runtime's heap). The pinned
NumPy kind keeps one array allocated after the rest, as a later allocation of a
running job does, so the heap cannot shrink from its top on its own.
"""

from __future__ import annotations

import argparse
import gc
import json
import os
import sys
import time
from pathlib import Path

from scripts.memory_smoke import StdlibMemorySampler

KINDS = ("polars", "numpy", "numpy_pinned", "python")

#: About 256 MiB of each kind: large enough to read through resident-set noise.
_POLARS_ROWS = 4_000_000
_POLARS_COLUMNS = 8
_NUMPY_ARRAY_FLOATS = 8_192  # 64 KiB, below glibc's default 128 KiB mmap threshold
_NUMPY_ARRAYS = 4_096
_PYTHON_OBJECTS = 2_000_000


def _allocate(kind: str) -> object:
    if kind == "polars":
        import polars as pl

        column = pl.int_range(0, _POLARS_ROWS, eager=True).cast(pl.Float64)
        return pl.DataFrame({f"c{index}": column + index for index in range(_POLARS_COLUMNS)})
    if kind in {"numpy", "numpy_pinned"}:
        import numpy as np

        return [
            np.full(_NUMPY_ARRAY_FLOATS, index, dtype=np.float64) for index in range(_NUMPY_ARRAYS)
        ]
    if kind == "python":
        return [(index, float(index), str(index)) for index in range(_PYTHON_OBJECTS)]
    raise ValueError(f"unknown kind {kind!r}")


def _allocate_pin() -> object:
    import numpy as np

    return np.zeros(_NUMPY_ARRAY_FLOATS, dtype=np.float64)


def _settled_rss(sampler: StdlibMemorySampler) -> int:
    # A short pause lets a background release (jemalloc's decay, say) show in the
    # reading the trim is compared against, rather than be credited to the trim.
    time.sleep(0.2)
    rss = sampler.process_rss_bytes(os.getpid())
    assert rss is not None
    return rss


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kind", choices=KINDS, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    import polars as pl

    from haute._polars_utils import _malloc_trim

    sampler = StdlibMemorySampler()
    gc.collect()
    baseline = _settled_rss(sampler)
    held = _allocate(args.kind)
    # Allocated after the rest, so it outlives them above them in the heap.
    pinned = _allocate_pin() if args.kind == "numpy_pinned" else None
    allocated = _settled_rss(sampler)
    del held
    gc.collect()
    after_free = _settled_rss(sampler)
    _malloc_trim()
    after_trim = _settled_rss(sampler)
    del pinned
    payload = {
        "schema_version": 1,
        "kind": args.kind,
        "platform": sys.platform,
        "polars_version": pl.__version__,
        "baseline_rss_bytes": baseline,
        "allocated_rss_bytes": allocated,
        "after_free_rss_bytes": after_free,
        "after_trim_rss_bytes": after_trim,
        "freed_but_resident_bytes": after_free - baseline,
        "returned_by_trim_bytes": after_free - after_trim,
    }
    # ``--output`` is supplied from pytest's tmp_path by the parent harness.
    args.output.write_text(  # write-sandbox: deliberate
        json.dumps(payload, sort_keys=True), encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
