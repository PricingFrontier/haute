"""V054 reproduction — emit-true table with ZERO selected columns.

Frontend gate bug (ApiInputEditor.tsx:396-413): the cache button is enabled
whenever ANY table has `emit:true`, but the backend's single source of truth
for "this table contributes a data port" is `table_is_emitting` =
`emit AND >=1 selected column`. So a schema with an emit-true table but no
selected columns (exactly the shape `addTable` creates: emit:true, columns:[])
ENABLES the cache button.

This script proves the BACKEND consequence that the weak frontend gate exposes:
  (1) build_per_port_cache SUCCEEDS for such a config and writes meta.json with
      tables == []  (no RuntimeError, so the button would flip to "Refresh
      Cache");
  (2) is_per_port_cache_valid then reports the cache VALID (the validity loop
      skips non-emitting tables, so "no emitting table" => nothing to miss);
  (3) load_v2_api_source on the SAME config raises the specific RuntimeError
      "...emit-true tables but none has any selected columns..." — i.e. preview
      / run can never load.

The build-succeeds vs load-fails split is precisely what the frontend gate
should prevent (by mirroring `table_is_emitting`) but does not.

ISOLATION: all disk I/O is under a Python tempdir; cwd is moved there so
`_json_cache_dir` (uses Path.cwd()) resolves inside the sandbox; the project
root is overridden to the tempdir. Nothing under rating/, src/, tests/ or any
real project file is read or written.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import haute._sandbox as _sandbox
from haute._json_flatten import _json_cache_dir
from haute._json_shred import (
    build_per_port_cache,
    is_per_port_cache_valid,
    load_v2_api_source,
    table_is_emitting,
)


def main() -> int:
    original_cwd = Path.cwd()
    tmp = Path(tempfile.mkdtemp(prefix="v054_"))
    try:
        # Sandbox: project root + cwd both inside the tempdir.
        _sandbox.set_project_root(tmp)
        os.chdir(tmp)

        # Minimal JSON data file (a root array of two objects).
        data_path = tmp / "quotes.json"
        data_path.write_text('[{"a": 1}, {"a": 2}]', encoding="utf-8")

        # The exact config the frontend produces after one "Add Table":
        # emit:true, columns:[]  (no selected column). A non-blank label/path
        # so validate_v2_schema passes.
        config = {
            "tables": [
                {
                    "path": "$[*]",
                    "label": "root",
                    "displayPath": None,
                    "emit": True,
                    "columns": [],
                },
            ],
        }

        # Sanity: the shared predicate says this table is NOT emitting, yet the
        # frontend gate (t.emit) would treat it as cache-eligible.
        assert table_is_emitting(config["tables"][0]) is False, (
            "expected table_is_emitting == False for emit-true / zero selected columns"
        )

        # (1) The build that the (wrongly) enabled button would fire.
        cache_dir = _json_cache_dir(data_path, "working")
        summary = build_per_port_cache(str(data_path), config, cache_dir)

        # It SUCCEEDS (no RuntimeError) and records zero tables -> the UI flips
        # to "Refresh Cache" as if the cache were real.
        assert summary["tables"] == [], (
            f"expected build summary tables == [] (no emitting ports), got {summary['tables']!r}"
        )

        # (2) Validity check now reports the (empty) cache as VALID, so the
        # button stays in the "cached / Refresh" state.
        valid = is_per_port_cache_valid(cache_dir, config, data_path=str(data_path))
        assert valid is True, (
            "expected is_per_port_cache_valid == True for the freshly built empty cache"
        )

        # (3) The very next preview/run rejects — the wrong VALUE/behaviour the
        # finder predicts. Assert on the SPECIFIC message, not merely "raised".
        raised_msg = None
        try:
            load_v2_api_source(str(data_path), config)
        except RuntimeError as exc:  # noqa: BLE001 — asserting on the message
            raised_msg = str(exc)

        assert raised_msg is not None, (
            "expected load_v2_api_source to raise RuntimeError, but it returned normally"
        )
        assert "emit-true tables but none has any selected columns" in raised_msg, (
            "expected the 'none has any selected columns' RuntimeError; got: " + raised_msg
        )

        print("REPRO OK — build succeeded with tables == [] and cache valid == True,")
        print("           but load_v2_api_source raised:")
        print("           " + raised_msg.splitlines()[0])
        print("Conclusion: frontend gate (t.emit only) enables a cache that the")
        print("            backend's load predicate (emit AND >=1 selected col) rejects.")
        return 0
    finally:
        os.chdir(original_cwd)
        # Best-effort cleanup of the isolated tempdir.
        try:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    sys.exit(main())
