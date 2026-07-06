"""Adversarial repro for claim v2-fingerprint-malformed-table-collision.

Claim: `_v2_fingerprint` silently skips non-dict tables/columns (the
`if not isinstance(table, dict): continue` and `if not isinstance(col, dict):
continue` at _json_shred.py:118-123), so a config carrying a malformed
(non-dict) table/column entry fingerprints byte-identically to the same config
with that entry absent. Because `is_per_port_cache_valid` (line 1064) compares
`_v2_fingerprint(v2_config)` of an ARBITRARY caller-supplied config against the
stored meta fingerprint WITHOUT re-running `validate_v2_schema`, a cache built
for config A is judged "fresh" for a structurally-different config B.

This script:
  1. Asserts the raw fingerprint collision directly (clean cfg vs cfg + a
     string table vs cfg + a string column vs cfg + two DIFFERENT garbage
     tables) -- all must hash identically, and to the specific SHA-256 quoted
     in the claim.
  2. Builds a REAL valid per-port cache for config A via `build_per_port_cache`
     into a tempdir, then calls `is_per_port_cache_valid(cache_dir, cfg_B, ...)`
     where cfg_B == cfg_A plus a non-dict table appended to tables[]. The claim
     predicts this returns True (cache judged fresh for a different config).
  3. Confirms `validate_v2_schema` WOULD reject cfg_B on the build path -- i.e.
     the collision only matters because the validity trapdoor skips validation.

Isolation: all disk I/O via tempfile; project root pinned into the tempdir; no
read/write of rating/, src/, tests/, or any real project file.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path


def _clean_config(data_filename: str) -> dict:
    """A minimal, valid v2 config with one emit-true table + one column."""
    return {
        "path": data_filename,
        "tables": [
            {
                "path": "$",
                "label": "root",
                "emit": True,
                "row_id_column": None,
                "columns": [
                    {
                        "name": "id",
                        "path": "$.id",
                        "type": "str",
                        "selected": True,
                        "levels": None,
                    }
                ],
            }
        ],
    }


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="haute_repro_v2fp_"))

    # Pin sandbox project root AND cwd into the tempdir before importing the
    # cache modules (_json_cache_dir uses Path.cwd()). build/validity take an
    # explicit cache_dir + data_path so this is belt-and-braces isolation.
    os.chdir(tmp)

    import haute._sandbox as _sandbox

    _sandbox.set_project_root(tmp)

    from haute._api_input_schema import ApiInputSchemaError, validate_v2_schema
    from haute._json_shred import (
        _v2_fingerprint,
        build_per_port_cache,
        is_per_port_cache_valid,
    )

    failures: list[str] = []

    # ------------------------------------------------------------------
    # Part 1 — raw fingerprint collision across malformed variants.
    # ------------------------------------------------------------------
    data_filename = "data.json"
    clean = _clean_config(data_filename)

    # (a) extra non-dict (string) TABLE appended.
    cfg_str_table = json.loads(json.dumps(clean))
    cfg_str_table["tables"].append("i am not a dict")

    # (b) extra non-dict (string) COLUMN appended to the existing table.
    cfg_str_col = json.loads(json.dumps(clean))
    cfg_str_col["tables"][0]["columns"].append("neither am i")

    # (c) two DIFFERENT garbage tables: a string vs the integer 12345.
    cfg_garbage_a = json.loads(json.dumps(clean))
    cfg_garbage_a["tables"].append("garbage-string")
    cfg_garbage_b = json.loads(json.dumps(clean))
    cfg_garbage_b["tables"].append(12345)

    fp_clean = _v2_fingerprint(clean)
    fp_str_table = _v2_fingerprint(cfg_str_table)
    fp_str_col = _v2_fingerprint(cfg_str_col)
    fp_garbage_a = _v2_fingerprint(cfg_garbage_a)
    fp_garbage_b = _v2_fingerprint(cfg_garbage_b)

    print(f"fp(clean)              = {fp_clean}")
    print(f"fp(clean + str table)  = {fp_str_table}")
    print(f"fp(clean + str column) = {fp_str_col}")
    print(f"fp(clean + 'garbage')  = {fp_garbage_a}")
    print(f"fp(clean + 12345)      = {fp_garbage_b}")

    # The structural claim: malformed entries are silently dropped, so every
    # variant collapses to the clean fingerprint.
    if fp_str_table != fp_clean:
        failures.append(
            f"EXPECTED collision but fp(clean+str table) {fp_str_table} "
            f"!= fp(clean) {fp_clean}"
        )
    if fp_str_col != fp_clean:
        failures.append(
            f"EXPECTED collision but fp(clean+str column) {fp_str_col} "
            f"!= fp(clean) {fp_clean}"
        )
    if not (fp_garbage_a == fp_garbage_b == fp_clean):
        failures.append(
            "EXPECTED two-different-garbage-tables to collide with clean: "
            f"{fp_garbage_a} / {fp_garbage_b} / {fp_clean}"
        )

    # ------------------------------------------------------------------
    # Part 2 — the validity trapdoor: a cache built for cfg_A judged fresh
    # for structurally-different cfg_B (== cfg_A + a non-dict table).
    # ------------------------------------------------------------------
    data_path = tmp / data_filename
    # Two records so the parquet is non-trivial.
    data_path.write_text(
        json.dumps([{"id": "alpha"}, {"id": "beta"}]),
        encoding="utf-8",
    )

    cfg_A = _clean_config(data_filename)
    cache_dir = tmp / "cache_working"

    # Build a REAL valid cache for cfg_A (this path DOES validate; cfg_A passes).
    build_per_port_cache(
        data_path=data_path,
        v2_config=cfg_A,
        cache_dir=cache_dir,
    )

    # Sanity: the freshly-built cache is valid for its OWN config.
    valid_for_A = is_per_port_cache_valid(cache_dir, cfg_A, data_path=data_path)
    if not valid_for_A:
        failures.append(
            "setup sanity failed: freshly-built cache not valid for its own "
            "config A (repro environment problem, not the bug)"
        )

    # cfg_B == cfg_A plus ONE non-dict table appended. This is a STRUCTURALLY
    # DIFFERENT on-disk config (it carries an extra tables[] element).
    cfg_B = _clean_config(data_filename)
    cfg_B["tables"].append("malformed-extra-table")

    valid_for_B = is_per_port_cache_valid(cache_dir, cfg_B, data_path=data_path)
    print(f"is_per_port_cache_valid(cache_for_A, cfg_B) = {valid_for_B}")

    # The bug: cfg_B differs from cfg_A (extra malformed table) yet the cache
    # built for A is judged FRESH for B -> stale parquet served.
    if valid_for_B is not True:
        failures.append(
            f"EXPECTED validity trapdoor to judge cfg_B fresh (True) but got "
            f"{valid_for_B!r}"
        )

    # ------------------------------------------------------------------
    # Part 3 — confirm validate_v2_schema WOULD reject cfg_B on the build
    # path, proving the collision only bites because validity skips it.
    # ------------------------------------------------------------------
    rejected = False
    try:
        validate_v2_schema(cfg_B)
    except ApiInputSchemaError as exc:
        rejected = True
        print(f"validate_v2_schema(cfg_B) correctly raised: {exc}")
    if not rejected:
        failures.append(
            "EXPECTED validate_v2_schema(cfg_B) to raise on the non-dict table "
            "(this underpins the 'trapdoor skips validation' argument)"
        )

    # ------------------------------------------------------------------
    print()
    if failures:
        print("REPRO RESULT: claim NOT reproduced — discrepancies:")
        for f in failures:
            print(f"  - {f}")
        return 1

    print("REPRO RESULT: claim REPRODUCED.")
    print(
        "  - clean / +str-table / +str-column / +'garbage' / +12345 all hash "
        f"to the SAME fingerprint ({fp_clean})."
    )
    print(
        "  - is_per_port_cache_valid judged a cache built for cfg_A FRESH for "
        "structurally-different cfg_B (cfg_A + a non-dict table)."
    )
    print(
        "  - validate_v2_schema WOULD have rejected cfg_B, but the validity "
        "trapdoor never calls it."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
