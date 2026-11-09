"""The assistant's schema and inspection read tools.

``node_schema`` parses the saved pipeline, validates the target against the hierarchical graph (a
submodel placeholder or internal id is a boundary error, an unknown id an unknown-node error),
reproduces the production graph preparation and reads the schema without collecting;
``inspect_node`` checks each part against the egress policy; column profiles answer for authoring
states rather than refusing; and the engine is invoked through the patched
``haute.assistant._tools`` seam.
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
from unittest.mock import MagicMock

import polars as pl
import pytest

from haute._types import PipelineGraph
from tests import _assistant_tool_helpers
from tests._assistant_tool_helpers import (
    PIPELINE_SOURCE,
    _egress_policy,
    _egress_toml,
    _node,
    _policy,
    _profile,
)
from tests._project import use_project

# Fixtures shared with the other modules split from one suite.
project_root = _assistant_tool_helpers.project_root


# ---------------------------------------------------------------------------
# Fixtures — a real tmp project with a parquet source
# ---------------------------------------------------------------------------


def _columns(result: dict) -> dict[str, str]:
    assert "columns" in result, result
    return {column["name"]: column["dtype"] for column in result["columns"]}


# ---------------------------------------------------------------------------
# End-to-end schema resolution (no mocks)
# ---------------------------------------------------------------------------


class TestGetNodeSchemaEndToEnd:
    def test_source_node_schema_matches_file(self, project_root: Path):
        from haute.assistant._tools import node_schema

        result = node_schema("main.py", "quotes")
        cols = _columns(result)
        assert cols == {
            "quote_id": "String",
            "vehicle_year": "Int64",
            "notes": "String",
        }

    def test_downstream_node_reflects_transforms_and_preamble(self, project_root: Path):
        """Rename, drop, derived column, and the preamble-defined helper all
        resolve — proving flatten/preamble/engine preparation is wired."""

        from haute.assistant._tools import node_schema

        result = node_schema("main.py", "enriched")
        cols = _columns(result)
        assert "year" in cols and "vehicle_year" not in cols
        assert "notes" not in cols
        assert "age" in cols
        assert "flag" in cols  # created by the preamble helper add_flag

    def test_unknown_node_is_structured_error(self, project_root: Path):
        from haute.assistant._tools import node_schema

        result = node_schema("main.py", "ghost")
        assert result["error"]["code"] == "unknown_node"
        assert "ghost" in result["error"]["message"]

    def test_result_reports_each_input_by_its_code_visible_name(self, project_root: Path):
        """Authoring code against a node needs the columns arriving on it, not
        only the columns leaving it."""

        from haute.assistant._tools import node_schema

        result = node_schema("main.py", "enriched")
        inputs = result["inputs"]
        assert set(inputs) == {"quotes"}
        assert {column["name"] for column in inputs["quotes"]} == {
            "quote_id",
            "vehicle_year",
            "notes",
        }

    def test_source_node_without_inputs_omits_the_inputs_key(self, project_root: Path):
        from haute.assistant._tools import node_schema

        assert "inputs" not in node_schema("main.py", "quotes")


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
            allow_aggregate_statistics=False,
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
            allow_aggregate_statistics=False,
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


def _profiles_by_name(result: dict[str, object]) -> dict[str, dict[str, object]]:
    assert "error" not in result, result
    return {column["name"]: column for column in result["columns"]}  # type: ignore[index,union-attr]


class TestColumnProfiles:
    def test_group_by_output_is_profiled_under_an_admitted_preview(
        self,
        project_root: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        config_dir = project_root / "config" / "data_input"
        config_dir.mkdir(parents=True)
        (config_dir / "quotes.json").write_text(
            json.dumps(
                {
                    "input_type": "file",
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
                allow_aggregate_statistics=False,
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

    async def test_a_profile_refused_under_the_lock_is_the_calls_error(self, profile_project: Path):
        import haute.assistant._tools as tools_module

        result = await tools_module.build_tool_executor("main.py")(
            "inspect_node", {"node": "totals", "parts": ["profile"], "input": "no_such_input"}
        )

        assert result["error"]["code"] == "unknown_input"
        assert result["error"]["part"] == "profile"

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
            "inspect_node", {"node": "totals", "parts": ["profile"], "input": "claims"}
        )

        assert "error" not in result, result
        json.dumps(result, allow_nan=False)

    async def test_a_save_during_a_profile_waits_for_no_collection(
        self,
        profile_project: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        import haute.assistant._tools as tools_module

        async def collect_while_saving(*_args: object, **_kwargs: object) -> dict[str, object]:
            async with asyncio.timeout(5), tools_module.save_lock:
                return {"node": "totals", "saved_during_collection": True, "project_revision": "r"}

        monkeypatch.setattr(tools_module, "column_profiles", collect_while_saving)

        result = await tools_module.build_tool_executor("main.py")(
            "inspect_node", {"node": "totals", "parts": ["profile"], "input": "claims"}
        )

        assert result["profile"]["saved_during_collection"] is True

    def test_the_profile_worker_uses_the_calls_policy_without_reading_it_again(
        self,
        profile_project: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        import haute.assistant._tools as tools_module
        from haute.assistant._config import EgressPolicy

        policy = EgressPolicy(
            trust="organization",
            max_sensitivity="restricted",
            allow_project_knowledge=False,
            allow_executable_source=False,
            allow_row_samples=True,
            allow_aggregate_statistics=False,
        )

        def refuse(_root: Path) -> EgressPolicy:
            raise AssertionError("a spawned worker must not read the configuration")

        monkeypatch.setattr(tools_module, "resolve_egress_policy", refuse)
        monkeypatch.setattr(
            tools_module, "create_isolated_execution_context", lambda _budget: MagicMock()
        )
        seen: list[object] = []

        def collect(_request: object, _context: object) -> dict[str, object]:
            seen.append(tools_module._egress_policy())
            return {}

        monkeypatch.setattr(tools_module, "_collect_column_profile", collect)
        request = tools_module._ColumnProfileRequest(MagicMock(), "totals", None, policy)

        tools_module._run_column_profile_worker(request, MagicMock())

        assert seen == [policy]

    async def test_a_tool_call_resolves_the_egress_policy_once(
        self,
        profile_project: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        import haute.assistant._tools as tools_module

        original = tools_module.resolve_egress_policy
        resolved: list[object] = []

        def counting(root: Path):
            resolved.append(root)
            return original(root)

        monkeypatch.setattr(tools_module, "resolve_egress_policy", counting)

        result = await tools_module.build_tool_executor("main.py")(
            "inspect_node", {"node": "totals", "parts": ["schema", "config", "profile"]}
        )

        assert "error" not in result, result
        assert len(resolved) == 1

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
                "inspect_node", {"node": "totals", "parts": ["profile"], "input": "claims"}
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
                raise workers_module.WorkerStoppedError(reason)

        monkeypatch.setattr(workers_module, "interactive_worker_pool", _Pool)
        call = asyncio.ensure_future(
            tools_module.build_tool_executor("main.py", session_id="stop")(
                "inspect_node", {"node": "totals", "parts": ["profile"], "input": "claims"}
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
                allow_aggregate_statistics=False,
            ),
        )

        result = _profile("main.py", "enriched")

        assert result["error"]["code"] == "egress_policy_denied"
        assert result["error"]["required_policy"] == "allow_row_samples = true"


class TestInspectNode:
    """Each part of `inspect_node` is checked against the egress policy on its own."""

    @pytest.mark.parametrize(
        ("max_sensitivity", "row_samples", "answered", "withheld"),
        [
            (
                "internal",
                True,
                ["schema", "profile"],
                [{"part": "config", "required_policy": 'max_sensitivity = "restricted"'}],
            ),
            (
                "internal",
                False,
                ["schema"],
                [
                    {"part": "config", "required_policy": 'max_sensitivity = "restricted"'},
                    {"part": "profile", "required_policy": "allow_row_samples = true"},
                ],
            ),
            (
                "restricted",
                False,
                ["schema", "config"],
                [{"part": "profile", "required_policy": "allow_row_samples = true"}],
            ),
            ("restricted", True, ["schema", "config", "profile"], []),
        ],
    )
    async def test_a_denied_part_is_withheld_before_it_reads_while_the_others_answer(
        self,
        project_root: Path,
        monkeypatch: pytest.MonkeyPatch,
        max_sensitivity: str,
        row_samples: bool,
        answered: list[str],
        withheld: list[dict[str, str]],
    ):
        import haute.assistant._tools as tools_module
        from haute.assistant._config import EgressPolicy

        monkeypatch.setattr(
            tools_module,
            "resolve_egress_policy",
            lambda _root: EgressPolicy(
                trust="organization",
                max_sensitivity=max_sensitivity,  # type: ignore[arg-type]
                allow_project_knowledge=False,
                allow_executable_source=False,
                allow_row_samples=row_samples,
                allow_aggregate_statistics=False,
            ),
        )
        read: list[str] = []
        for part, name in (("config", "node_config"), ("profile", "column_profiles")):
            original = getattr(tools_module, name)

            def spy(*args: object, _part: str = part, _original=original, **kwargs: object):
                read.append(_part)
                return _original(*args, **kwargs)

            monkeypatch.setattr(tools_module, name, spy)

        result = await tools_module.build_tool_executor("main.py")(
            "inspect_node", {"node": "enriched", "parts": ["profile", "config", "schema"]}
        )

        assert "error" not in result, result
        assert [part for part in ("schema", "config", "profile") if part in result] == answered
        assert result.get("withheld", []) == withheld
        assert read == [part for part in answered if part != "schema"]
        assert {column["name"] for column in result["schema"]["columns"]} >= {"year", "age"}
        assert len(result["project_revision"]) == 64

    async def test_a_call_whose_every_part_is_denied_is_refused(
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
                allow_aggregate_statistics=False,
            ),
        )
        monkeypatch.setattr(
            tools_module,
            "node_schema",
            lambda *_args: (_ for _ in ()).throw(AssertionError("a denied part never reads")),
        )

        result = await tools_module.build_tool_executor("main.py")(
            "inspect_node", {"node": "enriched", "parts": ["schema", "config"]}
        )

        error = result["error"]
        assert error["code"] == "egress_policy_denied"
        assert error["retryable"] is False
        assert error["withheld"] == [
            {"part": "schema", "required_policy": 'max_sensitivity = "internal"'},
            {"part": "config", "required_policy": 'max_sensitivity = "internal"'},
        ]
        assert "available" not in error

    async def test_a_denied_config_part_says_which_parts_the_policy_permits(
        self, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """The model that asked only for a withheld config part learns that the
        schema part, which lists output columns and struct fields, is permitted."""

        import haute.assistant._tools as tools_module

        monkeypatch.setattr(
            tools_module,
            "resolve_egress_policy",
            lambda _root: _policy(max_sensitivity="internal", executable=False),
        )

        result = await tools_module.build_tool_executor("main.py")(
            "inspect_node", {"node": "enriched", "parts": ["config"]}
        )

        error = result["error"]
        assert error["code"] == "egress_policy_denied"
        assert error["available"] == ["schema"]
        assert "The schema part is permitted" in error["message"]
        assert "struct" in error["message"]

    async def test_input_without_the_profile_part_is_an_invalid_request(self, project_root: Path):
        from haute.assistant._tools import build_tool_executor

        result = await build_tool_executor("main.py")(
            "inspect_node", {"node": "enriched", "input": "quotes"}
        )

        assert result["error"]["code"] == "invalid_request"
        assert result["error"]["validation_reason"] == "input_without_profile"
        assert result["error"]["retryable"] is True

    async def test_a_call_whose_every_part_fails_fails_naming_the_first(self, project_root: Path):
        """Only a call no requested part answers is an error, and it carries
        the first failing part's error with `part` naming it."""

        from haute.assistant._tools import build_tool_executor

        result = await build_tool_executor("main.py")(
            "inspect_node", {"node": "ghost", "parts": ["schema", "config"]}
        )

        assert result["error"]["code"] == "unknown_node"
        assert result["error"]["part"] == "schema"


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
                allow_aggregate_statistics=False,
            ),
        )

        config = tools_module.node_config("main.py", "enriched")["config"]

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
        from haute.assistant._tools import node_schema

        result = node_schema("main.py", "combined")

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
        from haute.assistant._tools import node_schema
        from haute.routes._helpers import parse_pipeline_to_graph
        from haute.routes._save_pipeline import SavePipelineService

        graph = parse_pipeline_to_graph(project_root / "main.py")
        table = {
            "factors": ["quote_id"],
            "output_column": "rate",
            "default_value": "1.0",
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

        blank = node_schema("main.py", "blank")
        rated = node_schema("main.py", "rated")

        assert blank["unresolved_reason"] == "node_has_no_code", blank
        assert set(blank["inputs"]) == {"quotes"}
        assert "rate" in _columns(rated)

    def test_group_by_node_resolves_because_nothing_is_collected(self, project_root: Path):
        """Schema resolution materialises nothing, so the engine's group-by
        memory-admission gate does not apply to it. Before this, every
        aggregation the assistant was asked to author was unresolvable."""

        (project_root / "main.py").write_text(GROUP_BY_SOURCE, encoding="utf-8")
        from haute.assistant._tools import node_schema

        result = node_schema("main.py", "by_year")

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
        from haute.assistant._tools import node_schema

        result = node_schema("main.py", "enriched")

        assert result["error"]["code"] == "schema_unresolvable"
        assert "no_such_column" in result["error"]["message"]

    def test_schema_resolution_never_collects(
        self, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """The collect-poisoning invariant: plan construction plus
        ``collect_schema()`` must never invoke ``LazyFrame.collect``."""

        from haute.assistant._tools import node_schema

        def poisoned_collect(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
            raise AssertionError("the schema part must never collect data")

        monkeypatch.setattr(pl.LazyFrame, "collect", poisoned_collect)
        result = node_schema("main.py", "enriched")
        assert "columns" in result, result

    def test_output_node_schema_resolves_without_collecting(
        self, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """EXEC-P08: an OUTPUT terminal used to assemble its whole document while
        the graph was being built, so the collect-poisoning invariant did not
        hold for it. ``schema_only=True`` now reaches the OUTPUT builder, which
        describes the document from its mapping and source schemas instead."""

        import haute.assistant._tools as tools_module
        from haute.assistant._tools import node_schema
        from tests.conftest import make_edge, make_graph, make_output_config

        use_project(project_root)

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
                                "input_type": "file",
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
            raise AssertionError("the schema part must never collect data")

        monkeypatch.setattr(pl.LazyFrame, "collect", poisoned_collect)
        result = node_schema("main.py", "out")
        assert _columns(result) == {"quote_id": "String", "vehicle_year": "Int64"}


# ---------------------------------------------------------------------------
# Pre-flatten target validation (crafted hierarchical graph)
# ---------------------------------------------------------------------------


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

    def test_submodel_internal_child_is_boundary_error_never_resolved(
        self, patched_parse, monkeypatch: pytest.MonkeyPatch
    ):
        """The id exists in the FLATTENED executable graph, so this asserts
        validation happens against the hierarchical graph before flattening."""

        import haute.assistant._tools as tools_module

        def must_not_execute(*args, **kwargs):  # noqa: ANN002, ANN003
            raise AssertionError("engine must not run for a boundary-rejected target")

        monkeypatch.setattr(tools_module, "execute_lazy_graph", must_not_execute)
        from haute.assistant._tools import node_schema

        result = node_schema("main.py", "inner_child")
        assert result["error"]["code"] == "submodel_boundary"

    def test_id_found_nowhere_is_unknown_node(self, patched_parse):
        from haute.assistant._tools import node_schema

        result = node_schema("main.py", "nowhere")
        assert result["error"]["code"] == "unknown_node"

    def test_a_boundary_error_is_final_and_says_to_block(self, patched_parse):
        """Nothing the model corrects makes a submodel's nodes readable or editable,
        so the error is not retryable and says to report a blocker."""

        from haute.assistant._tools import _with_retryable, node_schema

        error = _with_retryable(node_schema("main.py", "inner_child"))["error"]

        assert (error["code"], error["retryable"]) == ("submodel_boundary", False)
        assert "cannot be read or edited by the assistant" in error["message"]
        assert "BLOCKED:" in error["fix"] and "editor" in error["fix"]


@pytest.fixture()
def submodel_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The evaluation's submodel project: `policies` feeds the `vehicle_factors`
    occurrence's `vehicles` port, whose `factored` port feeds `premium`."""

    import shutil
    from collections import OrderedDict

    from haute.assistant import _tools

    root = tmp_path / "s"
    shutil.copytree(
        Path(__file__).parent / "assistant_eval" / "projects" / "submodel_pricing", root
    )
    use_project(root)
    use_project(root)  # restored by the autouse _restore_project_root
    monkeypatch.setattr(_tools, "_BRIEF_CACHE", OrderedDict())
    _egress_toml(root, max_sensitivity="restricted")
    return root


_POLICY_COLUMNS = ["policy_id", "vehicle_year", "base_rate", "region"]


class TestSubmodelOccurrenceSchema:
    """A top-level submodel occurrence's columns are metadata the model may read,
    named by its ports; its configuration, code and inner nodes stay off limits."""

    async def test_the_schema_part_names_the_ports_and_what_the_submodel_adds(
        self, submodel_project: Path
    ):
        from haute.assistant._tools import build_tool_executor

        result = await build_tool_executor("pipeline.py")(
            "inspect_node", {"node": "vehicle_factors", "parts": ["schema"]}
        )

        schema = result["schema"]
        assert {port: [c["name"] for c in cols] for port, cols in schema["ports"].items()} == {
            "factored": [*_POLICY_COLUMNS, "vehicle_age", "vehicle_factor"]
        }
        assert {name: [c["name"] for c in cols] for name, cols in schema["inputs"].items()} == {
            "vehicles": _POLICY_COLUMNS
        }

    async def test_a_consumer_names_its_input_by_the_occurrence_port(self, submodel_project: Path):
        from haute.assistant._tools import node_schema

        schema = node_schema("pipeline.py", "premium")

        assert list(schema["inputs"]) == ["factored"]

    @pytest.mark.parametrize("extra_output", [False, True])
    async def test_instance_inputs_and_brief_use_the_instance_output_name(
        self, submodel_project: Path, extra_output: bool
    ):
        from haute.assistant._render import render_turn_context
        from haute.assistant._tools import build_tool_executor, build_turn_context

        input_name = "vehicle_copy_factored" if extra_output else "vehicle_copy"
        path = submodel_project / "pipeline.py"
        source = path.read_text(encoding="utf-8")
        source = source.replace("def premium(factored:", f"def premium({input_name}:")
        source = source.replace("df = factored", f"df = {input_name}")
        source = source.replace(
            'pipeline.connect("vehicle_factors", "premium", source_port="factored")',
            'pipeline.submodel(vehicle_factors.submodel, "vehicle_copy", '
            'instance_of="vehicle_factors")\n'
            'pipeline.connect("policies", "vehicle_copy", target_port="vehicles")\n'
            'pipeline.connect("vehicle_copy", "premium", source_port="factored")',
        )
        # The premium transform's first step is its code's `df = <input>` (READ-22).
        path.write_text(source, encoding="utf-8", newline="\n")
        if extra_output:
            definition_path = submodel_project / "modules/vehicle_factors.py"
            definition = definition_path.read_text(encoding="utf-8")
            definition_path.write_text(
                definition.replace(
                    "output_ports=[",
                    'output_ports=[\n        {"name": "audit", '
                    '"source": {"node_id": "vehicle_factor"}},',
                ),
                encoding="utf-8",
                newline="\n",
            )

        result = await build_tool_executor("pipeline.py")(
            "inspect_node", {"node": "premium", "parts": ["schema"]}
        )
        assert {
            name: [c["name"] for c in cols] for name, cols in result["schema"]["inputs"].items()
        } == {input_name: [*_POLICY_COLUMNS, "vehicle_age", "vehicle_factor"]}
        context = render_turn_context(
            build_turn_context(
                "pipeline.py", _policy(max_sensitivity="restricted", executable=False)
            )
        )
        assert f"- input `{input_name}` from `vehicle_copy`: [" in context
        assert (
            "- output port `factored` (add_edge source_handle `out__factored`, read as input "
            f"`{input_name}`): [" in context
        )
        if extra_output:
            assert (
                "- output port `audit` (add_edge source_handle `out__audit`, read as input "
                "`vehicle_copy_audit`): [" in context
            )

    @pytest.mark.parametrize(
        ("node", "parts", "schema_readable"),
        [("vehicle_factors", ["config"], True), ("vehicle_age", ["schema"], False)],
    )
    async def test_its_config_and_inner_nodes_stay_off_limits(
        self, submodel_project: Path, node: str, parts: list[str], schema_readable: bool
    ):
        from haute.assistant._tools import build_tool_executor

        result = await build_tool_executor("pipeline.py")(
            "inspect_node", {"node": node, "parts": parts}
        )

        error = result["error"]
        assert (error["code"], error["retryable"]) == ("submodel_boundary", False)
        assert "BLOCKED:" in error["fix"]
        assert ('parts ["schema"]' in error["fix"]) is schema_readable

    def test_the_brief_lists_the_occurrence_ports_and_the_consumer_input(
        self, submodel_project: Path
    ):
        from haute.assistant._render import render_turn_context
        from haute.assistant._tools import build_turn_context

        policy = _policy(max_sensitivity="restricted", executable=False)
        context = render_turn_context(build_turn_context("pipeline.py", policy))

        assert (
            '- input `vehicles` from `policies`: ["policy_id", "vehicle_year", "base_rate", '
            '"region"]' in context
        )
        assert (
            "- output port `factored` (add_edge source_handle `out__factored`, read as input "
            '`factored`): ["policy_id", "vehicle_year", "base_rate", "region", '
            '"vehicle_age", "vehicle_factor"]' in context
        )
        assert "- input `factored` from `vehicle_factors`: [" in context
        assert "submodel_runtime" not in context and "not resolved" not in context


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
        from haute.assistant._tools import node_schema

        result = node_schema("main.py", "enriched")
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
        from haute.assistant._tools import node_schema

        result = node_schema("main.py", "quotes")
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
        from haute.assistant._tools import node_schema

        result = node_schema("main.py", "quotes")
        assert result["error"]["code"] == "schema_unresolvable"
        assert "Rebuild" in result["error"]["message"]
