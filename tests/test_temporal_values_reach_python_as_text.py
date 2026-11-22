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
        "a frame request's records for readers that type each value: a value below a "
        "microsecond is refused first (first_value_finer_than_microseconds)",
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
        "reads only each level's bucket and sums; the level itself is shown by its label, "
        "written by _level_text",
    ),
    ("assistant/_tools.py", "_column_profile"): (
        1,
        "levels are listed for text and boolean columns only (_PROFILABLE_LEVEL_KINDS)",
    ),
    ("modelling/_metrics.py", "_ave_categorical_bins"): (
        1,
        "the category column is written through with_temporal_text before its frame is built",
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
    """Each function's row reads whose frame is not written through ``with_temporal_text``."""
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
        if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            inner = child.name if scope == "<module>" else f"{scope}.{child.name}"
            if not isinstance(child, ast.ClassDef):
                for call in _own_calls(child):
                    assert isinstance(call.func, ast.Attribute)
                    if not _exact(call.func.value):
                        found[(relative, inner)] += 1
            _visit(child, relative, inner, found)
        elif scope == "<module>":
            _visit(child, relative, scope, found)


def _own_nodes(function: ast.AST) -> list[ast.AST]:
    """The nodes of *function* itself, not of the functions or classes it defines."""
    nodes: list[ast.AST] = []
    stack = list(ast.iter_child_nodes(function))
    while stack:
        node = stack.pop()
        # A lambda's body is its function's: its reads are counted there.
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            continue
        nodes.append(node)
        stack.extend(ast.iter_child_nodes(node))
    return nodes


def _own_calls(function: ast.AST) -> list[ast.Call]:
    """The row reads in *function* itself."""
    return [
        node
        for node in _own_nodes(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in _ROW_READERS
    ]


def _is_exact_call(node: ast.AST) -> bool:
    return isinstance(node, ast.Call) and (
        (isinstance(node.func, ast.Name) and node.func.id == _EXACT)
        or (isinstance(node.func, ast.Attribute) and node.func.attr == _EXACT)
    )


#: Frame methods that can add no column, so a chain through them keeps what
#: ``with_temporal_text`` wrote; any other method (a join, a select, ``with_columns``) can
#: bring a temporal column back, and ends the guarantee.
_KEEPS_COLUMNS = frozenset(
    {"head", "tail", "slice", "limit", "sort", "filter", "drop_nulls", "unique", "drop"}
)


def _exact(node: ast.AST) -> bool:
    """Whether *node*, a read's frame, is written through ``with_temporal_text`` at the read
    itself: the call, or a chain on it of methods that add no column
    (``with_temporal_text(frame).head(5)``). A name never is: between its assignment and the
    read, anything may change its columns (``insert_column``, a reassignment)."""
    while True:
        if _is_exact_call(node):
            return True
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in _KEEPS_COLUMNS
        ):
            node = node.func.value
            continue
        return False


def test_the_guard_sees_through_no_read_that_only_looks_exact() -> None:
    """Codex review: a chain that can add a column, a name read before its conversion or
    changed after it, and a read inside a lambda are each reported."""
    source = """
def joined(left, right):
    return with_temporal_text(left).join(right, on="id").to_dicts()

def read_before(frame):
    rows = frame.to_dicts()
    frame = with_temporal_text(frame)
    return rows

def selected(frame):
    return with_temporal_text(frame).select("at").to_dicts()

def mutated(frame, timestamps):
    exact = with_temporal_text(frame)
    exact.insert_column(0, timestamps)
    return exact.to_dicts()

def in_a_lambda(frames):
    return list(map(lambda frame: frame.to_dicts(), frames))

def kept(frame):
    return with_temporal_text(frame).head(5).to_dicts(), with_temporal_text(frame).rows()
"""
    found: Counter[tuple[str, str]] = Counter()
    _visit(ast.parse(source), "example.py", "<module>", found)
    assert dict(found) == {
        ("example.py", "joined"): 1,
        ("example.py", "read_before"): 1,
        ("example.py", "selected"): 1,
        ("example.py", "mutated"): 1,
        ("example.py", "in_a_lambda"): 1,
    }


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
