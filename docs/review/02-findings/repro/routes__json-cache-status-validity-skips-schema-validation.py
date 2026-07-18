"""Adversarial repro for claim:
  json-cache-status-validity-skips-schema-validation

Claim under test
----------------
The status/validity route path
    post/get /json-cache/status
      -> _v2_status_response
        -> is_per_port_cache_valid(cache_dir, v2_config, data_path=...)
reads an on-disk / volatile v2 config WITHOUT running validate_v2_schema,
and _v2_fingerprint silently `continue`s past non-dict tables/columns.
So a config A and a config B that differs only by an EXTRA non-dict column
(a bare string column entry rather than an object) hash to the SAME
schema_fingerprint. A per-port cache built for A is therefore judged
"fresh" for B by is_per_port_cache_valid -> the status endpoint reports
cached=True against a parquet set that does not reflect config B.

The routes-specific angle: /build calls validate_v2_schema (which
loud-fails B because a non-dict column is rejected), but the status path
never validates, so B never loud-fails before the freshness compare.

What counts as REPRODUCED
-------------------------
We assert on the specific wrong VALUES:
  (1) _v2_fingerprint(A) == _v2_fingerprint(B)          (collision)
  (2) validate_v2_schema(B) RAISES ApiInputSchemaError  (build would reject B)
  (3) is_per_port_cache_valid(cache_for_A, B, ...) is True   (stale judged fresh)
  (4) _v2_status_response(...) for B reports cached=True

If (1) and (3) hold while (2) shows B is build-rejected, the status path
genuinely reports a cache as fresh for a config the build would have
refused, having skipped the validation entirely.

Isolation: all disk I/O via tempfile; project root set via
haute._sandbox.set_project_root(tmp); no real project files touched.
"""

from __future__ import annotations

import copy
import json
import os
import tempfile
from pathlib import Path


def main() -> int:
    import haute._sandbox as _sandbox

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        _sandbox.set_project_root(tmp)
        # _json_cache_dir() resolves under Path.cwd(); chdir into the tempdir
        # so NO cache artifact is ever written into the real project tree.
        prev_cwd = Path.cwd()
        os.chdir(tmp)
        try:
            return _run(tmp)
        finally:
            # Restore cwd so TemporaryDirectory cleanup can remove the tree
            # (Windows refuses to delete the process's current directory).
            os.chdir(prev_cwd)


def _run(tmp: Path) -> int:
        from haute._api_input_schema import ApiInputSchemaError, validate_v2_schema
        from haute._json_shred import (
            _v2_fingerprint,
            build_per_port_cache,
            is_per_port_cache_valid,
            read_per_port_cache_meta,
        )

        # --- synthetic data file (JSONL): two records, root table ----------
        data_path = tmp / "data.jsonl"
        records = [
            {"id": 1, "name": "alpha"},
            {"id": 2, "name": "beta"},
        ]
        data_path.write_text(
            "\n".join(json.dumps(r) for r in records) + "\n",
            encoding="utf-8",
        )

        # --- config A: a normal, valid v2 schema ---------------------------
        config_a: dict = {
            "tables": [
                {
                    "path": "$",
                    "label": "root",
                    "emit": True,
                    "row_id_column": None,
                    "columns": [
                        {"name": "id", "path": "$.id", "type": "int", "selected": True},
                        {
                            "name": "name",
                            "path": "$.name",
                            "type": "str",
                            "selected": True,
                        },
                    ],
                },
            ],
        }

        # --- config B: A + an EXTRA non-dict column (a bare string) --------
        # Differentiating content lives in a malformed entry. _v2_fingerprint
        # `continue`s past it; validate_v2_schema rejects it.
        config_b = copy.deepcopy(config_a)
        config_b["tables"][0]["columns"].append("extra_column_as_bare_string")

        # (1) fingerprint collision -----------------------------------------
        fp_a = _v2_fingerprint(config_a)
        fp_b = _v2_fingerprint(config_b)
        print(f"[1] fingerprint(A) = {fp_a[:16]}")
        print(f"[1] fingerprint(B) = {fp_b[:16]}")
        assert fp_a == fp_b, "EXPECTED collision: _v2_fingerprint(A) == _v2_fingerprint(B)"
        print("[1] OK: two textually-distinct configs share one schema_fingerprint")

        # (2) the build path would loud-fail B ------------------------------
        build_rejected_b = False
        try:
            validate_v2_schema(config_b)
        except ApiInputSchemaError as exc:
            build_rejected_b = True
            print(f"[2] validate_v2_schema(B) raised: {exc}")
        assert build_rejected_b, (
            "EXPECTED validate_v2_schema(B) to raise (non-dict column) — "
            "this is what /build enforces and the status path skips"
        )
        # sanity: A is genuinely valid
        validate_v2_schema(config_a)
        print("[2] OK: B is build-rejected; A is build-valid")

        # --- build a REAL per-port cache for config A ----------------------
        from haute._json_flatten import _json_cache_dir

        cache_dir = Path(_json_cache_dir(str(data_path), "working"))
        summary = build_per_port_cache(str(data_path), config_a, cache_dir)
        print(f"[setup] built cache for A at {cache_dir}")
        print(f"[setup] recorded schema_fingerprint = {summary['schema_fingerprint'][:16]}")
        meta = read_per_port_cache_meta(cache_dir)
        assert meta is not None, "cache build should have written meta.json"
        assert meta.get("schema_fingerprint") == fp_a

        # Sanity: cache is fresh for the very config it was built from.
        assert is_per_port_cache_valid(cache_dir, config_a, data_path=data_path) is True

        # (3) THE BUG: cache built for A is judged fresh for B --------------
        valid_for_b = is_per_port_cache_valid(cache_dir, config_b, data_path=data_path)
        print(f"[3] is_per_port_cache_valid(cache_for_A, B) = {valid_for_b}")
        assert valid_for_b is True, (
            "EXPECTED stale-judged-fresh: is_per_port_cache_valid returns True for "
            "config B even though B's schema differs from what was cached"
        )
        print("[3] OK: the cache built for A is reported FRESH for the different config B")

        # (4) reproduce the route-level decision in _v2_status_response -----
        # Mirror the exact control flow of routes.json_cache._v2_status_response,
        # which never calls validate_v2_schema before this point.
        from haute.routes.json_cache import _v2_status_response

        status = _v2_status_response(str(data_path), config_b, "apiInput-path-B")
        print(f"[4] _v2_status_response(B).cached = {status.cached}")
        assert status.cached is True, (
            "EXPECTED the status endpoint to (wrongly) report cached=True for B "
            "— it read an unvalidated config and the fingerprint collided"
        )
        print("[4] OK: status endpoint reports cached=True for the build-rejected config B")

        print()
        print("REPRODUCED: status/validity path reports a per-port cache as fresh for a")
        print("config the build would have loud-failed; validate_v2_schema is skipped and")
        print("_v2_fingerprint's non-dict `continue` makes the two configs collide.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
