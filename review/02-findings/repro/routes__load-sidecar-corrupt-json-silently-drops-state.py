"""Adversarial repro for claim:
load-sidecar-corrupt-json-silently-drops-state

Claim: load_sidecar swallows a corrupt .haute.json (JSONDecodeError/OSError/
ValueError/TypeError) to {} with only a warning, silently dropping node
positions, declared sources and active_source. parse_pipeline_to_graph then
applies NO positions, resets sources to ['live'] and active_source to 'live'.
This is asymmetric with pipeline_dir(), which RAISES ConfigError on a
malformed haute.toml.

This script asserts on the specific WRONG behaviour/value:
  1. load_sidecar(corrupt) returns {} and does NOT raise.
  2. parse_pipeline_to_graph over a pipeline whose sidecar declared a
     non-default source + active_source returns a graph with sources==['live']
     and active_source=='live' (the user's declared source selection is
     silently dropped) and applies NO custom node position.
  3. Contrast: pipeline_dir() over a malformed haute.toml RAISES ConfigError.

Isolation: all disk I/O via tempfile; project root set via
haute._sandbox.set_project_root(tmp); no real project files touched.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import haute._sandbox as _sandbox
from haute.routes._helpers import (
    load_sidecar,
    parse_pipeline_to_graph,
    pipeline_dir,
)
from haute.errors import ConfigError


def main() -> None:
    failures: list[str] = []

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td).resolve()
        _sandbox.set_project_root(tmp)

        # ------------------------------------------------------------------
        # Build a minimal but VALID pipeline .py with a single transform node.
        # The parser sanitises the function/label name; we only need the
        # node to exist so we can show its position is NOT restored.
        # ------------------------------------------------------------------
        py_path = tmp / "pipeline.py"
        py_path.write_text(
            'import haute\n'
            'pipeline = haute.Pipeline("main")\n'
            '\n'
            '@pipeline.polars\n'
            'def step_one(df):\n'
            '    return df\n',
            encoding="utf-8",
        )

        # First, parse with NO sidecar to learn the parser's DEFAULT node
        # positions and default source state. This is the baseline we will
        # compare against to prove the corrupt-sidecar path silently reverts
        # to defaults.
        baseline = parse_pipeline_to_graph(py_path)
        baseline_positions = {n.id: (n.position.get("x"), n.position.get("y")) for n in baseline.nodes}
        node_ids = list(baseline_positions.keys())
        print(f"[info] parsed node ids: {node_ids}")
        print(f"[info] baseline default positions: {baseline_positions}")
        print(f"[info] baseline sources={baseline.sources} active={baseline.active_source}")

        if not node_ids:
            print("[setup-error] parser produced no nodes; cannot demonstrate position drop")
            print("REPRO RESULT: SETUP-ERROR")
            return

        target_id = node_ids[-1]

        # ------------------------------------------------------------------
        # CASE A — sanity: a VALID sidecar IS honoured (positions + sources
        # applied). This proves the merge path works when JSON is well-formed,
        # so the later silent loss is specifically due to corruption, not a
        # broken merge.
        # ------------------------------------------------------------------
        custom_pos = {"x": 4242.0, "y": 9999.0}
        valid_payload = {
            "positions": {target_id: custom_pos},
            "sources": ["live", "snapshot_2024"],
            "active_source": "snapshot_2024",
        }
        sidecar = py_path.with_suffix(".haute.json")
        sidecar.write_text(json.dumps(valid_payload), encoding="utf-8")

        g_valid = parse_pipeline_to_graph(py_path)
        valid_target = next(n for n in g_valid.nodes if n.id == target_id)
        valid_ok = (
            valid_target.position.get("x") == 4242.0
            and valid_target.position.get("y") == 9999.0
            and "snapshot_2024" in g_valid.sources
            and g_valid.active_source == "snapshot_2024"
        )
        print(
            f"[caseA valid sidecar] target pos={valid_target.position} "
            f"sources={g_valid.sources} active={g_valid.active_source} -> honoured={valid_ok}"
        )
        if not valid_ok:
            failures.append(
                "CASE A: valid sidecar was NOT honoured; cannot cleanly attribute "
                "case-B loss to corruption."
            )

        # ------------------------------------------------------------------
        # CASE B — THE BUG: overwrite the SAME sidecar with corrupt JSON
        # (simulating a torn/partial write from external tooling). The user
        # previously had a custom layout + a selected non-default source.
        # ------------------------------------------------------------------
        sidecar.write_text('{ "positions": { "' + target_id + '": {"x": 4242.0, "y": ',
                           encoding="utf-8")  # truncated mid-write

        # 1) load_sidecar must NOT raise and must return {}
        raised = None
        try:
            raw = load_sidecar(py_path)
        except Exception as exc:  # noqa: BLE001
            raised = exc
            raw = None

        if raised is not None:
            print(f"[caseB] load_sidecar RAISED {type(raised).__name__}: {raised}")
            failures.append("CASE B: expected load_sidecar to swallow to {} but it RAISED.")
        else:
            print(f"[caseB] load_sidecar returned {raw!r} (no exception)")
            if raw != {}:
                failures.append(f"CASE B: expected load_sidecar()=={{}} but got {raw!r}")

        # 2) parse_pipeline_to_graph must silently revert to defaults
        g_corrupt = parse_pipeline_to_graph(py_path)
        corrupt_target = next(n for n in g_corrupt.nodes if n.id == target_id)
        corrupt_pos = (corrupt_target.position.get("x"), corrupt_target.position.get("y"))
        print(
            f"[caseB corrupt sidecar] target pos={corrupt_target.position} "
            f"sources={g_corrupt.sources} active={g_corrupt.active_source}"
        )

        # The custom position must have been DROPPED (reverted to the parser
        # default we recorded in baseline_positions).
        baseline_pos = baseline_positions[target_id]
        if corrupt_pos == (4242.0, 9999.0):
            failures.append(
                "CASE B: custom position survived corruption (claim predicts it is dropped)."
            )
        elif corrupt_pos != baseline_pos:
            failures.append(
                f"CASE B: position neither custom nor default: {corrupt_pos} "
                f"(baseline default {baseline_pos})"
            )
        else:
            print(
                f"[caseB] PASS: custom position {custom_pos} silently dropped; "
                f"reverted to parser default {baseline_pos}"
            )

        # sources must have reverted to ['live'] and active to 'live'.
        if g_corrupt.sources != ["live"]:
            failures.append(
                f"CASE B: expected sources==['live'] after corruption, got {g_corrupt.sources}"
            )
        else:
            print("[caseB] PASS: declared sources silently reverted to ['live']")
        if g_corrupt.active_source != "live":
            failures.append(
                f"CASE B: expected active_source=='live' after corruption, got {g_corrupt.active_source!r}"
            )
        else:
            print("[caseB] PASS: active_source silently reverted to 'live'")

        # ------------------------------------------------------------------
        # CASE C — asymmetry: pipeline_dir() over a malformed haute.toml RAISES
        # ConfigError. Demonstrates the inconsistent failure posture.
        # pipeline_dir reads haute.toml from CWD, so run it from inside tmp.
        # ------------------------------------------------------------------
        import os

        prev_cwd = Path.cwd()
        toml_raises = None
        try:
            (tmp / "haute.toml").write_text("this is = not valid = toml [[[", encoding="utf-8")
            os.chdir(tmp)
            pipeline_dir.cache_clear()  # lru_cache(maxsize=1)
            try:
                pdir = pipeline_dir()
                print(f"[caseC] pipeline_dir() returned {pdir} (did NOT raise)")
            except ConfigError as exc:
                toml_raises = exc
                print(f"[caseC] pipeline_dir() RAISED ConfigError: {exc}")
            except Exception as exc:  # noqa: BLE001
                print(f"[caseC] pipeline_dir() raised {type(exc).__name__}: {exc}")
        finally:
            os.chdir(prev_cwd)
            pipeline_dir.cache_clear()

        if toml_raises is None:
            failures.append(
                "CASE C: expected pipeline_dir() to raise ConfigError on malformed haute.toml; "
                "asymmetry not demonstrated."
            )
        else:
            print("[caseC] PASS: pipeline_dir raises ConfigError — asymmetric with load_sidecar's silent {}")

    # ----------------------------------------------------------------------
    print("\n================ SUMMARY ================")
    if failures:
        for f in failures:
            print(f"  FAIL: {f}")
        print("\nREPRO RESULT: NOT-REPRODUCED (claim behaviour did not hold as predicted)")
        raise SystemExit(1)
    print("  All predicted behaviours observed.")
    print("\nREPRO RESULT: REPRODUCED")
    print("  - load_sidecar(corrupt) -> {} (no exception)")
    print("  - parse_pipeline_to_graph silently dropped custom position, sources, active_source")
    print("  - pipeline_dir(malformed toml) -> raises ConfigError (asymmetric)")


if __name__ == "__main__":
    main()
