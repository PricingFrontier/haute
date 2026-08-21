"""Fresh-process structured cache-build probe for bounded-memory certification."""

from __future__ import annotations

import argparse
import gc
import json
import os
import time
from pathlib import Path
from typing import Any

from haute._json_shred import build_per_port_cache
from scripts.memory_smoke import StdlibMemorySampler


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--rows", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def _column(name: str, path: str, type_token: str) -> dict[str, Any]:
    return {
        "name": name,
        "path": path,
        "type": type_token,
        "status": "Confirmed",
        "selected": True,
        "levels": None,
    }


def _config() -> dict[str, Any]:
    return {
        "tables": [
            {
                "path": "$[:]",
                "label": "records",
                "emit": True,
                "row_id_column": None,
                "columns": [
                    _column("id", "$[:].id", "str"),
                    _column("payload", "$[:].payload", "str"),
                ],
            }
        ]
    }


def _write_evidence(output: Path, evidence: dict[str, int | float]) -> None:
    """Persist probe evidence to the parent-provided scratch destination."""
    output.write_text(json.dumps(evidence, sort_keys=True), encoding="utf-8")


def main() -> int:
    args = _parse_args()
    sampler = StdlibMemorySampler()
    gc.collect()
    rss_before = sampler.process_rss_bytes(os.getpid())
    started = time.perf_counter()
    summary = build_per_port_cache(args.source, _config(), args.cache)
    elapsed_seconds = time.perf_counter() - started
    rss_after = sampler.process_rss_bytes(os.getpid())
    table = summary["tables"][0]
    if table["row_count"] != args.rows:
        raise RuntimeError(
            f"cache row count {table['row_count']} did not match expected {args.rows}"
        )
    _write_evidence(
        args.output,
        {
            "schema_version": 1,
            "rows": args.rows,
            "source_bytes": args.source.stat().st_size,
            "cache_bytes": sum(path.stat().st_size for path in args.cache.glob("*")),
            "elapsed_seconds": elapsed_seconds,
            "rss_before_bytes": rss_before,
            "rss_after_bytes": rss_after,
        },
    )
    time.sleep(0.1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
