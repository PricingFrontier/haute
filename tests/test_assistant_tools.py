"""Tests for the assistant read tools (``haute.assistant._tools``), chiefly
``get_node_schema``.

Spec: specs/assistant/low-level.md — Control flow step 5 and Edge cases.
``get_node_schema(source_file, node)`` parses the saved pipeline, validates
the target id against the ORIGINAL hierarchical graph (submodel placeholder
or submodel-internal id → boundary error; nowhere → unknown-node error),
then reproduces the production graph preparation (flatten → compile preamble
→ ``execute_lazy_graph`` with the graph's active source) and reads the
schema without collecting anything.  Results are structured payloads —
``{"node", "columns": [{"name", "dtype"}]}`` for single-frame nodes,
``{"node", "ports": {port: [...]}}`` for multi-frame sources, and
``{"error": {"code", "message"}}`` for failures — tools never raise into
the loop.

Seam pinned for patchability: ``_tools`` imports
``parse_pipeline_to_graph`` (from ``haute.routes._helpers``) and
``execute_lazy_graph`` (from ``haute.execution``) at module level, so tests
patch ``haute.assistant._tools.<name>``.

Authored test-first per CLAUDE.md TDD.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from haute._types import GraphNode, NodeData, PipelineGraph

# ---------------------------------------------------------------------------
# Fixtures — a real tmp project with a parquet source
# ---------------------------------------------------------------------------


PIPELINE_SOURCE = '''\
import polars as pl

import haute

def add_flag(frame: pl.LazyFrame) -> pl.LazyFrame:
    return frame.with_columns(flag=pl.lit(1))


pipeline = haute.Pipeline("main", description="schema tool fixture")


@pipeline.polars
def quotes() -> pl.LazyFrame:
    """Read the quote rows."""

    return pl.scan_parquet("data/quotes.parquet")


@pipeline.polars
def enriched(quotes: pl.LazyFrame) -> pl.LazyFrame:
    """Derive, rename, and drop columns so the schema visibly changes."""

    return add_flag(
        quotes.rename({"vehicle_year": "year"}).drop("notes")
    ).with_columns(age=2026 - pl.col("year"))
'''


@pytest.fixture()
def project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    pl.DataFrame(
        {
            "quote_id": ["q1", "q2"],
            "vehicle_year": [2019, 2021],
            "notes": ["a", "b"],
        }
    ).write_parquet(tmp_path / "data" / "quotes.parquet")
    (tmp_path / "main.py").write_text(PIPELINE_SOURCE, encoding="utf-8")
    (tmp_path / "haute.toml").write_text(
        '[assistant]\nprovider = "openai"\nmodel = "test"\n'
        'base_url = "https://api.openai.com/v1"\n'
        '[assistant.egress]\ntrust = "organization"\nmax_sensitivity = "restricted"\n'
        "allow_project_knowledge = false\nallow_executable_source = false\n"
        "allow_row_samples = false\n",
        encoding="utf-8",
    )
    return tmp_path


def _columns(result: dict) -> dict[str, str]:
    assert "columns" in result, result
    return {column["name"]: column["dtype"] for column in result["columns"]}


# ---------------------------------------------------------------------------
# End-to-end schema resolution (no mocks)
# ---------------------------------------------------------------------------


class TestGetNodeSchemaEndToEnd:
    def test_source_node_schema_matches_file(self, project_root: Path):
        from haute.assistant._tools import get_node_schema

        result = get_node_schema("main.py", "quotes")
        cols = _columns(result)
        assert cols == {
            "quote_id": "String",
            "vehicle_year": "Int64",
            "notes": "String",
        }

    def test_downstream_node_reflects_transforms_and_preamble(self, project_root: Path):
        """Rename, drop, derived column, and the preamble-defined helper all
        resolve — proving flatten/preamble/engine preparation is wired."""

        from haute.assistant._tools import get_node_schema

        result = get_node_schema("main.py", "enriched")
        cols = _columns(result)
        assert "year" in cols and "vehicle_year" not in cols
        assert "notes" not in cols
        assert "age" in cols
        assert "flag" in cols  # created by the preamble helper add_flag

    def test_unknown_node_is_structured_error(self, project_root: Path):
        from haute.assistant._tools import get_node_schema

        result = get_node_schema("main.py", "ghost")
        assert result["error"]["code"] == "unknown_node"
        assert "ghost" in result["error"]["message"]

    def test_result_reports_each_input_by_its_code_visible_name(self, project_root: Path):
        """Authoring code against a node needs the columns arriving on it, not
        only the columns leaving it."""

        from haute.assistant._tools import get_node_schema

        result = get_node_schema("main.py", "enriched")
        inputs = result["inputs"]
        assert set(inputs) == {"quotes"}
        assert {column["name"] for column in inputs["quotes"]} == {
            "quote_id",
            "vehicle_year",
            "notes",
        }

    def test_source_node_without_inputs_omits_the_inputs_key(self, project_root: Path):
        from haute.assistant._tools import get_node_schema

        assert "inputs" not in get_node_schema("main.py", "quotes")


# ---------------------------------------------------------------------------
# Authoring states the tool must answer usefully rather than refuse
# ---------------------------------------------------------------------------


EMPTY_TRANSFORM_SOURCE = '''\
import polars as pl

import haute

pipeline = haute.Pipeline("main", description="empty transform fixture")


@pipeline.polars
def left() -> pl.LazyFrame:
    return pl.scan_parquet("data/quotes.parquet")


@pipeline.polars
def right() -> pl.LazyFrame:
    return pl.scan_parquet("data/quotes.parquet")


@pipeline.polars
def combined(left: pl.LazyFrame, right: pl.LazyFrame) -> pl.LazyFrame:
    """"""
    raise NotImplementedError(
        "This transform has no code yet. Add code that defines what it returns.",
    )
'''


GROUP_BY_SOURCE = """\
import polars as pl

import haute

pipeline = haute.Pipeline("main", description="group-by fixture")


@pipeline.polars
def quotes() -> pl.LazyFrame:
    return pl.scan_parquet("data/quotes.parquet")


@pipeline.polars
def by_year(quotes: pl.LazyFrame) -> pl.LazyFrame:
    return quotes.group_by("vehicle_year").agg(pl.len().alias("quote_count"))
"""


PROFILE_SOURCE = '''\
import polars as pl

import haute

pipeline = haute.Pipeline("main", description="value profile fixture")


@pipeline.polars
def claims() -> pl.LazyFrame:
    return pl.scan_parquet("data/claims.parquet")


@pipeline.polars
def totals(claims: pl.LazyFrame) -> pl.LazyFrame:
    """"""
    df = claims
    return df
'''


JOIN_PROFILE_SOURCE = """\
import polars as pl

import haute

pipeline = haute.Pipeline("main", description="join and aggregation profile fixture")


@pipeline.polars
def claims() -> pl.LazyFrame:
    return pl.scan_parquet("data/claims.parquet")


@pipeline.polars
def policies() -> pl.LazyFrame:
    return pl.scan_parquet("data/policies.parquet")


@pipeline.polars
def joined(claims: pl.LazyFrame, policies: pl.LazyFrame) -> pl.LazyFrame:
    return claims.join(policies, on="quote_id", how="left")


@pipeline.polars
def by_fault(joined: pl.LazyFrame) -> pl.LazyFrame:
    return joined.group_by("fault").agg(pl.len().alias("claim_count"))
"""


@pytest.fixture()
def profile_project(project_root: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A project whose categorical encoding cannot be guessed from its name."""

    pl.DataFrame(
        {
            "quote_id": [f"q{index}" for index in range(60)],
            "fault": ["at_fault", "not_at_fault", "pending"] * 20,
            "amount_paid": [float(index) for index in range(60)],
        }
    ).write_parquet(project_root / "data" / "claims.parquet")
    (project_root / "main.py").write_text(PROFILE_SOURCE, encoding="utf-8")

    import haute.assistant._tools as tools_module
    from haute.assistant._config import EgressPolicy

    def allowing(root: Path) -> EgressPolicy:
        return EgressPolicy(
            trust="organization",
            max_sensitivity="restricted",
            allow_project_knowledge=True,
            allow_executable_source=True,
            allow_row_samples=True,
        )

    monkeypatch.setattr(tools_module, "resolve_egress_policy", allowing)
    return project_root


