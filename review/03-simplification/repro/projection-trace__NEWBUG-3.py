"""Isolated reproduction for NEWBUG-3.

CLAIM: In ``_trace_enrichment._build_input_sources`` (lines ~1094-1153), when the
upstream ``other_step`` is a *banding* node, the branch at 1094-1110 builds
banding-specific lineage (``expression_text`` / ``substituted_text`` /
``result_value`` / ``parsed_refs``) from ``enrich_banding``.  The very next block
(1111-1153) re-reads ``raw = cfg.get("code")``; if the banding node's config
carries a non-empty ``code``, ``other_code`` is non-empty and
``parse_expression`` / ``evaluate_expression`` run UNCONDITIONALLY, overwriting
the band lineage with a generic single-row evaluation of the raw code.

This script drives the REAL ``_build_input_sources`` with synthetic in-memory
TraceStep / node_map objects.  No disk I/O, no rating/, no src/ or tests/ touched.

It runs two scenarios:

  * CONTROL  - banding node config has NO ``code``.  Band lineage is preserved.
               (proves the band branch works and that the repro harness is faithful)
  * BUG      - identical banding node, but config additionally carries a
               ``code`` field that produces a *different* expression/result.
               The band lineage is silently overwritten by the generic eval.

A correct implementation would show the same band lineage in BOTH scenarios
(the band mapping is the source of truth for a banding-produced column).
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

from haute._trace_correlation import SchemaDiff
from haute._trace_enrichment import _build_input_sources
from haute.trace import TraceStep


# --- Minimal stand-ins for the graph node objects that node_map holds. ------
# _build_input_sources only ever touches ``node_map[id].data.config``.
@dataclass
class _FakeNodeData:
    config: dict[str, Any]


@dataclass
class _FakeNode:
    data: _FakeNodeData


def _make_steps_and_node_map(banding_config: dict[str, Any]):
    """Build a 2-step trace: a banding node followed by a downstream consumer.

    The banding node turns ``age`` (=40) into ``age_band`` (="adult").  The
    downstream step references ``age_band`` so _build_input_sources will pick the
    banding step as the source for that column.
    """
    band_step = TraceStep(
        node_id="band1",
        node_name="Age banding",
        node_type="banding",
        schema_diff=SchemaDiff(
            columns_added=["age_band"],
            columns_removed=[],
            columns_modified=[],
            columns_passed=["age"],
        ),
        input_values={"age": 40},
        output_values={"age": 40, "age_band": "adult"},
    )
    downstream_step = TraceStep(
        node_id="rate1",
        node_name="Rate by band",
        node_type="ratingStep",
        schema_diff=SchemaDiff(
            columns_added=["premium"],
            columns_removed=[],
            columns_modified=[],
            columns_passed=["age", "age_band"],
        ),
        input_values={"age": 40, "age_band": "adult"},
        output_values={"age": 40, "age_band": "adult", "premium": 100.0},
    )
    all_steps = [band_step, downstream_step]
    node_map = {
        "band1": _FakeNode(_FakeNodeData(banding_config)),
        "rate1": _FakeNode(_FakeNodeData({})),
    }
    return all_steps, downstream_step, node_map


# Real multi-factor Haute banding config: categorical map age->band.
# (Shape consumed by enrich_banding / normalise_banding_factors.)
BASE_BANDING_CONFIG: dict[str, Any] = {
    "factors": [
        {
            "banding": "categorical",
            "column": "age",
            "outputColumn": "age_band",
            "rules": [
                {"key": 40, "value": "adult"},
                {"key": 10, "value": "child"},
            ],
            "default": "unknown",
        }
    ],
}


def _input_source_for_age_band(banding_config: dict[str, Any]) -> dict[str, Any]:
    all_steps, downstream_step, node_map = _make_steps_and_node_map(banding_config)
    sources = _build_input_sources(
        ["age_band"],
        downstream_step,
        all_steps,
        node_map,
        preamble_ns=None,
    )
    assert "age_band" in sources, f"banding step was not selected as source: {sources!r}"
    return sources["age_band"]


def main() -> None:
    # ---- CONTROL: no code on the banding node ------------------------------
    control_cfg = copy.deepcopy(BASE_BANDING_CONFIG)
    control = _input_source_for_age_band(control_cfg)
    print("CONTROL (no code):")
    print("  expression_text =", repr(control.get("expression_text")))
    print("  substituted_text =", repr(control.get("substituted_text")))
    print("  result_value     =", repr(control.get("result_value")))

    # The band branch must have produced band lineage (proves harness fidelity).
    assert control.get("result_value") == "adult", (
        "harness setup error: band branch did not produce the banded value; "
        f"got result_value={control.get('result_value')!r}"
    )
    control_expr = control.get("expression_text") or ""
    assert "age" in control_expr, (
        "harness setup error: band expression_text missing input column; "
        f"got {control_expr!r}"
    )
    # The band substitution renders the input->band mapping, not raw polars code.
    assert "->" in (control.get("substituted_text") or ""), (
        "harness setup error: band substituted_text not in mapping form; "
        f"got {control.get('substituted_text')!r}"
    )

    # ---- BUG: same banding node, but config carries a 'code' field ---------
    # A persisted graph node config is a free-form dict[str, Any] (see
    # haute._types.NodeData.config); the frontend/GUI can store an arbitrary
    # 'code' key on a banding node.  That code computes a DIFFERENT value
    # (age * 99 = 3960) so any overwrite of the band lineage is unmistakable.
    bug_cfg = copy.deepcopy(BASE_BANDING_CONFIG)
    bug_cfg["code"] = "df = df.with_columns((pl.col('age') * 99).alias('age_band'))"
    bug = _input_source_for_age_band(bug_cfg)
    print("\nBUG (config also carries 'code'):")
    print("  expression_text =", repr(bug.get("expression_text")))
    print("  substituted_text =", repr(bug.get("substituted_text")))
    print("  result_value     =", repr(bug.get("result_value")))

    # If the band lineage were respected, these would match CONTROL exactly.
    # Instead the generic parse/eval block (1123-1153) clobbers all three.
    assert bug.get("expression_text") == control.get("expression_text"), (
        "BUG CONFIRMED: banding expression_text was OVERWRITTEN by the generic "
        f"code parse.\n  band lineage : {control.get('expression_text')!r}\n"
        f"  shown instead: {bug.get('expression_text')!r}"
    )
    # (Unreachable if the bug is present — assertion above fires first.)
    assert bug.get("result_value") == "adult", (
        f"BUG: banding result_value overwritten -> {bug.get('result_value')!r}"
    )

    print("\nNo overwrite detected - claim REFUTED.")


if __name__ == "__main__":
    main()
