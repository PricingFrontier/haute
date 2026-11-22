"""IOL-08: a temporal value reaches Python only as its exact text.

Python's ``datetime``, ``time`` and ``timedelta`` hold microseconds; a Polars
``Datetime("ns")``, a ``Duration("ns")`` and every ``Time`` hold nanoseconds. So every
place Haute's own code reads a frame's rows into Python objects (``to_dicts``,
``iter_rows``, ``rows``, ``row``) either reads a frame written through
``haute._temporal_text.with_temporal_text`` in the same function, or is listed below
with why its frame holds no datetime, time or duration column. A new reader fails this
test until it does one or the other, so the digits are not lost at the next site.
"""

from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path

SOURCE = Path(__file__).resolve().parent.parent / "src" / "haute"
_ROW_READERS = frozenset({"to_dicts", "iter_rows", "rows", "row"})
_EXACT = "with_temporal_text"

#: (module path under src/haute, enclosing function) -> (row reads there, why its frame
#: holds no temporal column).
COUNTS = "counts, sums or byte sizes only"
READERS_WITHOUT_TEMPORAL_COLUMNS: dict[tuple[str, str], tuple[int, str]] = {
    ("_binning.py", "equal_width_bins"): (1, "a bin number and its count"),
    ("_frame_profile.py", "_build_frame_stats"): (
        1,
        "a temporal aggregate is written by temporal_text inside the query",
    ),
    ("_frame_profile.py", "_build_histograms"): (1, "bin edges of a numeric column, counts"),
    ("_graph_walker.py", "_Walk._collect_row"): (1, "a measurement aggregate: " + COUNTS),
    ("_model_explainability.py", "_single_glm_contribution_record"): (
        1,
        "a GLM's numeric term contributions",
    ),
    ("_polars_dtypes.py", "_integer_range"): (1, "an integer column's bounds"),
    ("_trace_correlation.py", "_identical_candidate_count"): (1, COUNTS),
    ("_workbench_output.py", "workbench_response.produce"): (
        2,
        "a workbench column is int, float, str, bool or date (_workbench_form.ColumnType)",
    ),
    ("deploy/_impact.py", "_segment_breakdown"): (
        1,
        "String segment columns with float means and counts",
    ),
    ("deploy/_scorer.py", "request_records"): (
        1,
        "a request's own records, for readers that infer each value's type; a JSON request "
        "already arrives as text",
    ),
    ("routes/_banding_stats.py", "BandingStatsService._with_rule_counts"): (
        1,
        "a rule's index and its count",
    ),
    ("routes/_explore_relationships.py", "ExploreRelationshipsService._numeric_levels"): (
        1,
        "levels of a numeric feature only (dtype.is_numeric())",
    ),
    ("routes/_frontier_point_summary.py", "frontier_point_rows"): (
        1,
        "a frontier point's numeric objective and constraint totals",
    ),
    ("routes/_optimiser_input.py", "AutoRangeValueCheck.add"): (1, COUNTS),
    ("routes/_optimiser_input.py", "estimate_input_metrics"): (1, COUNTS),
    ("routes/_optimiser_outcomes.py", "_key_fingerprint"): (1, "integer hashes and counts"),
    ("routes/_optimiser_outcomes.py", "_require_constant_within_quote"): (1, COUNTS),
    ("routes/_optimiser_outcomes.py", "write_quote_analysis"): (1, COUNTS),
    ("routes/_optimiser_segments.py", "AnalysisSegments._numeric"): (
        2,
        "bins of a numeric analysis column only (NUMBER_KINDS)",
    ),
    ("routes/_optimiser_segments.py", "_categorical_levels"): (
        1,
        "a level is shown by its label, written by _level_text; the row holds its bucket and sums",
    ),
    ("routes/_optimiser_service.py", "_ScenarioFrontierRangeAccumulator._carry_batch"): (
        1,
        "numeric sums",
    ),
    ("routes/_optimiser_service.py", "_ScenarioFrontierRangeAccumulator._fallback_totals"): (
        1,
        "numeric sums",
    ),
    ("routes/_optimiser_service.py", "_ScenarioFrontierRangeAccumulator._row_values"): (
        1,
        "numeric extrema",
    ),
    ("routes/_optimiser_solver.py", "frontier_point_history_payload"): (
        1,
        "the solver's numeric iteration trace",
    ),
    ("trace.py", "_assemble_steps"): (
        1,
        "_TypedRows.row, the trace's own reader, which reads through _traced_row",
    ),
    ("trace.py", "_typed_input_row"): (
        1,
        "_TypedRows.row, the trace's own reader, which reads through _traced_row",
    ),
}


def _readers() -> Counter[tuple[str, str]]:
    found: Counter[tuple[str, str]] = Counter()
    for path in sorted(SOURCE.rglob("*.py")):
        relative = path.relative_to(SOURCE).as_posix()
        if relative == "_temporal_text.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        _visit(tree, relative, "<module>", found)
    return found


def _visit(node: ast.AST, relative: str, scope: str, found: Counter[tuple[str, str]]) -> None:
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
            inner = child.name if scope == "<module>" else f"{scope}.{child.name}"
            if not _calls(child, _EXACT):
                for call in _own_calls(child):
                    found[(relative, inner)] += 1
            _visit(child, relative, inner, found)
        elif isinstance(child, ast.ClassDef):
            _visit(
                child,
                relative,
                child.name if scope == "<module>" else f"{scope}.{child.name}",
                found,
            )
        elif scope == "<module>":
            _visit(child, relative, scope, found)


def _own_calls(function: ast.AST) -> list[ast.Call]:
    """The row reads in *function* itself, not in the functions it defines."""
    calls: list[ast.Call] = []
    stack = list(ast.iter_child_nodes(function))
    while stack:
        node = stack.pop()
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Lambda):
            continue
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in _ROW_READERS
        ):
            calls.append(node)
        stack.extend(ast.iter_child_nodes(node))
    return calls


def _calls(function: ast.AST, name: str) -> bool:
    return any(
        isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Name) and node.func.id == name)
            or (isinstance(node.func, ast.Attribute) and node.func.attr == name)
        )
        for node in ast.walk(function)
    )


def test_every_row_read_writes_temporal_columns_as_text_or_says_why_it_holds_none() -> None:
    found = _readers()
    unlisted = {
        site: count for site, count in found.items() if site not in READERS_WITHOUT_TEMPORAL_COLUMNS
    }
    stale = {
        site: listed
        for site, (listed, _reason) in READERS_WITHOUT_TEMPORAL_COLUMNS.items()
        if found.get(site, 0) != listed
    }
    assert not unlisted, (
        "These functions read a frame's rows into Python without with_temporal_text: "
        "write the frame through it first, or list the site with why its frame holds no "
        f"datetime, time or duration column: {sorted(unlisted.items())}"
    )
    assert not stale, f"These listed sites now read a different number of rows: {stale}"