@pytest.fixture()
def dtype_matrix_project(project_root: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A claims frame carrying every dtype family a profile must render.

    Dates, money, and a column literally named `count` are the ordinary shape
    of the data this tool exists to describe, not exotic edge cases.
    """

    pl.DataFrame(
        {
            "accident_date": [date(2024, 1, 1 + index) for index in range(6)],
            "settled_at": [datetime(2024, 1, 1 + index) for index in range(6)],
            "paid": pl.Series(
                [Decimal(f"{index}.50") for index in range(6)], dtype=pl.Decimal(10, 2)
            ),
            "excess": [float(index) for index in range(6)],
            "exposure": [1.0, float("inf"), 2.0, 3.0, 4.0, 5.0],
            "count": ["one", "two", "two", "one", "one", "one"],
        }
    ).write_parquet(project_root / "data" / "claims.parquet")
    (project_root / "main.py").write_text(PROFILE_SOURCE, encoding="utf-8")

    import haute.assistant._tools as tools_module
    from haute.assistant._config import EgressPolicy

    monkeypatch.setattr(
        tools_module,
        "resolve_egress_policy",
        lambda _root: EgressPolicy(
            trust="organization",
            max_sensitivity="restricted",
            allow_project_knowledge=True,
            allow_executable_source=True,
            allow_row_samples=True,
        ),
    )
    return project_root


@pytest.fixture()
def join_profile_project(profile_project: Path) -> Path:
    """Claims joined to policies, then aggregated: the realistic frames the
    engine refuses to materialise in-process without a native memory cap."""

    pl.DataFrame(
        {
            "quote_id": [f"q{index}" for index in range(60)],
            "region": ["north", "south"] * 30,
        }
    ).write_parquet(profile_project / "data" / "policies.parquet")
    (profile_project / "main.py").write_text(JOIN_PROFILE_SOURCE, encoding="utf-8")
    return profile_project


def _profile(source_file: str, node: str, input_name: str | None = None) -> dict[str, object]:
    from haute.assistant._tools import get_column_profiles

    return asyncio.run(get_column_profiles(source_file, node, input_name, session_id="test"))


def _profiles_by_name(result: dict[str, object]) -> dict[str, dict[str, object]]:
    assert "error" not in result, result
    return {column["name"]: column for column in result["columns"]}  # type: ignore[index,union-attr]


class TestColumnProfiles:
    def test_group_by_output_is_profiled_under_an_admitted_preview(
        self,
        project_root: Path,
        monkeypatch: pytest.MonkeyPatch,
        _widen_sandbox_root: None,
    ) -> None:
        config_dir = project_root / "config" / "data_input"
        config_dir.mkdir(parents=True)
        (config_dir / "quotes.json").write_text(
            json.dumps(
                {
                    "inputType": "file",
                    "format": "parquet",
                    "mode": "scan",
                    "path": str(project_root / "data" / "quotes.parquet"),
                    "arguments": {},
                }
            ),
            encoding="utf-8",
        )
        source = """\
import polars as pl

import haute

pipeline = haute.Pipeline("main", description="group-by fixture")


@pipeline.data_input(config="config/data_input/quotes.json")
def quotes(): ...


@pipeline.polars
def by_year(quotes: pl.LazyFrame) -> pl.LazyFrame:
    return quotes.group_by("vehicle_year").agg(pl.len().alias("quote_count"))
"""
        (project_root / "main.py").write_text(source, encoding="utf-8")
        import haute.assistant._tools as tools_module
        from haute.assistant._config import EgressPolicy

        monkeypatch.setattr(
            tools_module,
            "resolve_egress_policy",
            lambda _root: EgressPolicy(
                trust="organization",
                max_sensitivity="restricted",
                allow_project_knowledge=True,
                allow_executable_source=True,
                allow_row_samples=True,
            ),
        )

        result = _profile("main.py", "by_year")

        assert "error" not in result, result
        by_name = {column["name"]: column for column in result["columns"]}
        assert by_name["vehicle_year"]["min"] == 2019
        assert by_name["vehicle_year"]["max"] == 2021
        assert by_name["quote_count"]["min"] == 1
        assert by_name["quote_count"]["max"] == 1

    def test_small_cardinality_categories_are_reported_with_counts(self, profile_project: Path):
        """The encoding is the fact a schema cannot carry. Guessing `"Y"` here
        yields code that runs, validates, and counts nothing."""

        result = _profile("main.py", "totals", "claims")

        assert "error" not in result, result
        by_name = {column["name"]: column for column in result["columns"]}
        assert [entry["value"] for entry in by_name["fault"]["values"]] == [
            "at_fault",
            "not_at_fault",
            "pending",
        ]
        assert sum(entry["count"] for entry in by_name["fault"]["values"]) == 60
        assert result["rows_scanned"] == 60
        assert result["scan_bounded"] is False

    def test_high_cardinality_columns_withhold_their_values(self, profile_project: Path):
        """High-cardinality values are withheld to reduce unnecessary disclosure."""

        result = _profile("main.py", "totals", "claims")
        by_name = {column["name"]: column for column in result["columns"]}

        assert by_name["quote_id"]["values_withheld"] == "high_cardinality"
        assert "values" not in by_name["quote_id"]
        assert by_name["quote_id"]["distinct_count"] == 60

    def test_numeric_columns_report_bounds_rather_than_values(self, profile_project: Path):
        result = _profile("main.py", "totals", "claims")
        amount = {column["name"]: column for column in result["columns"]}["amount_paid"]

        assert (amount["min"], amount["max"]) == (0.0, 59.0)
        assert "values" not in amount

    def test_unknown_input_names_the_available_inputs(self, profile_project: Path):
        result = _profile("main.py", "totals", "ghost")

        assert result["error"]["code"] == "unknown_input"
        assert result["error"]["inputs"] == ["claims"]

    async def test_every_dtype_a_frame_can_carry_survives_the_executor(
        self, dtype_matrix_project: Path
    ):
        """The result is JSON-encoded twice before the model sees it — once to
        bound it, once by the provider adapter — and neither encoder accepts a
        `date`, `Decimal`, or `NaN`. A profile that cannot be encoded is not a
        degraded profile: the whole call fails as an opaque internal error, on
        exactly the date-and-money frames the tool exists to describe."""

        from haute.assistant._tools import build_tool_executor

        result = await build_tool_executor("main.py")(
            "get_column_profiles", {"node": "totals", "input": "claims"}
        )

        assert "error" not in result, result
        json.dumps(result, allow_nan=False)

    async def test_executor_holds_the_save_lock_while_profiling(
        self,
        profile_project: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        import haute.assistant._tools as tools_module

        async def observe_lock(*_args: object, **_kwargs: object) -> dict[str, object]:
            return {"save_lock_held": tools_module.save_lock.locked()}

        monkeypatch.setattr(tools_module, "get_column_profiles", observe_lock)

        result = await tools_module.build_tool_executor("main.py")(
            "get_column_profiles", {"node": "totals", "input": "claims"}
        )

        assert result["save_lock_held"] is True

    def test_frames_downstream_of_a_join_and_an_aggregation_profile_in_the_preview_worker(
        self, join_profile_project: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """In-process the engine refuses a join or group-by it cannot estimate,
        because nothing caps its memory there; the preview worker has that cap,
        so these ordinary frames profile under it."""

        from haute._interactive_workers import shutdown_interactive_worker_pool

        monkeypatch.setenv("HAUTE_INTERACTIVE_EXECUTION_MODE", "process")
        monkeypatch.setenv("HAUTE_INTERACTIVE_WORKER_COUNT", "1")
        # The route-isolation tests' policy: this exercises the worker route,
        # not native cap availability, which macOS lacks.
        monkeypatch.setenv("HAUTE_WORKER_MEMORY_ENFORCEMENT", "best_effort")
        # A spawned worker's project root is the directory it starts in.
        shutdown_interactive_worker_pool()
        try:
            joined = _profiles_by_name(_profile("main.py", "joined"))
            by_fault = _profiles_by_name(_profile("main.py", "by_fault"))
        finally:
            shutdown_interactive_worker_pool()

        assert joined["region"]["values"] == [
            {"value": "north", "count": 30},
            {"value": "south", "count": 30},
        ]
        # Each level is one aggregated row, so the counts tie and order is Polars'.
        assert sorted(entry["value"] for entry in by_fault["fault"]["values"]) == [
            "at_fault",
            "not_at_fault",
            "pending",
        ]
        assert (by_fault["claim_count"]["min"], by_fault["claim_count"]["max"]) == (20, 20)

    async def test_a_stopped_turn_cancels_its_profile(
        self, profile_project: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Stopping a turn cancels the tool call; the collection it started
        must stop too rather than run on after the turn has ended."""

        import haute.assistant._tools as tools_module

        running = threading.Event()
        stopped: list[str] = []

        def slow_profile(_frame: pl.LazyFrame, *, execution_context: object) -> dict[str, object]:
            running.set()
            deadline = time.monotonic() + 30
            while True:
                assert time.monotonic() < deadline
                try:
                    execution_context.checkpoint(label="profile")  # type: ignore[attr-defined]
                except Exception as exc:
                    stopped.append(type(exc).__name__)
                    raise
                time.sleep(0.01)

        monkeypatch.setattr(tools_module, "_profile_frame", slow_profile)
        call = asyncio.ensure_future(
            tools_module.build_tool_executor("main.py", session_id="stop")(
                "get_column_profiles", {"node": "totals", "input": "claims"}
            )
        )
        assert await asyncio.to_thread(running.wait, 30)
        call.cancel()

        with pytest.raises(asyncio.CancelledError):
            await call
        deadline = time.monotonic() + 30
        while not stopped:
            assert time.monotonic() < deadline
            await asyncio.sleep(0.01)
        assert stopped == ["ExecutionCancelledError"]

    async def test_a_stopped_turn_stops_its_profile_worker(
        self, profile_project: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """In the production mode the pool is told to stop the worker, and the
        call waits for it to stop before the cancellation reaches the turn."""

        import haute._interactive_workers as workers_module
        import haute.assistant._tools as tools_module

        monkeypatch.setenv("HAUTE_INTERACTIVE_EXECUTION_MODE", "process")
        running = threading.Event()
        reasons: list[str] = []

        class _Pool:
            def run(self, *_args: object, stop_reason: Any, **_kwargs: object) -> object:
                running.set()
                deadline = time.monotonic() + 30
                while (reason := stop_reason()) is None:
                    assert time.monotonic() < deadline
                    time.sleep(0.01)
                reasons.append(reason)
                raise workers_module.InteractiveWorkerStoppedError(reason)

        monkeypatch.setattr(workers_module, "interactive_worker_pool", _Pool)
        call = asyncio.ensure_future(
            tools_module.build_tool_executor("main.py", session_id="stop")(
                "get_column_profiles", {"node": "totals", "input": "claims"}
            )
        )
        assert await asyncio.to_thread(running.wait, 30)
        call.cancel()

        with pytest.raises(asyncio.CancelledError):
            await call
        assert reasons == ["cancelled"]

    def test_temporal_and_decimal_bounds_render_as_their_written_form(
        self, dtype_matrix_project: Path
    ):
        by_name = _profiles_by_name(_profile("main.py", "totals", "claims"))

        assert (by_name["accident_date"]["min"], by_name["accident_date"]["max"]) == (
            "2024-01-01",
            "2024-01-06",
        )
        assert by_name["settled_at"]["min"] == "2024-01-01 00:00:00"
        assert (by_name["paid"]["min"], by_name["paid"]["max"]) == ("0.50", "5.50")

    def test_numeric_bounds_keep_their_json_type(self, dtype_matrix_project: Path):
        """A bound the model compares against must stay a number."""

        by_name = _profiles_by_name(_profile("main.py", "totals", "claims"))

        assert (by_name["excess"]["min"], by_name["excess"]["max"]) == (0.0, 5.0)

    def test_a_non_finite_bound_is_rendered_rather_than_emitted_raw(
        self, dtype_matrix_project: Path
    ):
        """`Infinity` is a real Polars value and is not JSON: the bounding
        encoder runs under `allow_nan=False` and rejects it."""

        by_name = _profiles_by_name(_profile("main.py", "totals", "claims"))

        assert by_name["exposure"]["max"] == {"__haute_type__": "non_finite_float", "value": "inf"}
        assert by_name["exposure"]["min"] == 1.0

    def test_a_column_named_count_is_profiled_like_any_other(self, dtype_matrix_project: Path):
        """Polars refuses `value_counts` on a column already named `count`,
        and one refusal used to abort every other column in the frame."""

        by_name = _profiles_by_name(_profile("main.py", "totals", "claims"))

        assert [entry["value"] for entry in by_name["count"]["values"]] == ["one", "two"]

    def test_an_unsummarisable_column_withholds_only_itself(self):
        """`n_unique` raises on an Object column. Losing the whole frame's
        profile to one column the model never asked about is not a boundary,
        it is a defect — the column withholds its own values instead."""

        from haute.assistant._tools import _profile_frame

        frame = pl.DataFrame(
            {"fault": ["Y", "N", "Y"], "opaque": pl.Series("opaque", [object()] * 3)}
        ).lazy()

        by_name = {column["name"]: column for column in _profile_frame(frame)["columns"]}

        assert by_name["opaque"]["values_withheld"] == "unsupported_dtype"
        assert "distinct_count" not in by_name["opaque"]
        assert [entry["value"] for entry in by_name["fault"]["values"]] == ["Y", "N"]

    def test_policy_denies_profiles_when_row_samples_are_not_allowed(
        self, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Reading project data is the one thing this tool does, so the policy
        flag that names it is the gate."""

        import haute.assistant._tools as tools_module
        from haute.assistant._config import EgressPolicy

        monkeypatch.setattr(
            tools_module,
            "resolve_egress_policy",
            lambda _root: EgressPolicy(
                trust="organization",
                max_sensitivity="restricted",
                allow_project_knowledge=True,
                allow_executable_source=True,
                allow_row_samples=False,
            ),
        )

        result = _profile("main.py", "enriched")

        assert result["error"]["code"] == "egress_policy_denied"
        assert result["error"]["required_policy"] == "allow_row_samples"


class TestExecutableSourcePolicy:
    @pytest.mark.parametrize("allowed", [True, False])
    def test_node_code_follows_the_executable_source_policy(
        self, project_root: Path, monkeypatch: pytest.MonkeyPatch, allowed: bool
    ):
        """The flag was parsed, reported, and then ignored, so a project that
        granted access still could not read the code it was editing."""

        import haute.assistant._tools as tools_module
        from haute.assistant._config import EgressPolicy

        monkeypatch.setattr(
            tools_module,
            "resolve_egress_policy",
            lambda _root: EgressPolicy(
                trust="organization",
                max_sensitivity="restricted",
                allow_project_knowledge=True,
                allow_executable_source=allowed,
                allow_row_samples=False,
            ),
        )

        config = tools_module.get_node_config("main.py", "enriched")["config"]

        if allowed:
            assert "rename" in config["code"]
        else:
            assert config["code"] == "<redacted: executable_source>"


class TestUnresolvableButInspectableNodes:
    def test_empty_transform_reports_its_inputs_and_a_stable_reason(self, project_root: Path):
        """An authored-but-empty transform is the state an analyst is in when
        they ask the assistant to write that code. The node's own output is
        genuinely unresolvable, but refusing outright left no way to discover
        the columns needed to write it."""

        (project_root / "main.py").write_text(EMPTY_TRANSFORM_SOURCE, encoding="utf-8")
        from haute.assistant._tools import get_node_schema

        result = get_node_schema("main.py", "combined")

        assert "error" not in result, result
        assert result["unresolved_reason"] == "node_has_no_code"
        assert "columns" not in result and "ports" not in result
        assert set(result["inputs"]) == {"left", "right"}
        assert {column["name"] for column in result["inputs"]["left"]} == {
            "quote_id",
            "vehicle_year",
            "notes",
        }

    def test_palette_default_stepped_nodes_answer_by_their_surface(self, project_root: Path):
        """A palette Transform's ``steps: []`` has not chosen its input, so it is
        awaiting its code exactly like a code-less one; a frame-start node at
        ``steps: []`` simply passes its frame through."""

        from haute._config_io import palette_default_config
        from haute._pipeline_recovery import load_pipeline_editor_document
        from haute._types import GraphEdge, GraphNode, NodeData, NodeType
        from haute.assistant._tools import get_node_schema
        from haute.routes._helpers import parse_pipeline_to_graph
        from haute.routes._save_pipeline import SavePipelineService

        graph = parse_pipeline_to_graph(project_root / "main.py")
        table = {
            "factors": ["quote_id"],
            "outputColumn": "rate",
            "defaultValue": "1.0",
            "entries": [{"quote_id": "q1", "value": "1.1"}],
        }
        for node_id, node_type, extra in (
            ("blank", NodeType.POLARS, {}),
            ("rated", NodeType.RATING_STEP, {"tables": [table]}),
        ):
            config = {**palette_default_config(node_type), **extra}
            graph.nodes.append(
                GraphNode(
                    id=node_id,
                    data=NodeData(label=node_id, nodeType=node_type, config=config),
                )
            )
            graph.edges.append(GraphEdge(id=f"q-{node_id}", source="quotes", target=node_id))
        SavePipelineService(project_root, project_root).save_graph_transactionally(
            graph=graph,
            name="main",
            description="",
            preamble=graph.preamble,
            source_file="main.py",
            base_revision=load_pipeline_editor_document(
                project_root / "main.py", project_root=project_root
            ).source_revision,
        )

        blank = get_node_schema("main.py", "blank")
        rated = get_node_schema("main.py", "rated")

        assert blank["unresolved_reason"] == "node_has_no_code", blank
        assert set(blank["inputs"]) == {"quotes"}
        assert "rate" in _columns(rated)

    def test_group_by_node_resolves_because_nothing_is_collected(self, project_root: Path):
        """Schema resolution materialises nothing, so the engine's group-by
        memory-admission gate does not apply to it. Before this, every
        aggregation the assistant was asked to author was unresolvable."""

        (project_root / "main.py").write_text(GROUP_BY_SOURCE, encoding="utf-8")
        from haute.assistant._tools import get_node_schema

        result = get_node_schema("main.py", "by_year")

        assert "error" not in result, result
        assert _columns(result) == {"vehicle_year": "Int64", "quote_count": "UInt32"}

    def test_invalid_node_code_still_reports_the_engine_diagnosis(
        self, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """A real query failure keeps its analyst-facing text: naming the
        missing column is what lets the model correct its own authoring. The
        name is in saved code only, so it is named where the model may read
        that code (masked source withholds it; see
        TestFailureColumnNamesFollowTheEgressPolicy)."""

        (project_root / "main.py").write_text(
            PIPELINE_SOURCE.replace('.drop("notes")', '.drop("no_such_column")'),
            encoding="utf-8",
        )
        _egress_policy(monkeypatch, executable_source=True, row_samples=False)
        from haute.assistant._tools import get_node_schema

        result = get_node_schema("main.py", "enriched")

        assert result["error"]["code"] == "schema_unresolvable"
        assert "no_such_column" in result["error"]["message"]

    def test_schema_resolution_never_collects(
        self, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """The collect-poisoning invariant: plan construction plus
        ``collect_schema()`` must never invoke ``LazyFrame.collect``."""

        from haute.assistant._tools import get_node_schema

        def poisoned_collect(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
            raise AssertionError("get_node_schema must never collect data")

        monkeypatch.setattr(pl.LazyFrame, "collect", poisoned_collect)
        result = get_node_schema("main.py", "enriched")
        assert "columns" in result, result

    def test_output_node_schema_resolves_without_collecting(
        self, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """EXEC-P08: an OUTPUT terminal used to assemble its whole document while
        the graph was being built, so the collect-poisoning invariant did not
        hold for it. ``schema_only=True`` now reaches the OUTPUT builder, which
        describes the document from its mapping and source schemas instead."""

        import haute._sandbox as sandbox_module
        import haute.assistant._tools as tools_module
        from haute.assistant._tools import get_node_schema
        from tests.conftest import make_edge, make_graph, make_output_config

        monkeypatch.setattr(sandbox_module, "_PROJECT_ROOT", project_root.resolve())

        graph = make_graph(
            {
                "nodes": [
                    {
                        "id": "quotes",
                        "data": {
                            "label": "quotes",
                            "nodeType": "dataInput",
                            "config": {
                                "path": "data/quotes.parquet",
                                "inputType": "file",
                                "format": "parquet",
                            },
                        },
                    },
                    {
                        "id": "out",
                        "data": {
                            "label": "out",
                            "nodeType": "output",
                            "config": make_output_config(
                                ["quote_id", "vehicle_year"], source_port="quotes"
                            ),
                        },
                    },
                ],
                "edges": [make_edge("quotes", "out").model_dump()],
            }
        )
        monkeypatch.setattr(tools_module, "parse_pipeline_to_graph", lambda _path: graph)

        def poisoned_collect(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
            raise AssertionError("get_node_schema must never collect data")

        monkeypatch.setattr(pl.LazyFrame, "collect", poisoned_collect)
        result = get_node_schema("main.py", "out")
        assert _columns(result) == {"quote_id": "String", "vehicle_year": "Int64"}


# ---------------------------------------------------------------------------
# Pre-flatten target validation (crafted hierarchical graph)
# ---------------------------------------------------------------------------


def _node(node_id: str, node_type: str = "polars") -> GraphNode:
    return GraphNode(
        id=node_id,
        data=NodeData(label=node_id, nodeType=node_type, config={}),
        position={"x": 0.0, "y": 0.0},
    )


def _graph_with_submodel() -> PipelineGraph:
    graph = PipelineGraph(nodes=[_node("a"), _node("submodel__sm1", "submodel")], edges=[])
    return graph.model_copy(
        update={
            "submodels": {
                "sm1": {
                    "graph": {
                        "nodes": [_node("inner_child").model_dump()],
                        "edges": [],
                    }
                }
            }
        }
    )


class TestSubmodelBoundaryValidation:
    @pytest.fixture()
    def patched_parse(self, monkeypatch: pytest.MonkeyPatch) -> PipelineGraph:
        import haute.assistant._tools as tools_module

        graph = _graph_with_submodel()
        monkeypatch.setattr(tools_module, "parse_pipeline_to_graph", lambda _path: graph)
        return graph

    def test_submodel_placeholder_is_boundary_error(self, patched_parse, tmp_path):
        from haute.assistant._tools import get_node_schema

        result = get_node_schema("main.py", "submodel__sm1")
        assert result["error"]["code"] == "submodel_boundary"

    def test_submodel_internal_child_is_boundary_error_never_resolved(
        self, patched_parse, monkeypatch: pytest.MonkeyPatch
    ):
        """The id exists in the FLATTENED executable graph, so this asserts
        validation happens against the hierarchical graph before flattening."""

        import haute.assistant._tools as tools_module

        def must_not_execute(*args, **kwargs):  # noqa: ANN002, ANN003
            raise AssertionError("engine must not run for a boundary-rejected target")

        monkeypatch.setattr(tools_module, "execute_lazy_graph", must_not_execute)
        from haute.assistant._tools import get_node_schema

        result = get_node_schema("main.py", "inner_child")
        assert result["error"]["code"] == "submodel_boundary"

    def test_id_found_nowhere_is_unknown_node(self, patched_parse):
        from haute.assistant._tools import get_node_schema

        result = get_node_schema("main.py", "nowhere")
        assert result["error"]["code"] == "unknown_node"


# ---------------------------------------------------------------------------
# Engine invocation contract + shaped results (patched facade)
# ---------------------------------------------------------------------------


class TestEngineInvocation:
    def test_facade_called_with_active_source_and_production_kwargs(
        self, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """The facade's ``source`` default is ``"live"`` — the tool must pass
        the graph's saved active source, the target/preserve ids, a compiled
        preamble namespace, and the production contract-enforcement flag."""

        import haute.assistant._tools as tools_module

        captured: dict = {}
        real_facade = tools_module.execute_lazy_graph

        def capturing_facade(graph, build_node_fn, **kwargs):
            captured.update(kwargs)
            captured["graph"] = graph
            return real_facade(graph, build_node_fn, **kwargs)

        monkeypatch.setattr(tools_module, "execute_lazy_graph", capturing_facade)
        from haute.assistant._tools import get_node_schema

        result = get_node_schema("main.py", "enriched")
        assert "columns" in result, result
        assert captured["target_node_id"] == "enriched"
        # The target's parents are preserved alongside it so one engine call
        # yields both the node's own schema and its per-input schemas.
        assert captured["preserve_node_ids"] == {"enriched", "quotes"}
        assert captured["enforce_contracts"] is True
        assert captured["schema_only"] is True
        parsed_active_source = tools_module.parse_pipeline_to_graph(Path("main.py")).active_source
        assert captured["source"] == parsed_active_source
        assert captured["preamble_ns"], "preamble namespace must be compiled and passed"
        assert "add_flag" in captured["preamble_ns"]

    def test_multi_frame_output_reports_per_port_schemas(
        self, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """A multi-frame source stores dict[port_name, LazyFrame] in
        lazy_outputs — the tool must render per-port, never call
        collect_schema() on the dict."""

        import haute.assistant._tools as tools_module

        def fake_facade(graph, build_node_fn, **kwargs):
            lazy_outputs = {
                kwargs["target_node_id"]: {
                    "quotes_a": pl.LazyFrame({"x": [1]}),
                    "quotes_b": pl.LazyFrame({"y": ["s"], "z": [1.0]}),
                }
            }
            return (lazy_outputs,)

        monkeypatch.setattr(tools_module, "execute_lazy_graph", fake_facade)
        from haute.assistant._tools import get_node_schema

        result = get_node_schema("main.py", "quotes")
        assert "ports" in result, result
        ports = {
            port: {column["name"]: column["dtype"] for column in columns}
            for port, columns in result["ports"].items()
        }
        assert ports == {
            "quotes_a": {"x": "Int64"},
            "quotes_b": {"y": "String", "z": "Float64"},
        }

    def test_engine_raise_becomes_structured_error(
        self, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """A source snapshot failure keeps its analyst-facing message."""

        import haute.assistant._tools as tools_module
        from haute._source_cache import SourceCacheCorruptError

        def raising_facade(graph, build_node_fn, **kwargs):
            raise SourceCacheCorruptError(
                "The Data Input snapshot is corrupt. Rebuild its cache in the node editor."
            )

        monkeypatch.setattr(tools_module, "execute_lazy_graph", raising_facade)
        from haute.assistant._tools import get_node_schema

        result = get_node_schema("main.py", "quotes")
        assert result["error"]["code"] == "schema_unresolvable"
        assert "Rebuild" in result["error"]["message"]


# ---------------------------------------------------------------------------
# Remaining read tools + the executor dispatch seam
# ---------------------------------------------------------------------------


class TestReadTools:
    def test_get_pipeline_renders_compact_graph(self, project_root: Path):
        from haute.assistant._tools import get_pipeline

        rendered = get_pipeline("main.py")
        assert {node["id"] for node in rendered["nodes"]} == {"quotes", "enriched"}
        assert rendered["name"] == "main"
        assert set(rendered["nodes"][0].keys()) == {"id", "type", "label", "config"}
        assert rendered["preamble"]["present"] is True
        assert len(rendered["preamble"]["sha256"]) == 64
        assert len(rendered["project_revision"]) == 64
        assert "def add_flag" not in repr(rendered)

    def test_get_pipeline_missing_source_is_structured_error(self, project_root: Path):
        """Assistant read tools stay strict; a missing file is unavailable."""

        from haute.assistant._tools import get_pipeline

        result = get_pipeline("missing.py")
        assert result["error"]["code"] == "pipeline_unavailable"

    def test_get_node_config_returns_redacted_policy_eligible_config(self, project_root: Path):
        from haute.assistant._tools import get_node_config

        result = get_node_config("main.py", "quotes")
        assert result["node"] == "quotes"
        assert isinstance(result["config"], dict)
        assert result["config"]["code"] == "<redacted: executable_source>"
        assert len(result["project_revision"]) == 64

    def test_get_node_config_is_denied_before_read_for_public_only_policy(
        self, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ):
        import haute.assistant._tools as tools_module
        from haute.assistant._config import EgressPolicy

        monkeypatch.setattr(
            tools_module,
            "resolve_egress_policy",
            lambda _root: EgressPolicy(
                trust="external",
                max_sensitivity="public",
                allow_project_knowledge=False,
                allow_executable_source=False,
                allow_row_samples=False,
            ),
        )
        monkeypatch.setattr(
            tools_module,
            "_parse_graph",
            lambda _source: (_ for _ in ()).throw(
                AssertionError("policy must be checked before node config is read")
            ),
        )

        result = tools_module.get_node_config("main.py", "quotes")
        assert result["error"]["code"] == "egress_policy_denied"

    def test_get_node_config_is_denied_before_read_for_internal_policy(
        self, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ):
        import haute.assistant._tools as tools_module
        from haute.assistant._config import EgressPolicy

        monkeypatch.setattr(
            tools_module,
            "resolve_egress_policy",
            lambda _root: EgressPolicy(
                trust="organization",
                max_sensitivity="internal",
                allow_project_knowledge=False,
                allow_executable_source=False,
                allow_row_samples=False,
            ),
        )
        monkeypatch.setattr(
            tools_module,
            "_parse_graph",
            lambda _source: (_ for _ in ()).throw(
                AssertionError("policy must be checked before node config is read")
            ),
        )

        result = tools_module.get_node_config("main.py", "quotes")
        assert result["error"]["code"] == "egress_policy_denied"
        assert result["error"]["required_sensitivity"] == "restricted"

    def test_get_node_config_unknown_node(self, project_root: Path):
        from haute.assistant._tools import get_node_config

        assert get_node_config("main.py", "ghost")["error"]["code"] == "unknown_node"

    def test_the_manifest_node_index_covers_all_19(self, project_root: Path):
        from haute.assistant._tools import get_capability_descriptors, get_capability_manifest

        node_ids = [entry["id"] for entry in get_capability_manifest()["node_index"]]
        assert len(node_ids) == 19
        descriptors = get_capability_descriptors("node", node_ids[:12])["descriptors"]
        assert all(descriptor["usage"] for descriptor in descriptors)

    def test_capability_manifest_and_descriptor_batch_are_registry_views(self, project_root: Path):
        from haute.assistant._catalog import capability_manifest
        from haute.assistant._tools import (
            get_capability_descriptors,
            get_capability_manifest,
        )

        manifest = get_capability_manifest()
        assert manifest["capability_hash"] == capability_manifest().capability_hash
        assert "node_index" in manifest
        assert "nodes" not in manifest

        node_batch = get_capability_descriptors("node", ["banding", "edgeJoin"])
        assert node_batch["kind"] == "node"
        assert node_batch["count"] == 2
        assert [descriptor["id"] for descriptor in node_batch["descriptors"]] == [
            "banding",
            "edgeJoin",
        ]
        node = node_batch["descriptors"][0]
        assert node["id"] == "banding"
        assert node["config_schema"]["additionalProperties"] is False

        operation = get_capability_descriptors("operation", ["get_pipeline"])["descriptors"][0]
        assert operation["id"] == "get_pipeline"
        assert operation["risk"] == "none"

        unknown = get_capability_descriptors("node", ["banding", "not-real"])
        assert unknown["error"]["code"] == "unsupported_capability"
        assert "descriptors" not in unknown
        duplicate = get_capability_descriptors("node", ["banding", "banding"])

        assert duplicate["error"]["code"] == "invalid_capability_query"

    async def test_every_capability_descriptor_batch_is_json_safe_through_executor(
        self, project_root: Path
    ):
        from haute.assistant._catalog import capability_manifest
        from haute.assistant._tools import build_tool_executor

        execute_tool = build_tool_executor("main.py")

        manifest = capability_manifest()
        descriptor_ids = {
            "node": [descriptor.id for descriptor in manifest.nodes],
            "recipe": [str(descriptor["id"]) for descriptor in manifest.recipes],
            "operation": [descriptor.id for descriptor in manifest.operations],
        }

        for kind, ids in descriptor_ids.items():
            returned_ids: list[str] = []
            for offset in range(0, len(ids), 12):
                expected_ids = ids[offset : offset + 12]
                result = await execute_tool(
                    "get_capability_descriptors",
                    {"kind": kind, "ids": expected_ids},
                )
                is_error = "error" in result
                assert is_error is False
                assert result["count"] == len(expected_ids)
                returned_ids.extend(descriptor["id"] for descriptor in result["descriptors"])
                json.dumps(result, allow_nan=False)

            assert returned_ids == ids

    @pytest.mark.parametrize("tool", ["list_datasets", "get_dataset_schema"])
    def test_a_path_outside_the_project_reports_the_bare_containment_message(
        self, project_root: Path, tool: str
    ):
        from haute.assistant._tools import get_dataset_schema, list_datasets

        if tool == "list_datasets":
            result = list_datasets("../outside")
        else:
            result = get_dataset_schema("../outside/quotes.parquet")

        assert result["error"]["message"] == "Cannot access paths outside the project root"

    def test_list_datasets_uses_the_installed_input_extension_registry(
        self, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ):
        import haute.routes.files as files_routes
        from haute.assistant._tools import list_datasets

        (project_root / "data" / "notes.txt").write_text("x", encoding="utf-8")
        (project_root / "data" / "supported.feather").write_text("x", encoding="utf-8")
        (project_root / "data" / "unsupported.xml").write_text("x", encoding="utf-8")
        (project_root / "data" / ".hidden.parquet").write_text("x", encoding="utf-8")
        monkeypatch.setattr(
            files_routes,
            "_installed_input_extensions",
            lambda: (".parquet", ".feather"),
        )

        result = list_datasets("data")
        names = {item["name"] for item in result["datasets"]}
        assert names == {"quotes.parquet", "supported.feather"}

    def test_list_datasets_names_subdirectories_for_navigation(self, project_root: Path):
        """The project root lists visible subdirectories so the model can
        navigate to nested data instead of guessing paths.

        Regression: with datasets only under ``data/``, the root listing
        returned bare ``{"datasets": []}`` — no clue any subdirectory existed.
        """
        from haute.assistant._tools import list_datasets

        (project_root / ".git").mkdir(exist_ok=True)
        result = list_datasets(None)
        assert result["datasets"] == []
        assert "data" in result["directories"]
        assert all(not d.startswith(".") for d in result["directories"])

    def test_list_datasets_subdirectory_listing_still_navigable(self, project_root: Path):
        from haute.assistant._tools import list_datasets

        (project_root / "data" / "nested").mkdir()
        result = list_datasets("data")
        assert {item["name"] for item in result["datasets"]} == {"quotes.parquet"}
        assert result["directories"] == ["data/nested"]
        assert result["datasets"][0]["path"] == "data/quotes.parquet"

    def test_list_datasets_recursively_finds_nested_project_data(self, project_root: Path):
        from haute.assistant._tools import list_datasets

        nested = project_root / "data" / "competitor_premiums"
        nested.mkdir()
        pl.DataFrame({"quote_id": ["q1"], "premium": [123.0]}).write_parquet(
            nested / "competitor_insight.parquet"
        )

        result = list_datasets("data", recursive=True)

        assert [item["path"] for item in result["datasets"]] == [
            "data/competitor_premiums/competitor_insight.parquet",
            "data/quotes.parquet",
        ]
        assert result["directories"] == ["data/competitor_premiums"]
        assert result["recursive"] is True
        assert result["truncated"] is False

    async def test_executor_runs_the_dataset_listing_the_model_asked_for(self, project_root: Path):
        from haute.assistant._tools import build_tool_executor

        nested = project_root / "data" / "competitor_premiums"
        nested.mkdir()
        pl.DataFrame({"quote_id": ["q1"], "premium": [123.0]}).write_parquet(
            nested / "competitor_insight.parquet"
        )
        execute_tool = build_tool_executor("main.py")

        result = await execute_tool(
            "list_datasets",
            {"project_root": "data", "recursive": False},
        )

        assert [item["path"] for item in result["datasets"]] == ["data/quotes.parquet"]
        assert result["recursive"] is False

    def test_list_datasets_missing_directory(self, project_root: Path):
        from haute.assistant._tools import list_datasets

        assert list_datasets("nope")["error"]["code"] == "directory_not_found"

    def test_list_datasets_rejects_path_escape(self, project_root: Path):
        from haute.assistant._tools import list_datasets

        result = list_datasets("../..")
        assert "error" in result

    def test_dataset_tools_reject_hidden_state_paths(self, project_root: Path):
        from haute.assistant._tools import get_dataset_schema, list_datasets

        state_dir = project_root / ".haute"
        state_dir.mkdir()
        (state_dir / "session.json").write_text('[{"secret": "value"}]', encoding="utf-8")

        listed = list_datasets(".haute")
        previewed = get_dataset_schema(".haute/session.json")

        assert listed["error"]["code"] == "dataset_path_forbidden"
        assert previewed["error"]["code"] == "dataset_path_forbidden"

    def test_dataset_tools_hide_denylisted_credential_files(self, project_root: Path):
        from haute.assistant._tools import get_dataset_schema, list_datasets

        credentials = project_root / "credentials.json"
        credentials.write_text('[{"token": "do-not-preview"}]', encoding="utf-8")
        credential_dir = project_root / "credentials"
        credential_dir.mkdir()
        (credential_dir / "token.json").write_text(
            '[{"token": "also-do-not-preview"}]', encoding="utf-8"
        )

        listed = list_datasets(None)
        previewed = get_dataset_schema("credentials.json")
        nested = get_dataset_schema("credentials/token.json")

        assert "credentials.json" not in {item["name"] for item in listed["datasets"]}
        assert "credentials" not in listed["directories"]
        assert previewed["error"]["code"] == "dataset_path_forbidden"
        assert nested["error"]["code"] == "dataset_path_forbidden"

    def test_get_dataset_schema_reads_real_file(self, project_root: Path):
        from haute.assistant._tools import get_dataset_schema

        result = get_dataset_schema("data/quotes.parquet")
        names = {column["name"] for column in result["columns"]}
        assert {"quote_id", "vehicle_year", "notes"} <= names
        assert "preview" not in result
        assert "do-not-preview" not in repr(result)

    def test_get_dataset_schema_never_collects_preview_rows(
        self, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ):
        import haute.routes.files as files_route
        from haute.assistant._tools import get_dataset_schema

        def forbidden_preview(_frame):
            raise AssertionError("schema-only assistant reads must not collect preview rows")

        monkeypatch.setattr(files_route, "_collect_file_preview", forbidden_preview)
        result = get_dataset_schema("data/quotes.parquet")
        assert "columns" in result
        assert "preview" not in result

    def test_get_dataset_schema_missing_file(self, project_root: Path):
        from haute.assistant._tools import get_dataset_schema

        assert get_dataset_schema("data/nope.parquet")["error"]["code"] == "dataset_not_found"

    def test_get_example_passthrough(self, project_root: Path):
        from haute.assistant._assets import example_index
        from haute.assistant._tools import get_example

        name = example_index()[0][0]
        assert "graph" in get_example(name)
        assert get_example("nope")["error"]["code"] == "unknown_example"


class TestToolExecutorDispatch:
    async def test_dispatches_read_tools_and_rejects_unknown(self, project_root: Path):
        from haute.assistant._tools import build_tool_executor

        execute_tool = build_tool_executor("main.py")
        rendered = await execute_tool("get_pipeline", {})
        assert {node["id"] for node in rendered["nodes"]} == {"quotes", "enriched"}
        assert len(rendered["capability_hash"]) == 64
        assert rendered["operation_version"] == "1.0"

        schema = await execute_tool("get_node_schema", {"node": "quotes"})
        assert "columns" in schema

        unknown = await execute_tool("explode", {})
        assert unknown["error"]["code"] == "unknown_tool"
        assert "get_pipeline" in unknown["error"]["valid_names"]

        manifest = await execute_tool("get_capability_manifest", {})
        assert "capability_hash" in manifest

        descriptors = await execute_tool(
            "get_capability_descriptors",
            {"kind": "operation", "ids": ["get_pipeline"]},
        )
        assert descriptors["descriptors"][0]["id"] == "get_pipeline"

        recipe = await execute_tool(
            "plan_recipe",
            {
                "recipe_id": "reference_join",
                "base_source": "quotes",
                "reference_source": "regions",
                "name": "Attach region",
                "how": "left",
                "left_on": ["region"],
                "right_on": ["region"],
            },
        )
        assert recipe["recipe_id"] == "reference_join"
        assert set(recipe) == {
            "recipe_id",
            "version",
            "recipe_plan_hash",
            "capability_hash",
            "operation_version",
        }
        assert len(recipe["recipe_plan_hash"]) == 64

    async def test_pending_recipe_dry_runs_by_handle_without_relaying_operations(
        self, project_root: Path
    ):
        from haute.assistant._tools import build_tool_executor

        execute_tool = build_tool_executor("main.py")
        recipe = await execute_tool(
            "plan_recipe",
            {
                "recipe_id": "categorical_banding",
                "source": "quotes",
                "name": "year_band",
                "column": "vehicle_year",
                "output_column": "vehicle_year_band",
                "rules": [
                    {"value": "2019", "assignment": "older"},
                    {"value": "2021", "assignment": "newer"},
                ],
                "output_name": "year_response",
                "output_columns": ["vehicle_year_band"],
                "default": "unknown",
            },
        )

        rewritten = await execute_tool(
            "dry_run_graph_edits",
            {
                "ops": [
                    {
                        "op": "add_node",
                        "node_type": "polars",
                        "name": "year_band",
                        "config": {"code": "df = df"},
                    },
                    {"op": "add_edge", "source": "quotes", "target": "$missing"},
                ]
            },
        )
        assert rewritten["error"]["code"] == "recipe_plan_requires_handle"

        exact = await execute_tool(
            "dry_run_recipe_plan",
            {"recipe_plan_hash": recipe["recipe_plan_hash"]},
        )
        assert "plan_hash" in exact
        normalized = exact["normalized_operations"]
        assert [operation["op"] for operation in normalized] == [
            "add_node",
            "add_edge",
            "add_node",
            "add_edge",
        ]
        assert normalized[0]["node_type"] == "banding"
        assert normalized[2]["node_type"] == "output"
        assert normalized[2]["name"] == "year_response"
        assert normalized[3]["source"] == "$recipe_categorical_banding"
        assert normalized[3]["target"] == "$recipe_output"

    async def test_latest_recipe_handle_replaces_prior_and_rejects_provider_authored_extras(
        self, project_root: Path
    ):
        from haute.assistant._tools import build_tool_executor

        execute_tool = build_tool_executor("main.py")
        arguments = {
            "recipe_id": "categorical_banding",
            "source": "quotes",
            "name": "year_band",
            "column": "vehicle_year",
            "output_column": "vehicle_year_band",
            "rules": [{"value": "2019", "assignment": "older"}],
            "default": "unknown",
        }
        prior = await execute_tool("plan_recipe", arguments)
        latest = await execute_tool(
            "plan_recipe",
            {**arguments, "name": "replacement_year_band"},
        )
        assert prior["recipe_plan_hash"] != latest["recipe_plan_hash"]

        replaced = await execute_tool(
            "dry_run_recipe_plan",
            {"recipe_plan_hash": prior["recipe_plan_hash"]},
        )
        assert replaced["error"]["code"] == "recipe_plan_not_found"

        rejected = await execute_tool(
            "dry_run_recipe_plan",
            {
                "recipe_plan_hash": latest["recipe_plan_hash"],
                "extra_ops": [
                    {
                        "op": "add_node",
                        "node_type": "polars",
                        "name": "after_banding",
                        "ref": "after_banding",
                        "config": {"code": "df = df.with_columns(pl.lit(1).alias('test_flag'))"},
                    },
                    {
                        "op": "add_edge",
                        "source": "$recipe_categorical_banding",
                        "target": "$after_banding",
                    },
                ],
                "extra_postconditions": [
                    {
                        "kind": "edge_exists",
                        "source": "$recipe_categorical_banding",
                        "target": "$after_banding",
                    }
                ],
            },
        )
        assert rejected["error"]["code"] == "invalid_request"
        assert rejected["error"]["validation_reason"] == "unknown_field"

        planned = await execute_tool(
            "dry_run_recipe_plan",
            {"recipe_plan_hash": latest["recipe_plan_hash"]},
        )
        assert "plan_hash" in planned
        assert [operation["op"] for operation in planned["normalized_operations"]] == [
            "add_node",
            "add_edge",
        ]

        consumed = await execute_tool(
            "dry_run_recipe_plan",
            {"recipe_plan_hash": latest["recipe_plan_hash"]},
        )
        assert consumed["error"]["code"] == "recipe_plan_not_found"

    async def test_executor_structured_plans_have_no_lexical_authority(self, project_root: Path):
        from haute.assistant._tools import build_tool_executor

        execute_tool = build_tool_executor("main.py")
        primitive = await execute_tool(
            "dry_run_graph_edits",
            {
                "ops": [
                    {
                        "op": "update_node",
                        "node": "quotes",
                        "config": {},
                    }
                ]
            },
        )
        assert "plan_hash" in primitive

        differently_selected = await execute_tool(
            "plan_recipe",
            {
                "recipe_id": "reference_join",
                "base_source": "quotes",
                "reference_source": "regions",
                "name": "wrong_route",
                "how": "left",
                "left_on": ["region"],
                "right_on": ["region"],
            },
        )
        assert differently_selected["recipe_id"] == "reference_join"
        assert "recipe_plan_hash" in differently_selected

        structured_name = await execute_tool(
            "plan_recipe",
            {
                "recipe_id": "categorical_banding",
                "source": "quotes",
                "name": "year_banding",
                "column": "vehicle_year",
                "output_column": "vehicle_year_band",
                "rules": [{"value": "2019", "assignment": "older"}],
                "default": "unknown",
            },
        )
        assert structured_name["recipe_id"] == "categorical_banding"
        assert "recipe_plan_hash" in structured_name

    async def test_complete_structured_plans_do_not_require_material_wording(
        self, project_root: Path
    ):
        from haute.assistant._tools import build_tool_executor

        execute_tool = build_tool_executor("main.py")
        recipe = await execute_tool(
            "plan_recipe",
            {
                "recipe_id": "rating_step",
                "source": "quotes",
                "name": "rating_factors",
                "tables": [
                    {
                        "factors": ["region"],
                        "output_column": "region_factor",
                        "entries": [{"factor_values": ["north"], "value": 1.1}],
                        "default_value": 1.0,
                    }
                ],
            },
        )
        assert recipe["recipe_id"] == "rating_step"
        assert "recipe_plan_hash" in recipe

        primitive_executor = build_tool_executor("main.py")
        primitive = await primitive_executor(
            "dry_run_graph_edits",
            {
                "ops": [
                    {
                        "op": "update_node",
                        "node": "quotes",
                        "config": {},
                    }
                ]
            },
        )
        assert "plan_hash" in primitive

    async def test_tool_input_and_result_context_are_bounded(
        self,
        project_root: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        import haute.assistant._tools as tools_module

        execute_tool = tools_module.build_tool_executor("main.py")
        oversized_input = await execute_tool(
            "get_capability_descriptors",
            {"kind": "node", "ids": ["x" * 1_000_001]},
        )
        assert oversized_input["error"]["code"] == "tool_payload_too_large"

        monkeypatch.setattr(
            tools_module,
            "get_authoring_guide",
            lambda: {"content": "x" * 256_001},
        )
        oversized_result = await execute_tool("get_authoring_guide", {})
        assert oversized_result["error"]["code"] == "tool_result_too_large"

    async def test_public_only_policy_denies_internal_project_tools_before_read(
        self,
        project_root: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        import haute.assistant._tools as tools_module
        from haute.assistant._config import EgressPolicy

        monkeypatch.setattr(
            tools_module,
            "resolve_egress_policy",
            lambda _root: EgressPolicy(
                trust="external",
                max_sensitivity="public",
                allow_project_knowledge=True,
                allow_executable_source=False,
                allow_row_samples=False,
            ),
        )
        monkeypatch.setattr(
            tools_module,
            "get_pipeline",
            lambda _source: (_ for _ in ()).throw(
                AssertionError("policy denial must happen before project read")
            ),
        )
        execute_tool = tools_module.build_tool_executor("main.py")

        read = await execute_tool("get_pipeline", {})
        mutation = await execute_tool("dry_run_graph_edits", {"ops": []})

        assert read["error"]["code"] == "egress_policy_denied"
        assert mutation["error"]["code"] == "egress_policy_denied"

    async def test_missing_required_argument_is_closed_invalid_request(self, project_root: Path):
        from haute.assistant._catalog import capability_manifest
        from haute.assistant._tools import build_tool_executor

        execute_tool = build_tool_executor("main.py")
        result = await execute_tool("get_node_schema", {})
        assert result["error"]["code"] == "invalid_request"
        assert "KeyError" not in result["error"]["message"]
        assert result["capability_hash"] == capability_manifest().capability_hash
        assert result["operation_version"] == "1.0"

    async def test_malformed_capability_query_has_its_stable_error(self, project_root: Path):
        from haute.assistant._tools import build_tool_executor

        execute_tool = build_tool_executor("main.py")
        missing = await execute_tool("get_capability_descriptors", {"kind": "node"})
        extra = await execute_tool(
            "get_capability_descriptors",
            {"kind": "node", "ids": ["banding"], "unexpected": True},
        )
        empty = await execute_tool(
            "get_capability_descriptors",
            {"kind": "node", "ids": []},
        )
        too_many = await execute_tool(
            "get_capability_descriptors",
            {"kind": "node", "ids": ["banding"] * 13},
        )

        assert missing["error"]["code"] == "invalid_capability_query"
        assert extra["error"]["code"] == "invalid_capability_query"
        assert empty["error"]["code"] == "invalid_capability_query"
        assert too_many["error"]["code"] == "invalid_capability_query"

    @pytest.mark.parametrize(
        ("operation", "path", "reason"),
        [
            ({}, "dry_run_graph_edits.ops[0].op", "missing_discriminator"),
            (
                {"op": "not_real"},
                "dry_run_graph_edits.ops[0].op",
                "unsupported_discriminator",
            ),
            (
                {
                    "op": "add_node",
                    "node_type": "dataInput",
                    "name": "Input",
                    "config": "not-an-object",
                },
                "dry_run_graph_edits.ops[0].config",
                "wrong_type",
            ),
        ],
    )
    async def test_discriminated_operation_validation_is_precise(
        self, project_root: Path, operation: dict, path: str, reason: str
    ):
        from haute.assistant._tools import build_tool_executor

        result = await build_tool_executor("main.py")(
            "dry_run_graph_edits",
            {"ops": [operation]},
        )

        assert result["error"]["code"] == "invalid_request"
        assert result["error"]["validation_path"] == path
        assert result["error"]["validation_reason"] == reason

    async def test_unknown_field_names_the_rejected_field_and_the_allowlist(
        self, project_root: Path
    ):
        """`get_pipeline` reports handles in the operation vocabulary, but a
        model can still reach for the persisted camel-case spelling. Naming
        both the rejected key and the closed allowlist is what makes that
        correctable inside the turn's single retry."""

        from haute.assistant._tools import build_tool_executor

        result = await build_tool_executor("main.py")(
            "dry_run_graph_edits",
            {
                "ops": [
                    {"op": "delete_node", "node": "quotes"},
                    {
                        "op": "add_edge",
                        "source": "quotes",
                        "target": "enriched",
                        "sourceHandle": "out",
                    },
                ]
            },
        )

        error = result["error"]
        assert error["code"] == "invalid_request"
        assert error["validation_path"] == "dry_run_graph_edits.ops[1]"
        assert error["validation_reason"] == "unknown_field"
        assert error["unknown_fields"] == ["sourceHandle"]
        assert error["allowed_fields"] == [
            "op",
            "source",
            "source_handle",
            "target",
            "target_handle",
        ]
        assert "sourceHandle" in error["message"]
        assert "source_handle" in error["message"]

    def test_rendered_edges_use_the_graph_edit_operation_field_names(self, project_root: Path):
        """The read shape and the write shape must name handles identically;
        echoing the persisted camel-case spelling invited edit operations the
        closed operation schema then rejected."""

        from haute.assistant._tools import get_pipeline

        edge = get_pipeline("main.py")["edges"][0]

        assert "source_handle" in edge and "target_handle" in edge
        assert "sourceHandle" not in edge and "targetHandle" not in edge

    async def test_recipe_arguments_reject_duplicate_unique_items(self, project_root: Path):
        from haute.assistant._tools import build_tool_executor

        result = await build_tool_executor("main.py")(
            "plan_recipe",
            {
                "recipe_id": "categorical_banding",
                "source": "quotes",
                "name": "year_band",
                "column": "vehicle_year",
                "output_column": "vehicle_year_band",
                "rules": [{"value": "2019", "assignment": "older"}],
                "output_name": "year_response",
                "output_columns": ["vehicle_year_band", "vehicle_year_band"],
                "default": "unknown",
            },
        )

        assert result["error"]["code"] == "invalid_request"
        assert result["error"]["validation_path"] == "plan_recipe.output_columns"
        assert result["error"]["validation_reason"] == "duplicate_items"

    @pytest.mark.parametrize(
        ("ops", "received"),
        [
            ('[{"op": "delete_node", "node": "quotes"}]', "string"),
            ({"op": "delete_node", "node": "quotes"}, "object"),
        ],
    )
    async def test_wrong_type_names_the_expected_and_received_json_types(
        self, project_root: Path, ops: object, received: str
    ):
        """A gateway that encodes a container as a string, or a model that
        sends one operation instead of a batch, both land here. "has the wrong
        JSON type" gave no way to tell those apart or to correct either."""

        from haute.assistant._tools import build_tool_executor

        result = await build_tool_executor("main.py")("dry_run_graph_edits", {"ops": ops})

        error = result["error"]
        assert error["code"] == "invalid_request"
        assert error["validation_path"] == "dry_run_graph_edits.ops"
        assert error["validation_reason"] == "wrong_type"
        assert error["expected_types"] == ["array"]
        assert error["received_type"] == received
        assert "must be JSON array" in error["message"]
        # The string case additionally names the fix, because a stringified
        # container is not something the model can see from the type alone.
        assert ("not a JSON-encoded string" in error["message"]) is (received == "string")

    async def test_wrong_type_spells_the_json_boolean_literals_without_the_value(
        self, project_root: Path
    ):
        """A Databricks model that writes Python's `True` arrives as a string;
        naming only the type left it guessing at the literal to send."""

        from haute.assistant._tools import build_tool_executor

        result = await build_tool_executor("main.py")("list_datasets", {"recursive": "True"})

        error = result["error"]
        assert error["validation_reason"] == "wrong_type"
        assert error["expected_types"] == ["boolean"]
        assert "must be JSON boolean (true or false), but a string was sent" in error["message"]
        assert "True" not in error["message"]

    @pytest.mark.parametrize(("value", "text_form"), [(True, '"true"'), (3, "digits")])
    async def test_non_string_categorical_rule_value_is_refused_with_its_text_form(
        self, project_root: Path, value: object, text_form: str
    ):
        """Banding matches the column's text form, so a boolean or number rule
        would never match; the refusal says how to write the value instead."""

        from haute.assistant._tools import build_tool_executor

        result = await build_tool_executor("main.py")(
            "plan_recipe",
            {
                "recipe_id": "categorical_banding",
                "source": "quotes",
                "name": "Claims band",
                "column": "has_claims",
                "output_column": "claims_group",
                "rules": [{"value": value, "assignment": "claimed"}],
                "default": "other",
            },
        )

        error = result["error"]
        assert error["code"] == "invalid_request"
        assert error["validation_reason"] == "wrong_type"
        assert error["validation_path"] == "plan_recipe.rules[0].value"
        assert text_form in error["message"]

    @pytest.mark.parametrize(
        ("name", "arguments"),
        [
            ("apply_graph_plan", {"plan_hash": "not-a-hash"}),
            (
                "dry_run_graph_edits",
                {
                    "ops": [
                        {
                            "op": "add_node",
                            "node_type": "polars",
                            "name": "new",
                            "unexpected": True,
                        }
                    ]
                },
            ),
            ("get_project_knowledge", {"query": "rating", "limit": 11}),
            ("dry_run_graph_edits", {"ops": "not-json"}),
        ],
    )
    async def test_closed_tool_schema_enforces_patterns_variants_and_bounds(
        self,
        project_root: Path,
        name: str,
        arguments: dict,
    ):
        from haute.assistant._tools import build_tool_executor

        result = await build_tool_executor("main.py")(name, arguments)

        assert result["error"]["code"] == "invalid_request"

    @pytest.mark.parametrize(
        "invalid_value",
        [Path("not-json"), float("nan"), float("inf")],
    )
    async def test_executor_never_raises_for_non_json_argument_values(
        self,
        project_root: Path,
        invalid_value: object,
    ):
        from haute.assistant._tools import build_tool_executor

        result = await build_tool_executor("main.py")(
            "get_node_schema",
            {"node": invalid_value},
        )

        assert result["error"]["code"] == "invalid_request"
        assert "not-json" not in result["error"]["message"]

    async def test_the_removed_node_catalogue_tool_names_its_replacement(self, project_root: Path):
        from haute.assistant._tools import TOOL_DEFINITIONS, build_tool_executor

        assert "list_node_types" not in {definition["name"] for definition in TOOL_DEFINITIONS}
        execute_tool = build_tool_executor("main.py")
        result = await execute_tool("list_node_types", {})
        assert result["error"]["code"] == "tool_removed"
        assert result["error"]["name"] == "list_node_types"
        assert result["error"]["message"].startswith(
            "list_node_types was removed; use get_capability_manifest"
        )
        assert "get_capability_descriptors" in result["error"]["message"]

    async def test_combined_apply_graph_edits_tool_is_not_provider_visible(
        self, project_root: Path
    ):
        from haute.assistant._tools import build_tool_executor

        execute_tool = build_tool_executor("main.py")
        result = await execute_tool("apply_graph_edits", {"ops": []})
        assert result["error"]["code"] == "unknown_tool"
        assert "apply_graph_edits" not in result["error"]["valid_names"]

    async def test_plan_tools_share_exact_single_use_authority(self, project_root: Path):
        from haute.assistant._tools import build_tool_executor

        execute_tool = build_tool_executor("main.py", session_id="session-1")
        dry_run = await execute_tool(
            "dry_run_graph_edits",
            {
                "ops": [
                    {
                        "op": "rename_node",
                        "node": "enriched",
                        "new_name": "renamed",
                    }
                ]
            },
        )
        assert len(dry_run["plan_hash"]) == 64
        assert "risk" not in dry_run
        assert "confirmation_required" not in dry_run
        assert "resulting_graph_shape" in dry_run

        refused = await execute_tool(
            "apply_graph_plan",
            {"plan_hash": dry_run["plan_hash"]},
        )
        assert refused["error"]["code"] == "authority_denied"

    async def test_rename_with_a_coded_consumer_fails_at_dry_run(self, project_root: Path):
        from haute.assistant._tools import build_tool_executor

        execute_tool = build_tool_executor("main.py", session_id="session-1")
        result = await execute_tool(
            "dry_run_graph_edits",
            {"ops": [{"op": "rename_node", "node": "quotes", "new_name": "policies"}]},
        )

        error = result["error"]
        assert error["code"] == "rename_has_consumers"
        assert error["consumers"] == [{"node": "enriched", "field": "code"}]
        assert "'enriched' code" in error["message"]

    async def test_destructive_dry_run_survives_shared_service_boundary(
        self,
        project_root: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        from haute.assistant import _tools
        from haute.assistant._ops import PlanStore

        shared_store = PlanStore()
        monkeypatch.setattr(_tools, "_PLAN_STORE", shared_store)
        execute_tool = _tools.build_tool_executor("main.py", session_id="session-1")

        dry_run = await execute_tool(
            "dry_run_graph_edits",
            {"ops": [{"op": "delete_node", "node": "enriched"}]},
        )

        assert "risk" not in dry_run
        assert "confirmation_required" not in dry_run
        assert shared_store.get(dry_run["plan_hash"]).plan_hash == dry_run["plan_hash"]


class TestExecutorArms:
    async def test_every_read_tool_dispatches_through_the_executor(self, project_root: Path):
        from haute.assistant._assets import example_index
        from haute.assistant._tools import build_tool_executor

        execute_tool = build_tool_executor("main.py")
        assert "config" in await execute_tool("get_node_config", {"node": "quotes"})
        listed = await execute_tool("list_datasets", {"project_root": "data"})
        assert listed["datasets"][0]["name"] == "quotes.parquet"
        schema = await execute_tool("get_dataset_schema", {"path": "data/quotes.parquet"})
        assert "columns" in schema
        knowledge = await execute_tool(
            "get_project_knowledge",
            {"query": "pipeline", "limit": 1},
        )
        assert "items" in knowledge
        plan = await execute_tool(
            "dry_run_graph_edits",
            {
                "ops": [
                    {
                        "op": "rename_node",
                        "node": "enriched",
                        "new_name": "renamed",
                    }
                ]
            },
        )
        assert "schema:data/quotes.parquet" in plan["revision_sources"]
        example = await execute_tool("get_example", {"name": example_index()[0][0]})
        assert "graph" in example
        guide = await execute_tool("get_authoring_guide", {})
        assert guide["approval_status"] == "reviewed"
        assert len(guide["sha256"]) == 64
        assert "node" in guide["content"].casefold()

    async def test_dry_run_rejects_dataset_schema_changed_after_retrieval(
        self,
        project_root: Path,
    ):
        from haute.assistant._tools import build_tool_executor

        execute_tool = build_tool_executor("main.py")
        schema = await execute_tool(
            "get_dataset_schema",
            {"path": "data/quotes.parquet"},
        )
        assert "source_digest" in schema
        pl.DataFrame({"replacement": [1]}).write_parquet(project_root / "data" / "quotes.parquet")

        result = await execute_tool(
            "dry_run_graph_edits",
            {
                "ops": [
                    {
                        "op": "rename_node",
                        "node": "enriched",
                        "new_name": "renamed",
                    }
                ]
            },
        )

        assert result["error"]["code"] == "stale_project_evidence"
        assert "data/quotes.parquet" in result["error"]["message"]

    @pytest.mark.parametrize("replayed", [False, True], ids=["live-turn", "from-history"])
    async def test_a_renamed_dataset_blocks_planning_only_until_datasets_are_listed(
        self, project_root: Path, replayed: bool
    ):
        from haute.assistant._tools import build_tool_executor

        pl.DataFrame({"id": [1]}).write_parquet(project_root / "data" / "extra.parquet")
        first_turn = build_tool_executor("main.py")
        schema = await first_turn("get_dataset_schema", {"path": "data/extra.parquet"})
        (project_root / "data" / "extra.parquet").rename(project_root / "data" / "moved.parquet")
        rename = {"ops": [{"op": "rename_node", "node": "enriched", "new_name": "renamed"}]}

        blocked = await first_turn("dry_run_graph_edits", rename)
        assert blocked["error"]["code"] == "project_source_missing"
        assert "data/extra.parquet" in blocked["error"]["message"]

        listed = await first_turn("list_datasets", {"project_root": "data"})
        execute_tool = (
            build_tool_executor(
                "main.py",
                prior_messages=[
                    {"role": "tool", "name": name, "content": content, "is_error": False}
                    for name, content in (
                        ("get_dataset_schema", schema),
                        ("list_datasets", listed),
                    )
                ],
            )
            if replayed
            else first_turn
        )
        plan = await execute_tool("dry_run_graph_edits", rename)

        assert "error" not in plan, plan
        assert "schema:data/extra.parquet" not in plan["revision_sources"]

    async def test_a_new_excel_input_is_refused_with_the_preview_remedy(self, project_root: Path):
        from haute._sandbox import set_project_root
        from haute.assistant._tools import build_tool_executor

        set_project_root(project_root)  # restored by the autouse _restore_project_root
        execute_tool = build_tool_executor("main.py")
        result = await execute_tool(
            "dry_run_graph_edits",
            {
                "ops": [
                    {
                        "op": "add_node",
                        "node_type": "dataInput",
                        "name": "book",
                        "ref": "book",
                        "config": {
                            "inputType": "file",
                            "format": "excel",
                            "mode": "read",
                            "path": "book.xlsx",
                        },
                    },
                    {"op": "add_node", "node_type": "explore", "name": "look", "ref": "look"},
                    {"op": "add_edge", "source": "$book", "target": "$look"},
                ]
            },
        )

        assert result["error"]["code"] == "schema_unresolvable"
        assert "format 'excel' reads only eagerly" in result["error"]["message"]
        assert "Preview this input first" in result["error"]["message"]

    async def test_new_turn_carries_provider_visible_schema_evidence_into_plan(
        self,
        project_root: Path,
    ):
        from haute.assistant._tools import build_tool_executor

        first_turn = build_tool_executor("main.py")
        schema = await first_turn(
            "get_dataset_schema",
            {"path": "data/quotes.parquet"},
        )
        second_turn = build_tool_executor(
            "main.py",
            prior_messages=[
                {
                    "role": "tool",
                    "tool_call_id": "schema-1",
                    "name": "get_dataset_schema",
                    "content": schema,
                    "is_error": False,
                }
            ],
        )

        plan = await second_turn(
            "dry_run_graph_edits",
            {
                "ops": [
                    {
                        "op": "rename_node",
                        "node": "enriched",
                        "new_name": "renamed",
                    }
                ]
            },
        )

        assert "schema:data/quotes.parquet" in plan["revision_sources"]


class TestClosedSchemaKeywords:
    def test_max_length_rejects_long_strings(self):
        from haute.assistant._tools import _ToolArgumentValidationError, _validate_tool_value

        schema = {"type": "string", "maxLength": 3}
        _validate_tool_value("abc", schema, path="tool.field")

        with pytest.raises(_ToolArgumentValidationError) as excinfo:
            _validate_tool_value("abcd", schema, path="tool.field")

        assert excinfo.value.path == "tool.field"
        assert excinfo.value.reason == "too_long"
        assert str(excinfo.value) == "tool.field is too long"

    def test_unique_items_rejects_repeated_members(self):
        from haute.assistant._tools import _ToolArgumentValidationError, _validate_tool_value

        schema = {"type": "array", "uniqueItems": True, "items": {"type": "string"}}
        _validate_tool_value(["a", "b"], schema, path="tool.items")

        with pytest.raises(_ToolArgumentValidationError) as excinfo:
            _validate_tool_value(["a", "a"], schema, path="tool.items")

        assert excinfo.value.path == "tool.items"
        assert excinfo.value.reason == "duplicate_items"
        assert str(excinfo.value) == "tool.items contains duplicate items"

    def test_unique_items_compares_unhashable_members_canonically(self):
        from haute.assistant._tools import _ToolArgumentValidationError, _validate_tool_value

        schema = {"type": "array", "uniqueItems": True}
        _validate_tool_value([{"a": 1}, {"a": 2}], schema, path="tool.items")

        with pytest.raises(_ToolArgumentValidationError):
            _validate_tool_value([{"a": 1, "b": 2}, {"b": 2, "a": 1}], schema, path="tool.items")

    def test_unique_items_rejects_non_finite_json_members(self):
        from haute.assistant._tools import _validate_tool_value

        with pytest.raises(ValueError, match="Out of range float values"):
            _validate_tool_value(
                [float("nan"), float("nan")],
                {"type": "array", "uniqueItems": True},
                path="tool.items",
            )

    def test_unique_items_is_inactive_unless_declared(self):
        from haute.assistant._tools import _validate_tool_value

        _validate_tool_value(["a", "a"], {"type": "array"}, path="tool.items")


# ---------------------------------------------------------------------------
# Steps-first authoring replays (ASSIST-03)
# ---------------------------------------------------------------------------


def _guide_step_lists() -> list[list[dict[str, str]]]:
    """The step lists the authoring guide shows, in the order it shows them."""

    import re

    from haute.assistant._assets import authoring_guide

    blocks = re.findall(r"```json\n(.*?)\n```", authoring_guide(), flags=re.DOTALL)
    return [json.loads(block) for block in blocks]


@pytest.fixture()
def steps_first_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A saved pipeline whose Transform, Rating Step and Load File the analyst
    dropped from the palette and wired, but has not filled in."""

    from haute._config_io import palette_default_config
    from haute._pipeline_recovery import load_pipeline_editor_document
    from haute._sandbox import set_project_root
    from haute._types import GraphEdge, NodeType
    from haute.assistant import _tools
    from haute.assistant._ops import PlanStore
    from haute.routes._save_pipeline import SavePipelineService

    monkeypatch.chdir(tmp_path)
    set_project_root(tmp_path)  # restored by the autouse _restore_project_root
    monkeypatch.setattr(_tools, "_PLAN_STORE", PlanStore())
    monkeypatch.setattr(_tools, "mutations_readiness", lambda _root: (True, None))
    frames = {
        "proposer_claims": {
            "policy_id": ["p1", "p1", "p2"],
            "claim_month": ["2026-08", "2026-08", "2026-07"],
            "amount": [100.0, 50.0, 30.0],
        },
        "additional_drivers_claims": {
            "policy_id": ["p1", "p2"],
            "claim_month": ["2026-08", "2026-08"],
            "amount": [20.0, 10.0],
        },
        "quotes": {
            "quote_id": ["q1", "q2"],
            "region": ["north", "south"],
            "premium": [100.0, 200.0],
        },
        "regions": {"region": ["north", "south"], "zone": ["A", "B"]},
    }
    for name, frame in frames.items():
        pl.DataFrame(frame).write_parquet(tmp_path / f"{name}.parquet")
    (tmp_path / "loadings.json").write_text(json.dumps({"north": 1.1, "south": 0.9}))
    (tmp_path / "main.py").write_text(
        'import haute\n\npipeline = haute.Pipeline("main", description="replays")\n',
        encoding="utf-8",
    )
    palette = {
        "august_totals": NodeType.POLARS,
        "rated": NodeType.RATING_STEP,
        "loaded": NodeType.EXTERNAL_FILE,
    }
    nodes = [
        GraphNode(
            id=name,
            type=NodeType.DATA_INPUT.value,
            data=NodeData(
                label=name,
                nodeType=NodeType.DATA_INPUT,
                config={
                    **palette_default_config(NodeType.DATA_INPUT),
                    "path": f"{name}.parquet",
                },
            ),
        )
        for name in frames
    ] + [
        GraphNode(
            id=name,
            type=node_type.value,
            data=NodeData(label=name, nodeType=node_type, config=palette_default_config(node_type)),
        )
        for name, node_type in palette.items()
    ]
    wiring = [
        ("proposer_claims", "august_totals"),
        ("additional_drivers_claims", "august_totals"),
        ("quotes", "rated"),
        ("quotes", "loaded"),
        ("regions", "loaded"),
    ]
    SavePipelineService(project_root=tmp_path, pipeline_root=tmp_path).save_graph_transactionally(
        graph=PipelineGraph(
            nodes=nodes,
            edges=[GraphEdge(id=f"{s}->{t}", source=s, target=t) for s, t in wiring],
        ),
        name="main",
        description="replays",
        preamble=None,
        source_file="main.py",
        base_revision=load_pipeline_editor_document(
            tmp_path / "main.py", project_root=tmp_path
        ).source_revision,
    )
    return tmp_path


class TestStepsFirstAuthoring:
    """The authoring guide's worked examples, sent as the model would send
    them, fill the named palette-default node through the real dry-run and
    apply tools, stay in the step builder and run."""

    @pytest.mark.parametrize(
        ("example", "node", "config", "key", "expected"),
        [
            pytest.param(
                0,
                "august_totals",
                {},
                "policy_id",
                {"policy_id": ["p1", "p2"], "august_claims": [170.0, 10.0]},
                id="august-aggregation-on-a-transform",
            ),
            pytest.param(
                1,
                "rated",
                {},
                "quote_id",
                {"quote_id": ["q1", "q2"], "premium": [100.0, 150.0]},
                id="rating-step-hook",
            ),
            pytest.param(
                2,
                "loaded",
                {"path": "loadings.json", "fileType": "json"},
                "quote_id",
                {"quote_id": ["q1", "q2"], "zone": ["A", "B"], "loading": [1.1, 0.9]},
                id="load-file-with-obj-and-a-second-input",
            ),
        ],
    )
    async def test_a_guide_example_fills_the_named_node_and_runs(
        self,
        steps_first_project: Path,
        example: int,
        node: str,
        config: dict[str, str],
        key: str,
        expected: dict[str, list[object]],
    ):
        from haute._native_memory_limit import native_memory_backend_scope
        from haute.assistant._tools import apply_graph_plan, dry_run_graph_edits
        from haute.executor import execute_graph
        from haute.routes._helpers import parse_pipeline_to_graph

        steps = _guide_step_lists()[example]
        before = parse_pipeline_to_graph(steps_first_project / "main.py")
        ops = [{"op": "update_node", "node": node, "config": {**config, "steps": steps}}]

        plan = await dry_run_graph_edits("main.py", ops)
        assert "error" not in plan, plan
        applied = await apply_graph_plan("main.py", plan["plan_hash"])
        assert "error" not in applied, applied

        graph = parse_pipeline_to_graph(steps_first_project / "main.py")
        assert [item.id for item in graph.nodes] == [item.id for item in before.nodes]
        saved = next(item for item in graph.nodes if item.id == node).data.config
        assert saved["steps"] == steps
        assert "_steps_error" not in saved and "_steps_discarded" not in saved
        with native_memory_backend_scope("rlimit"):
            result = execute_graph(graph, target_node_id=node)[node]
        assert result.status == "ok", result.error
        rows = sorted(result.preview, key=lambda row: row[key])
        assert {column: [row[column] for row in rows] for column in expected} == expected


EGRESS_SOURCE = """\
import polars as pl

import haute

pipeline = haute.Pipeline("main", description="egress fixture")


@pipeline.polars
def quotes() -> pl.LazyFrame:
    return pl.scan_csv("data/quotes.csv")


@pipeline.polars
def typed() -> pl.LazyFrame:
    return pl.scan_csv("data/quotes.csv", schema_overrides={"age": pl.Int64})


@pipeline.polars
def strict(quotes: pl.LazyFrame) -> pl.LazyFrame:
    return quotes.with_columns(pl.col("age").cast(pl.Int64, strict=True)).collect().lazy()
"""

# Values that exist only in the rows: none may reach a tool result unless the
# project permits row samples.
_ROW_VALUES = ("Q-7781", "Q-9912", "1984-03-02", "1990-11-30", "abc", "secret-123")


@pytest.fixture()
def egress_project(project_root: Path) -> Path:
    """Quote rows with personal values and malformed ages, read from CSV.

    The first malformed age reads like a Polars column phrase, so a scraper
    that trusts the error text reports that cell as a column name.
    """

    (project_root / "data" / "quotes.csv").write_text(
        "quote_id,dob,age\nQ-7781,1984-03-02,41\nQ-5150,1977-06-01,column 'secret-123'\n"
        "Q-9912,1990-11-30,abc\n",
        encoding="utf-8",
    )
    (project_root / "main.py").write_text(EGRESS_SOURCE, encoding="utf-8")
    return project_root


def _free_code_node(source: str, code: str) -> list[dict[str, object]]:
    """Add one Transform that starts from *source* and runs *code* as free code."""

    return [
        {
            "op": "add_node",
            "node_type": "polars",
            "name": "probe",
            "ref": "probe",
            "config": {
                "steps": [
                    {"id": "start", "kind": "source", "input": source},
                    {"id": "logic", "kind": "free_code", "code": code},
                ]
            },
        },
        {"op": "add_edge", "source": source, "target": "$probe"},
    ]


_RAISE_WITH_ROWS = (
    "# Reject unexpected quotes\n"
    "rows = df.collect()\n"
    "raise ValueError(f\"bad {rows['quote_id'].to_list()} {rows['dob'].to_list()}\")\n"
)


def _row_values_in(result: object) -> list[str]:
    text = json.dumps(result)
    return [value for value in _ROW_VALUES if value in text]


class TestExecutionErrorEgress:
    """Resolving a schema runs node code over the project's rows, and that code
    or Polars itself can quote those rows in an exception."""

    async def test_node_code_exception_is_reduced_to_type_and_step(self, egress_project: Path):
        from haute.assistant._tools import dry_run_graph_edits

        result = await dry_run_graph_edits("main.py", _free_code_node("quotes", _RAISE_WITH_ROWS))

        assert result["error"]["code"] == "schema_unresolvable", result
        assert _row_values_in(result) == []
        message = result["error"]["message"]
        assert "ValueError in step 2 ('logic') of node 'probe'" in message
        assert "allow_row_samples" in message

    async def test_csv_cast_failure_names_the_column_not_the_value(self, egress_project: Path):
        from haute.assistant._tools import dry_run_graph_edits

        result = await dry_run_graph_edits(
            "main.py", _free_code_node("typed", "# Materialise\ndf = df.collect().lazy()")
        )

        assert result["error"]["code"] == "schema_unresolvable", result
        assert _row_values_in(result) == []
        assert "ComputeError" in result["error"]["message"]
        assert "column(s) 'age'." in result["error"]["message"]

    def test_node_schema_withholds_a_strict_cast_value(self, egress_project: Path):
        from haute.assistant._tools import get_node_schema

        result = get_node_schema("main.py", "strict")

        assert result["error"]["code"] == "schema_unresolvable", result
        assert _row_values_in(result) == []
        assert "InvalidOperationError at line 1 of the node code" in result["error"]["message"]
        assert "column(s) 'age'." in result["error"]["message"]

    async def test_an_authored_polars_error_cannot_pass_a_value_off_as_a_column(
        self, egress_project: Path
    ):
        """Node code can raise a Polars error whose text embeds collected values
        in a `column '...'` phrase; only a name the code itself writes is named."""

        from haute.assistant._tools import dry_run_graph_edits

        code = (
            "# Reject the first quote\n"
            "rows = df.collect()\n"
            "raise pl.exceptions.ComputeError(f\"column '{rows['quote_id'][0]}' is bad\")\n"
        )
        result = await dry_run_graph_edits("main.py", _free_code_node("quotes", code))

        assert result["error"]["code"] == "schema_unresolvable", result
        assert _row_values_in(result) == []
        message = result["error"]["message"]
        assert "ComputeError in step 2 ('logic') of node 'probe'" in message
        assert "; it names a column." in message

    async def test_a_missing_column_the_code_names_is_still_named(self, egress_project: Path):
        from haute.assistant._tools import dry_run_graph_edits

        result = await dry_run_graph_edits(
            "main.py",
            _free_code_node(
                "quotes", '# Rename one column\ndf = df.rename({"missing_col": "renamed"})'
            ),
        )

        assert result["error"]["code"] == "schema_unresolvable", result
        assert "ColumnNotFoundError" in result["error"]["message"]
        assert "column(s) 'missing_col'." in result["error"]["message"]


def _egress_policy(
    monkeypatch: pytest.MonkeyPatch, *, executable_source: bool, row_samples: bool
) -> None:
    import haute.assistant._tools as tools_module
    from haute.assistant._config import EgressPolicy

    monkeypatch.setattr(
        tools_module,
        "resolve_egress_policy",
        lambda _root: EgressPolicy(
            trust="organization",
            max_sensitivity="restricted",
            allow_project_knowledge=False,
            allow_executable_source=executable_source,
            allow_row_samples=row_samples,
        ),
    )


# Saved code the model may not read without executable source, holding a
# literal equal to the malformed cell's `column '...'` phrase.
_MASKED_LITERAL_SOURCES = {
    "preamble": EGRESS_SOURCE.replace(
        'pipeline = haute.Pipeline("main", description="egress fixture")',
        '_BLOCKED_CUSTOMER = "secret-123"\n\n'
        'pipeline = haute.Pipeline("main", description="egress fixture")',
    ),
    "node": EGRESS_SOURCE
    + """

@pipeline.polars
def screened(quotes: pl.LazyFrame) -> pl.LazyFrame:
    return quotes.filter(pl.col("quote_id") != "secret-123")
""",
}

_GHOST_SOURCE = (
    EGRESS_SOURCE
    + """

@pipeline.polars
def ghost(quotes: pl.LazyFrame) -> pl.LazyFrame:
    return quotes.rename({"ghost_col": "renamed"})
"""
)


class TestFailureColumnNamesFollowTheEgressPolicy:
    """A column name in an authored-code failure is reported only when the
    egress policy already discloses it: permitted schema metadata, text the
    model itself submitted in the plan, or saved code when executable source
    is permitted. Appearing in masked code does not make a value a column."""

    @pytest.mark.parametrize("where", sorted(_MASKED_LITERAL_SOURCES))
    async def test_a_masked_literal_equal_to_a_cell_is_not_named(
        self, egress_project: Path, monkeypatch: pytest.MonkeyPatch, where: str
    ):
        from haute.assistant._tools import dry_run_graph_edits

        source = _MASKED_LITERAL_SOURCES[where]
        assert source != EGRESS_SOURCE
        (egress_project / "main.py").write_text(source, encoding="utf-8")
        _egress_policy(monkeypatch, executable_source=False, row_samples=False)

        result = await dry_run_graph_edits(
            "main.py", _free_code_node("typed", "# Materialise\ndf = df.collect().lazy()")
        )

        assert result["error"]["code"] == "schema_unresolvable", result
        assert _row_values_in(result) == []
        # The plan never names `age`; it is the failing node's input schema.
        assert "column(s) 'age'." in result["error"]["message"]

    def test_a_profile_names_the_column_of_the_frame_it_collected(
        self, egress_project: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """A profile's frame resolved schema-only and failed only when collected,
        so that frame's own schema is permitted metadata; the masked literal
        equal to the cell is still not a column."""

        (egress_project / "main.py").write_text(
            _MASKED_LITERAL_SOURCES["preamble"], encoding="utf-8"
        )
        # Profiles need row samples; the summary still follows the source policy.
        _egress_policy(monkeypatch, executable_source=False, row_samples=True)

        result = _profile("main.py", "typed")

        assert result["error"]["code"] == "profile_unavailable", result
        summary = result["error"]["message"].split(": ", 1)[0]
        assert summary.endswith("; it names column(s) 'age'"), summary

    def test_a_named_input_profile_failure_never_resolves_the_consumer(
        self, egress_project: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Profiling one input of a node collects only that input's frame, so
        naming the failure's columns resolves that frame alone, never the
        consumer the profile did not ask for."""

        import haute.assistant._tools as tools_module

        (egress_project / "main.py").write_text(
            EGRESS_SOURCE
            + """

@pipeline.polars
def consumer(typed: pl.LazyFrame) -> pl.LazyFrame:
    return typed.with_columns(flag=pl.lit(1))
""",
            encoding="utf-8",
        )
        _egress_policy(monkeypatch, executable_source=False, row_samples=True)
        resolved_targets: list[str] = []
        original = tools_module._resolve_schema_outputs

        def recording(*args: object, target: str, **kwargs: object) -> object:
            resolved_targets.append(target)
            return original(*args, target=target, **kwargs)

        monkeypatch.setattr(tools_module, "_resolve_schema_outputs", recording)

        result = _profile("main.py", "consumer", "typed")

        assert result["error"]["code"] == "profile_unavailable", result
        summary = result["error"]["message"].split(": ", 1)[0]
        assert summary.endswith("; it names column(s) 'age'"), summary
        assert "typed" in resolved_targets
        assert "consumer" not in resolved_targets, resolved_targets

    def test_saved_code_names_a_column_only_with_executable_source(
        self, egress_project: Path, monkeypatch: pytest.MonkeyPatch
    ):
        from haute.assistant._tools import get_node_schema

        (egress_project / "main.py").write_text(_GHOST_SOURCE, encoding="utf-8")

        _egress_policy(monkeypatch, executable_source=False, row_samples=False)
        masked = get_node_schema("main.py", "ghost")
        _egress_policy(monkeypatch, executable_source=True, row_samples=False)
        readable = get_node_schema("main.py", "ghost")

        for result in (masked, readable):
            assert result["error"]["code"] == "schema_unresolvable", result
            assert "ColumnNotFoundError" in result["error"]["message"]
        assert "ghost_col" not in json.dumps(masked)
        assert "; it names a column." in masked["error"]["message"]
        assert "column(s) 'ghost_col'." in readable["error"]["message"]

    async def test_the_models_own_plan_text_names_a_column_with_source_masked(
        self, egress_project: Path, monkeypatch: pytest.MonkeyPatch
    ):
        from haute.assistant._tools import dry_run_graph_edits

        _egress_policy(monkeypatch, executable_source=False, row_samples=False)

        result = await dry_run_graph_edits(
            "main.py",
            _free_code_node("quotes", '# Rename one column\ndf = df.rename({"absent_col": "x"})'),
        )

        assert result["error"]["code"] == "schema_unresolvable", result
        assert "column(s) 'absent_col'." in result["error"]["message"]


_PREAMBLE_EGRESS_SOURCE = EGRESS_SOURCE.replace(
    'pipeline = haute.Pipeline("main", description="egress fixture")',
    '_FIRST_QUOTE = int(pl.read_csv("data/quotes.csv")["quote_id"][0])\n\n'
    'pipeline = haute.Pipeline("main", description="egress fixture")',
)


def _allow_row_samples(monkeypatch: pytest.MonkeyPatch) -> None:
    import haute.assistant._tools as tools_module
    from haute.assistant._config import EgressPolicy

    monkeypatch.setattr(
        tools_module,
        "resolve_egress_policy",
        lambda _root: EgressPolicy(
            trust="organization",
            max_sensitivity="restricted",
            allow_project_knowledge=False,
            allow_executable_source=False,
            allow_row_samples=True,
        ),
    )


class TestPreambleFailureEgress:
    """The preamble is authored code that can read project data at import, so its
    failure is reduced exactly as a node-code failure is."""

    @pytest.fixture()
    def preamble_project(self, egress_project: Path) -> Path:
        assert _PREAMBLE_EGRESS_SOURCE != EGRESS_SOURCE
        (egress_project / "main.py").write_text(_PREAMBLE_EGRESS_SOURCE, encoding="utf-8")
        return egress_project

    async def test_dry_run_withholds_the_preamble_failure_text(self, preamble_project: Path):
        from haute.assistant._tools import dry_run_graph_edits

        result = await dry_run_graph_edits("main.py", _free_code_node("quotes", "df = df"))

        assert result["error"]["code"] == "preamble_failed", result
        assert _row_values_in(result) == []
        assert "PreambleError at line" in result["error"]["message"]
        assert "allow_row_samples" in result["error"]["message"]

    def test_node_schema_withholds_the_preamble_failure_text(self, preamble_project: Path):
        from haute.assistant._tools import get_node_schema

        result = get_node_schema("main.py", "quotes")

        assert result["error"]["code"] == "schema_unresolvable", result
        assert _row_values_in(result) == []
        assert "PreambleError at line" in result["error"]["message"]

    async def test_apply_withholds_a_preamble_failure(
        self, egress_project: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Apply rebuilds the plan, so the preamble runs again there; a failure
        reaching the tool's generic handler is still reduced."""

        import haute.assistant._tools as tools_module
        from haute.errors import PreambleError

        class _FailingService:
            async def apply(self, _source_file: str, _plan_hash: str) -> object:
                raise PreambleError(
                    "Preamble line 5: invalid literal for int() with base 10: 'Q-7781'",
                    source_line=5,
                )

        monkeypatch.setattr(tools_module, "_application_service", lambda: _FailingService())

        result = await tools_module.apply_graph_plan("main.py", "0" * 64)

        assert _row_values_in(result) == []
        assert "PreambleError at line 5 of the preamble" in result["error"]["message"]

    async def test_permitted_row_samples_keep_the_preamble_text(
        self, preamble_project: Path, monkeypatch: pytest.MonkeyPatch
    ):
        from haute.assistant._tools import dry_run_graph_edits, get_node_schema

        _allow_row_samples(monkeypatch)

        planned = await dry_run_graph_edits("main.py", _free_code_node("quotes", "df = df"))
        schema = get_node_schema("main.py", "quotes")

        for result in (planned, schema):
            assert "PreambleError at line" in result["error"]["message"], result
            assert "Q-7781" in result["error"]["message"]

    async def test_permitted_row_samples_keep_the_error_text(
        self, egress_project: Path, monkeypatch: pytest.MonkeyPatch
    ):
        import haute.assistant._tools as tools_module
        from haute.assistant._config import EgressPolicy

        monkeypatch.setattr(
            tools_module,
            "resolve_egress_policy",
            lambda _root: EgressPolicy(
                trust="organization",
                max_sensitivity="restricted",
                allow_project_knowledge=False,
                allow_executable_source=False,
                allow_row_samples=True,
            ),
        )

        result = await tools_module.dry_run_graph_edits(
            "main.py", _free_code_node("quotes", _RAISE_WITH_ROWS)
        )

        assert "ValueError in step 2 ('logic') of node 'probe'" in result["error"]["message"]
        assert "Q-7781" in result["error"]["message"]


async def test_saved_free_code_step_text_is_masked_without_executable_source(
    steps_first_project: Path, monkeypatch: pytest.MonkeyPatch
):
    """Structured steps are configuration and stay readable; the free-code
    card's text is executable source and follows the policy."""

    import haute.assistant._tools as tools_module
    from haute.assistant._config import EgressPolicy

    steps = _guide_step_lists()[0]
    assert [step["kind"] for step in steps] == ["source", "free_code"]
    ops = [{"op": "update_node", "node": "august_totals", "config": {"steps": steps}}]
    plan = await tools_module.dry_run_graph_edits("main.py", ops)
    applied = await tools_module.apply_graph_plan("main.py", plan["plan_hash"])
    assert "error" not in applied, applied
    monkeypatch.setattr(
        tools_module,
        "resolve_egress_policy",
        lambda _root: EgressPolicy(
            trust="organization",
            max_sensitivity="restricted",
            allow_project_knowledge=False,
            allow_executable_source=False,
            allow_row_samples=False,
        ),
    )

    config = tools_module.get_node_config("main.py", "august_totals")["config"]

    assert config["steps"] == [
        steps[0],
        {**steps[1], "code": "<redacted: executable_source>"},
    ]
    assert config["code"] == "<redacted: executable_source>"
