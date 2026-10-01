"""The assistant's data check (ASSIST-41): its engine, measured on seeded candidate graphs.

Most tests run the worker half in-process (``measure_candidate``) under an
admitted context, which is the code the interactive worker runs; the tests
that prove the worker boundary (busy slots, pre-emption, deadlines, cold
generations hashed only in the worker) run in process mode, which the suite's
autouse fixture otherwise replaces with thread mode. The last section proves
``inspect_node``'s data part (ASSIST-42), the same check over a saved node's
lineage.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from haute._execution_admission import create_admitted_execution_context
from haute._execution_context import ExecutionCancellationToken, ExecutionProfile
from haute._sandbox import set_project_root
from haute._types import PipelineGraph
from haute.assistant import _data_check as data_check
from haute.assistant._config import EgressPolicy
from haute.assistant._data_check import (
    DATA_CHECK_VIEW,
    NODE_DATA_OMITTED_NOTE,
    NODE_DATA_VIEW,
    DataCheckBinding,
    DataCheckJob,
    DataCheckRequest,
    DataCheckResult,
    NodeDataCheckRequest,
    changed_nodes,
    data_check_visibility,
    fit_data_check,
    fit_node_data_check,
    measure_candidate,
    run_data_check,
    server_exclusions,
)
from haute.assistant._ops import SemanticChanges, SemanticDiff
from haute.graph_utils import flatten_graph
from tests.conftest import build_test_input_snapshot, make_file_input_config

# ---------------------------------------------------------------------------
# Seeded projects
# ---------------------------------------------------------------------------

_TOML = (
    '[assistant]\nprovider = "openai"\nmodel = "test"\n'
    'base_url = "https://api.openai.com/v1"\n'
    '[assistant.egress]\ntrust = "organization"\nmax_sensitivity = "internal"\n'
    "allow_project_knowledge = false\nallow_executable_source = false\n"
    "allow_row_samples = false\nallow_aggregate_statistics = true\n"
)

_POLICY = EgressPolicy(
    trust="organization",
    max_sensitivity="internal",
    allow_project_knowledge=False,
    allow_executable_source=False,
    allow_row_samples=False,
    allow_aggregate_statistics=True,
)

# Ten quotes over four regions; the rating table covers two of them (a 0.6
# miss share), the region table three (seven of ten quotes match).
_QUOTES = {
    "quote_id": [f"Q-{index:03d}" for index in range(10)],
    "region": ["north", "north", "south", "south", "east", "east", "east", "west", "west", "west"],
    "premium": [101.5, 202.5, 303.5, 404.5, 505.5, 606.5, 707.5, 808.5, 909.5, 1010.5],
}


@pytest.fixture()
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    set_project_root(tmp_path)
    (tmp_path / "haute.toml").write_text(_TOML, encoding="utf-8")
    (tmp_path / "main.py").write_text("", encoding="utf-8")
    (tmp_path / "data").mkdir()
    pl.DataFrame(_QUOTES).write_parquet(tmp_path / "data" / "quotes.parquet")
    pl.DataFrame({"region": ["north", "south", "east"], "loading": [1.1, 0.9, 1.0]}).write_parquet(
        tmp_path / "data" / "regions.parquet"
    )
    return tmp_path


def _node(node_id: str, node_type: str, config: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return {
        "id": node_id,
        "data": {"label": node_id, "nodeType": node_type, "config": config or {}},
    }


def _edge(source: str, target: str, *, role: str | None = None) -> dict[str, Any]:
    return {
        "id": f"e_{source}_{target}",
        "source": source,
        "target": target,
        "sourceHandle": None,
        "targetHandle": role,
    }


def _graph(
    project: Path,
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    *,
    scenario: str = "live",
) -> PipelineGraph:
    return PipelineGraph.model_validate(
        {
            "nodes": nodes,
            "edges": edges,
            "source_file": str(project / "main.py"),
            "active_source": scenario,
        }
    )


def _parquet(name: str) -> dict[str, Any]:
    return _node(name, "dataInput", make_file_input_config(f"data/{name}.parquet"))


def _code(node_id: str, code: str) -> dict[str, Any]:
    return _node(node_id, "polars", {"code": code})


def _diff(*added: str) -> SemanticDiff:
    return SemanticDiff(complete=SemanticChanges(nodes_added=tuple(added)))


def _job(graph: PipelineGraph, *changed: str, policy: EgressPolicy = _POLICY) -> DataCheckJob:
    order = changed_nodes(graph, _diff(*changed))
    exclusions = server_exclusions(graph, flatten_graph(graph), order)
    candidates = tuple(node for node in order if exclusions[node] is None)
    return DataCheckJob(
        candidate_graph=graph,
        scenario=graph.active_source,
        candidates=candidates,
        cap_order=candidates,
        column=None,
        submitted=(),
        policy=policy,
    )


def _measure(graph: PipelineGraph, *changed: str, **kwargs: Any) -> data_check.WorkerOutcome:
    """The worker half, in-process, under an admitted preview context."""
    context = create_admitted_execution_context(
        operation="assistant_data_check", profile=ExecutionProfile.PREVIEW_EAGER
    )
    try:
        return measure_candidate(_job(graph, *changed, **kwargs), context)
    finally:
        context.release_admission()


def _findings(outcome: data_check.WorkerOutcome, kind: str) -> list[Mapping[str, Any]]:
    return [finding for finding in outcome.findings if finding["kind"] == kind]


def _view(outcome: data_check.WorkerOutcome, graph: PipelineGraph, *changed: str) -> dict[str, Any]:
    """The model-facing object the server builds from a worker outcome."""
    binding = DataCheckBinding("plan", "digest", graph.active_source)
    result = data_check._checked_result(
        binding, outcome, list(changed), {}, time.monotonic(), inspection=False
    )
    DATA_CHECK_VIEW.validate_python(result.check)
    return dict(result.check)


# ---------------------------------------------------------------------------
# Seeded findings
# ---------------------------------------------------------------------------


def _seeded_graph(project: Path) -> PipelineGraph:
    return _graph(
        project,
        [
            _parquet("quotes"),
            _parquet("regions"),
            _node(
                "region_band",
                "banding",
                {
                    "factors": [
                        {
                            "banding": "categorical",
                            "column": "region",
                            "outputColumn": "region_group",
                            "rules": [
                                {"value": "Atlantis", "assignment": "Lost"},
                                {"value": "Avalon", "assignment": "Lost"},
                            ],
                            "default": "Other",
                        }
                    ]
                },
            ),
            _node(
                "region_rate",
                "ratingStep",
                {
                    "tables": [
                        {
                            "factors": ["region"],
                            "outputColumn": "region_factor",
                            "entries": [
                                {"region": "north", "value": 1.25},
                                {"region": "south", "value": 0.75},
                                {"region": "polar", "value": 2.5},
                            ],
                            "defaultValue": "1.0",
                        }
                    ]
                },
            ),
            _code("big_only", "df = quotes.filter(pl.col('premium') > 5000.0)"),
            _node(
                "with_regions",
                "edgeJoin",
                {"how": "left", "on": "region"},
            ),
        ],
        [
            _edge("quotes", "region_band"),
            _edge("quotes", "region_rate"),
            _edge("quotes", "big_only"),
            _edge("quotes", "with_regions", role="base"),
            _edge("regions", "with_regions", role="join"),
        ],
    )


def test_seeded_scenarios_report_their_findings(project: Path) -> None:
    graph = _seeded_graph(project)
    changed = ("region_band", "region_rate", "big_only", "with_regions")
    outcome = _measure(graph, *changed)

    assert outcome.kind == "checked"
    assert _findings(outcome, "banding_all_default") == [
        {
            "kind": "banding_all_default",
            "severity": "advisory",
            "truncated": False,
            "factor": 0,
            "output_column": "region_group",
            "rows": 10,
            "node": "region_band",
        }
    ]
    [misses] = _findings(outcome, "rating_misses")
    assert (misses["severity"], misses["missed"], misses["rows"], misses["share"]) == (
        "advisory",
        6,
        10,
        0.6,
    )
    [unused] = _findings(outcome, "rating_entries_unused")
    assert (unused["unused_entries"], unused["entries"]) == (1, 3)
    [emptied] = _findings(outcome, "rows_emptied")
    assert (emptied["node"], emptied["port"], emptied["input_rows"]) == ("big_only", None, 10)
    [partial] = _findings(outcome, "join_partial")
    assert (partial["severity"], partial["matched_base_rows"], partial["base_rows"]) == (
        "informational",
        7,
        10,
    )
    assert partial["share"] == 0.7
    view = _view(outcome, graph, *changed)
    band = next(node for node in view["nodes"] if node["node"] == "region_band")
    assert band["banding"][0]["rule_rows"] == [0, 0]
    assert band["banding"][0]["defaulted"] == 10
    assert band["outputs"][0]["columns"] == [
        {"name": "region_group", "kind": "new", "nulls": 0, "share": 0.0}
    ]
    join = next(node for node in view["nodes"] if node["node"] == "with_regions")["join"]
    assert join == {
        "how": "left",
        "validate": None,
        "keys": {"base": ["region"], "join": ["region"]},
        "base_rows": 10,
        "join_rows": 3,
        "matched_base_rows": 7,
        "duplicate_key_tuples": {"base": 4, "join": 0},
        "truncated": False,
    }
    # Advisory findings come first, then the changed nodes' order.
    severities = [finding["severity"] for finding in outcome.findings]
    assert severities == sorted(severities, key=lambda value: value != "advisory")


def test_a_many_to_one_join_on_duplicate_keys_reports_its_validation(project: Path) -> None:
    pl.DataFrame({"region": ["north", "north", "south"], "loading": [1.1, 1.2, 0.9]}).write_parquet(
        project / "data" / "regions.parquet"
    )
    graph = _graph(
        project,
        [
            _parquet("quotes"),
            _parquet("regions"),
            _node("joined", "edgeJoin", {"how": "left", "on": "region", "validate": "m:1"}),
        ],
        [_edge("quotes", "joined", role="base"), _edge("regions", "joined", role="join")],
    )

    outcome = _measure(graph, "joined")

    record = outcome.records["joined"]
    assert record["status"] == "failed"
    assert record["error"]["class"] == "authored_code"
    assert record["join"]["duplicate_key_tuples"] == {"base": 4, "join": 1}
    assert [finding["kind"] for finding in outcome.findings] == [
        "join_validation_failed",
        "join_partial",
    ]
    validation = outcome.findings[0]
    assert (validation["validate"], validation["side"]) == ("m:1", "join")


def test_a_rating_miss_guard_that_raises_keeps_its_measurements(project: Path) -> None:
    """One miss in twenty is informational, but a guard that raised makes it advisory
    and replaces the node's execution failure."""
    pl.DataFrame({"region": ["north"] * 19 + ["west"], "premium": [1.0] * 20}).write_parquet(
        project / "data" / "quotes.parquet"
    )
    graph = _graph(
        project,
        [
            _parquet("quotes"),
            _node(
                "rated",
                "ratingStep",
                {
                    "tables": [
                        {
                            "factors": ["region"],
                            "outputColumn": "region_factor",
                            "entries": [{"region": "north", "value": 1.5}],
                        }
                    ]
                },
            ),
        ],
        [_edge("quotes", "rated")],
    )

    outcome = _measure(graph, "rated")

    record = outcome.records["rated"]
    assert record["status"] == "failed"
    assert (record["error"]["class"], record["error"]["type"]) == (
        "validation",
        "RatingTableMissError",
    )
    assert record["error"]["text"] is None and record["error"]["withheld"]
    assert record["inputs"] == [{"input": "quotes", "rows": 20, "truncated": False}]
    assert record["rating"][0]["missed"] == 1
    assert [(finding["kind"], finding["severity"]) for finding in outcome.findings] == [
        ("rating_misses", "advisory")
    ]


def test_a_failing_ancestor_shared_by_two_checked_nodes_is_reported_once(project: Path) -> None:
    graph = _graph(
        project,
        [
            _parquet("quotes"),
            _code("as_number", "df = quotes.with_columns(pl.col('region').cast(pl.Int64))"),
            _code("first_reader", "df = as_number.select('region')"),
            _code("second_reader", "df = as_number.select('premium')"),
            _code("independent", "df = quotes.with_columns(net=pl.col('premium') * 0.8)"),
        ],
        [
            _edge("quotes", "as_number"),
            _edge("as_number", "first_reader"),
            _edge("as_number", "second_reader"),
            _edge("quotes", "independent"),
        ],
    )

    outcome = _measure(graph, "first_reader", "second_reader", "independent")

    for reader in ("first_reader", "second_reader"):
        assert outcome.records[reader] == {
            "node": reader,
            "status": "upstream_failed",
            "failed_node": "as_number",
            "at_or_upstream": True,
        }
    assert outcome.records["independent"]["status"] == "checked"
    assert outcome.records["independent"]["outputs"][0]["rows"] == 10
    [failure] = _findings(outcome, "execution_failed")
    assert (failure["node"], failure["at_or_upstream"]) == ("as_number", True)
    assert failure["error"]["class"] == "authored_code"
    assert failure["error"]["text"] is None


def test_a_check_result_carries_no_data_or_configuration_value(project: Path) -> None:
    secret_rows = {
        "quote_id": ["ZEBRA-7731", "ZEBRA-7732", "ZEBRA-7733"],
        "region": ["Krakatoa", "Krakatoa", "Vesuvius"],
        "premium": [98765.4321, 87654.3219, 76543.2198],
    }
    pl.DataFrame(secret_rows).write_parquet(project / "data" / "quotes.parquet")
    graph = _graph(
        project,
        [
            _parquet("quotes"),
            _node(
                "banded",
                "banding",
                {
                    "factors": [
                        {
                            "banding": "categorical",
                            "column": "region",
                            "outputColumn": "zone",
                            "rules": [{"value": "Krakatoa", "assignment": "MAGMA-LABEL"}],
                            "default": "DEFAULT-LABEL",
                        }
                    ]
                },
            ),
            _node(
                "rated",
                "ratingStep",
                {
                    "tables": [
                        {
                            "factors": ["region"],
                            "outputColumn": "region_factor",
                            "entries": [{"region": "Pompeii", "value": 3.14159}],
                            "defaultValue": "2.71828",
                        }
                    ]
                },
            ),
            _code("filtered", "df = quotes.filter(pl.col('premium') > 55555.5)"),
            _code("broken", "df = quotes.with_columns(pl.col('region').cast(pl.Int64))"),
        ],
        [
            _edge("quotes", "banded"),
            _edge("quotes", "rated"),
            _edge("quotes", "filtered"),
            _edge("quotes", "broken"),
        ],
    )

    changed = ("banded", "rated", "filtered", "broken")
    outcome = _measure(graph, *changed)
    view = json.dumps(_view(outcome, graph, *changed))

    values = [
        *secret_rows["quote_id"],
        *secret_rows["region"],
        "98765",
        "87654",
        "76543",
        "MAGMA-LABEL",
        "DEFAULT-LABEL",
        "Pompeii",
        "3.14159",
        "2.71828",
        "55555",
    ]
    assert [value for value in values if value in view] == []
    assert outcome.records["broken"]["error"]["withheld"]


def test_a_frame_above_the_row_bound_is_cut_and_marked_truncated(project: Path) -> None:
    pl.DataFrame({"amount": pl.int_range(0, 1_000_001, eager=True)}).write_parquet(
        project / "data" / "big.parquet"
    )
    graph = _graph(
        project,
        [_parquet("big"), _code("doubled", "df = big.with_columns(twice=pl.col('amount') * 2)")],
        [_edge("big", "doubled")],
    )

    outcome = _measure(graph, "doubled")

    record = outcome.records["doubled"]
    assert record["inputs"] == [{"input": "big", "rows": 1_000_000, "truncated": True}]
    port = record["outputs"][0]
    assert (port["rows"], port["truncated"]) == (1_000_000, True)
    assert port["columns"] == [{"name": "twice", "kind": "new", "nulls": 0, "share": 0.0}]


# ---------------------------------------------------------------------------
# Eligibility
# ---------------------------------------------------------------------------


def _csv_input(project: Path, name: str, *, build: bool = True) -> dict[str, Any]:
    pl.DataFrame({"region": ["north", "south"], "premium": [1.0, 2.0]}).write_csv(
        project / "data" / f"{name}.csv"
    )
    config = make_file_input_config(f"data/{name}.csv")
    if build:
        build_test_input_snapshot(config, base_dir=project)
    return _node(name, "dataInput", config)


def test_each_eligibility_reason_names_its_blocking_node_in_precedence(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import haute._io as io_module

    loads: list[str] = []
    monkeypatch.setattr(
        io_module, "_load_external_object_uncached", lambda *args: loads.append(str(args))
    )
    monkeypatch.setattr(data_check, "DATA_CHECK_MAX_NODES", 2)
    graph = _graph(
        project,
        [
            _parquet("quotes"),
            _node("pickled", "externalFile", {"path": "model.pkl", "fileType": "pickle"}),
            _node(
                "registered",
                "modelScore",
                {"sourceType": "registered", "registered_model": "m", "version": "1"},
            ),
            _csv_input(project, "unbuilt", build=False),
            _csv_input(project, "built"),
            _code("loads_pickle", "df = pickled"),
            _code("pickle_and_registry", "df = registered"),
            _code("scores", "df = registered"),
            _code("reads_unbuilt", "df = unbuilt"),
            _code("first", "df = quotes"),
            _code("second", "df = built"),
            _code("third", "df = quotes.head(1)"),
            _node("written", "dataOutput", {"path": "out.parquet"}),
        ],
        [
            _edge("quotes", "pickled"),
            _edge("pickled", "loads_pickle"),
            _edge("pickled", "registered"),
            _edge("registered", "pickle_and_registry"),
            _edge("registered", "scores"),
            _edge("unbuilt", "reads_unbuilt"),
            _edge("built", "second"),
            _edge("quotes", "first"),
            _edge("quotes", "third"),
            _edge("quotes", "written"),
        ],
    )
    changed = (
        "loads_pickle",
        "pickle_and_registry",
        "scores",
        "reads_unbuilt",
        "first",
        "second",
        "third",
        "written",
    )

    order = changed_nodes(graph, _diff(*changed))
    exclusions = server_exclusions(graph, flatten_graph(graph), order)
    reasons = {
        node: None if exclusion is None else (exclusion.reason, exclusion.blocking_node)
        for node, exclusion in exclusions.items()
    }
    assert reasons == {
        "loads_pickle": ("artifact_in_lineage", "pickled"),
        "pickle_and_registry": ("artifact_in_lineage", "pickled"),
        "scores": ("artifact_in_lineage", "pickled"),
        "reads_unbuilt": ("input_not_prepared", "unbuilt"),
        "first": None,
        "second": None,
        "third": None,
        "written": ("sink_only", None),
    }
    remedy = exclusions["reads_unbuilt"]
    assert remedy is not None and remedy.remedy is not None and "unbuilt" in remedy.remedy

    capped = _measure(graph, *changed)
    assert {node: record["status"] for node, record in capped.records.items()} == {
        "first": "checked",
        "second": "checked",
        "third": "not_checked",
    }
    assert capped.records["third"]["reason"] == "node_cap"

    # A source rewritten after its snapshot was built is stale in the worker,
    # which frees its place under the cap.
    (project / "data" / "built.csv").write_text("region,premium\neast,9.0\n", encoding="utf-8")
    demoted = _measure(graph, *changed)
    assert demoted.records["second"]["reason"] == "input_not_prepared"
    assert demoted.records["second"]["blocking_node"] == "built"
    assert demoted.records["third"]["status"] == "checked"
    assert loads == []


def test_a_registered_model_and_an_uncached_run_model_are_not_local(project: Path) -> None:
    graph = _graph(
        project,
        [
            _parquet("quotes"),
            _node(
                "uncached",
                "modelScore",
                {"sourceType": "run", "run_id": "run1", "artifact_path": "model.cbm"},
            ),
            _node(
                "registered",
                "modelScore",
                {"sourceType": "registered", "registered_model": "m", "version": "1"},
            ),
            _code("uses_uncached", "df = uncached"),
            _code("uses_registered", "df = registered"),
        ],
        [
            _edge("quotes", "uncached"),
            _edge("quotes", "registered"),
            _edge("uncached", "uses_uncached"),
            _edge("registered", "uses_registered"),
        ],
    )
    exclusions = server_exclusions(
        graph, flatten_graph(graph), ["uses_uncached", "uses_registered"]
    )
    uncached, registered = exclusions["uses_uncached"], exclusions["uses_registered"]
    assert uncached is not None and registered is not None
    assert (uncached.reason, uncached.blocking_node) == ("artifact_not_local", "uncached")
    assert uncached.remedy is not None and "uncached" in uncached.remedy
    assert (registered.reason, registered.blocking_node, registered.remedy) == (
        "artifact_not_local",
        "registered",
        None,
    )


def test_a_cached_model_that_disappears_is_a_failure_never_a_registry_call(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from haute._mlflow_io import disk_cached_run_model

    def no_registry(**_kwargs: object) -> None:
        raise AssertionError("the check must never resolve a model through MLflow")

    monkeypatch.setattr("haute._mlflow_io.resolve_mlflow_source", no_registry)
    graph = _model_graph(project)
    cached = disk_cached_run_model(run_id="run1", artifact_path="model.cbm", destination="")
    assert cached is not None
    job = _job(graph, "uses_score")
    assert job.candidates == ("uses_score",)
    cached.model_path.unlink()

    context = create_admitted_execution_context(
        operation="assistant_data_check", profile=ExecutionProfile.PREVIEW_EAGER
    )
    try:
        outcome = measure_candidate(job, context)
    finally:
        context.release_admission()

    assert outcome.records["uses_score"]["failed_node"] == "scored"
    [failure] = _findings(outcome, "execution_failed")
    assert (failure["error"]["class"], failure["error"]["type"]) == (
        "haute",
        "ModelNotInDiskCacheError",
    )


def test_a_refresh_in_progress_makes_the_nodes_that_read_it_not_prepared(project: Path) -> None:
    from haute._input_preparation import (
        _acquire_single_flight,
        _release_single_flight,
        snapshot_backed_inputs,
    )
    from haute._input_providers import source_cache_identity

    graph = _graph(
        project,
        [_csv_input(project, "refreshing"), _code("reads", "df = refreshing")],
        [_edge("refreshing", "reads")],
    )
    [(_node_id, _kind, config)] = snapshot_backed_inputs(["refreshing"], graph.node_map)
    digest = source_cache_identity(config, base_dir=project).digest
    assert _acquire_single_flight(digest) is None
    try:
        exclusion = server_exclusions(graph, flatten_graph(graph), ["reads"])["reads"]
    finally:
        _release_single_flight(digest)
    assert exclusion is not None
    assert (exclusion.reason, exclusion.blocking_node) == ("input_not_prepared", "refreshing")
    assert server_exclusions(graph, flatten_graph(graph), ["reads"])["reads"] is None


def test_a_preamble_only_plan_changes_no_node(project: Path) -> None:
    graph = _seeded_graph(project)
    assert changed_nodes(graph, SemanticDiff(preamble_changed=True)) == []
    assert changed_nodes(graph, _diff("region_band", "big_only")) == ["big_only", "region_band"]


def test_a_check_writes_no_cache_entry(project: Path) -> None:
    graph = _graph(
        project,
        [
            _csv_input(project, "cached_input"),
            _parquet("quotes"),
            _code("reads_both", "df = cached_input.join(quotes, on='region', how='left')"),
        ],
        [_edge("cached_input", "reads_both"), _edge("quotes", "reads_both")],
    )

    def entries() -> set[str]:
        roots = [project / ".haute_cache", project / ".cache"]
        return {
            str(path.relative_to(project))
            for root in roots
            if root.exists()
            for path in root.rglob("*")
            # A reader's process liveness token is no cache entry.
            if path.is_file() and ".processes" not in path.parts
        }

    before = entries()
    outcome = _measure(graph, "reads_both")
    assert outcome.records["reads_both"]["status"] == "checked"
    assert entries() == before


# ---------------------------------------------------------------------------
# The binding and what a consumer shows
# ---------------------------------------------------------------------------


def _model_graph(project: Path, *, artifact: str = "model.cbm") -> PipelineGraph:
    from haute._mlflow_io import disk_cached_run_model

    cached = disk_cached_run_model(run_id="run1", artifact_path=artifact, destination="")
    assert cached is not None
    for cached_file in cached.files:
        # The disk model cache lives under the project (the working directory).
        path = project / cached_file.relative_to(project.resolve())
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"cached model bytes")
    return _graph(
        project,
        [
            _parquet("quotes"),
            _node(
                "scored",
                "modelScore",
                {"sourceType": "run", "run_id": "run1", "artifact_path": artifact},
            ),
            _code("uses_score", "df = scored"),
            _code("plain", "df = quotes.with_columns(net=pl.col('premium') * 0.5)"),
        ],
        [_edge("quotes", "scored"), _edge("scored", "uses_score"), _edge("quotes", "plain")],
    )


def _stored(
    graph: PipelineGraph, outcome: data_check.WorkerOutcome, *changed: str
) -> DataCheckResult:
    from haute._cache import graph_fingerprint

    binding = DataCheckBinding("plan", graph_fingerprint(flatten_graph(graph)), graph.active_source)
    return data_check._checked_result(
        binding, outcome, list(changed), {}, time.monotonic(), inspection=False
    )


@pytest.mark.parametrize(("artifact", "replaced"), [("model.cbm", 0), ("model.ebm", 1)])
def test_replacing_a_cached_model_or_ebm_contract_during_a_check_is_source_changed(
    project: Path, artifact: str, replaced: int
) -> None:
    """The graph configuration is unchanged; only the cache's bytes move."""
    from haute._mlflow_io import disk_cached_run_model

    graph = _model_graph(project, artifact=artifact)
    cached = disk_cached_run_model(run_id="run1", artifact_path=artifact, destination="")
    assert cached is not None
    target = cached.files[replaced].as_posix()
    rewrite = (
        f"df = quotes.with_columns(w=pl.lit(open({target!r}, 'wb').write(b'other model bytes')))"
    )
    dumped = graph.model_dump()
    nodes = [node for node in dumped["nodes"] if node["id"] != "plain"]
    edges = [edge for edge in dumped["edges"] if edge["target"] != "plain"]
    rewriting = _graph(
        project,
        [*nodes, _code("rewrites", rewrite)],
        [*edges, _edge("quotes", "rewrites")],
    )

    outcome = _measure(rewriting, "uses_score", "rewrites")

    assert outcome.kind == "source_changed"


@pytest.mark.parametrize(("artifact", "replaced"), [("model.cbm", 0), ("model.ebm", 1)])
def test_findings_are_labelled_after_their_cached_model_or_ebm_contract_is_replaced(
    project: Path, artifact: str, replaced: int
) -> None:
    from haute._mlflow_io import disk_cached_run_model

    graph = _model_graph(project, artifact=artifact)
    stored = _stored(graph, _measure(graph, "uses_score", "plain"), "uses_score", "plain")
    assert stored.binding.source_generation is not None
    assert data_check_visibility(stored, graph) == "current"

    cached = disk_cached_run_model(run_id="run1", artifact_path=artifact, destination="")
    assert cached is not None
    retrained = project / cached.files[replaced].relative_to(project.resolve())
    retrained.write_bytes(b"retrained model bytes, longer than before")

    assert data_check_visibility(stored, graph) == "earlier_inputs"


def test_findings_are_labelled_after_a_destination_moves_with_old_cached_files_untouched(
    project: Path,
) -> None:
    (project / "haute.toml").write_text(_TOML + '[mlflow]\nfolder = "runs_a"\n', encoding="utf-8")
    graph = _model_graph(project)
    stored = _stored(graph, _measure(graph, "uses_score"), "uses_score")
    assert data_check_visibility(stored, graph) == "current"

    (project / "haute.toml").write_text(_TOML + '[mlflow]\nfolder = "runs_b"\n', encoding="utf-8")

    assert data_check_visibility(stored, graph) == "earlier_inputs"


def test_findings_are_hidden_for_another_graph_or_scenario_and_labelled_after_a_refresh(
    project: Path,
) -> None:
    graph = _seeded_graph(project)
    stored = _stored(graph, _measure(graph, "big_only"), "big_only")
    assert data_check_visibility(stored, graph) == "current"

    other_scenario = graph.model_copy(update={"active_source": "batch"})
    assert data_check_visibility(stored, other_scenario) == "other_scenario"

    edited = graph.model_dump()
    [big_only] = [node for node in edited["nodes"] if node["id"] == "big_only"]
    big_only["data"]["config"]["code"] = "df = quotes.filter(pl.col('premium') > 1.0)"
    assert data_check_visibility(stored, PipelineGraph.model_validate(edited)) == "other_graph"

    pl.DataFrame(_QUOTES).head(4).write_parquet(project / "data" / "quotes.parquet")
    assert data_check_visibility(stored, graph) == "earlier_inputs"


# ---------------------------------------------------------------------------
# Outcomes that never run, and the size of the model-facing view
# ---------------------------------------------------------------------------


def _request(graph: PipelineGraph, *changed: str, tier: str = "schema") -> DataCheckRequest:
    return DataCheckRequest(
        plan_hash="plan",
        candidate_graph=graph,
        diff=_diff(*changed),
        verification_tier=tier,
        policy=_POLICY,
    )


async def test_thread_mode_structural_plans_and_ineligible_nodes_never_run(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    graph = _seeded_graph(project)
    unsupported = await run_data_check(_request(graph, "big_only"), session_id="s")
    assert unsupported.reason == "worker_mode_unsupported"

    monkeypatch.setenv("HAUTE_INTERACTIVE_EXECUTION_MODE", "process")
    structural = await run_data_check(
        _request(graph, "big_only", tier="structural"), session_id="s"
    )
    assert structural.reason == "not_schema_tier"
    sinks = _graph(
        project,
        [_parquet("quotes"), _node("written", "dataOutput", {"path": "out.parquet"})],
        [_edge("quotes", "written")],
    )
    nothing = await run_data_check(_request(sinks, "written"), session_id="s")
    assert nothing.reason == "no_checkable_nodes"
    assert nothing.check["nodes"] == [
        {
            "node": "written",
            "status": "not_checked",
            "reason": "sink_only",
            "blocking_node": None,
            "remedy": None,
        }
    ]
    for result in (unsupported, structural, nothing):
        DATA_CHECK_VIEW.validate_python(result.check)
        assert result.as_dict()["binding"]["source_generation"] is None


def _oversized(project: Path, monkeypatch: pytest.MonkeyPatch) -> DataCheckResult:
    """Sixteen checked nodes, each adding twenty columns that are null in every row."""
    columns = ", ".join(
        f"a_rather_long_and_descriptive_column_name_number_{index:02d}=pl.lit(None, dtype=pl.Int64)"
        for index in range(20)
    )
    nodes = [_parquet("quotes")]
    edges = []
    for index in range(16):
        node_id = f"wide_node_with_a_long_identifier_{index:02d}"
        nodes.append(_code(node_id, f"df = quotes.with_columns({columns})"))
        edges.append(_edge("quotes", node_id))
    graph = _graph(project, nodes, edges)
    changed = [node["id"] for node in nodes[1:]]
    monkeypatch.setattr(data_check, "DATA_CHECK_MAX_NODES", 16)
    return _stored(graph, _measure(graph, *changed), *changed)


def _compact_size(value: object) -> int:
    return len(json.dumps(value, separators=(",", ":"), sort_keys=True).encode())


def test_an_oversized_check_is_reduced_step_by_step_within_its_allocation(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stored = _oversized(project, monkeypatch)
    whole = _compact_size(stored.check)
    assert whole > data_check.DATA_CHECK_DETAIL_BYTES

    fitted = fit_data_check(stored, room_bytes=200_000)["data_check"]
    assert _compact_size(fitted) <= data_check.DATA_CHECK_DETAIL_BYTES
    assert len(fitted["findings"]) == data_check.DATA_CHECK_MAX_FINDINGS
    assert fitted["findings_omitted"] == len(stored.check["findings"]) - 20
    assert fitted["detail_truncated"] is True
    reduced = [node for node in fitted["nodes"] if node.get("detail_omitted")]
    assert reduced and reduced[-1] == fitted["nodes"][-1]
    # The stored check is never reduced.
    assert _compact_size(stored.check) == whole

    tight = fit_data_check(stored, room_bytes=1_500)["data_check"]
    assert _compact_size(tight) <= 1_500 - len('"data_check"') - 2
    assert tight["nodes_omitted"] > 0
    DATA_CHECK_VIEW.validate_python(tight)

    assert fit_data_check(stored, room_bytes=150) == {
        "data_check_omitted": data_check.DATA_CHECK_OMITTED_NOTE
    }
    assert fit_data_check(stored, room_bytes=50) == {}


def test_a_reduced_no_checkable_nodes_result_still_validates(project: Path) -> None:
    sinks = [f"output_sink_{index:02d}" for index in range(30)]
    binding = DataCheckBinding("plan", "digest", "live")
    records = [
        data_check._not_checked_record(node, data_check._Exclusion("sink_only")) for node in sinks
    ]
    stored = data_check._not_run(binding, "no_checkable_nodes", time.monotonic(), nodes=records)

    fitted = fit_data_check(stored, room_bytes=1_000)["data_check"]

    assert fitted["nodes_omitted"] > 0
    assert len(fitted["nodes"]) + fitted["nodes_omitted"] == 30
    assert set(fitted) == {
        "version",
        "outcome",
        "scenario",
        "reason",
        "detail",
        "elapsed_ms",
        "nodes",
        "nodes_omitted",
    }
    DATA_CHECK_VIEW.validate_python(fitted)


# ---------------------------------------------------------------------------
# In the interactive worker (process mode)
# ---------------------------------------------------------------------------


@pytest.fixture()
def worker_pool(project: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """A started one-worker pool whose worker runs in *project* (spawned after the chdir)."""
    from haute._interactive_workers import interactive_worker_pool, shutdown_interactive_worker_pool

    monkeypatch.setenv("HAUTE_INTERACTIVE_EXECUTION_MODE", "process")
    monkeypatch.setenv("HAUTE_INTERACTIVE_WORKER_COUNT", "1")
    # These tests exercise the worker boundary, not native cap availability.
    monkeypatch.setenv("HAUTE_WORKER_MEMORY_ENFORCEMENT", "best_effort")
    shutdown_interactive_worker_pool()
    pool = interactive_worker_pool()
    try:
        yield pool
    finally:
        shutdown_interactive_worker_pool()


def _marker_code(marker: Path, *, loop_forever: bool = False) -> str:
    """Node code whose row callback announces itself, then never returns in time."""
    body = "while True:\n        pass" if loop_forever else "time.sleep(120)"
    return (
        "import time\n"
        "from pathlib import Path\n"
        "def slow(series):\n"
        f"    Path({marker.as_posix()!r}).write_text('started')\n"
        f"    {body}\n"
        "    return series\n"
        "df = quotes.with_columns(pl.col('premium').map_batches(slow, return_dtype=pl.Float64))\n"
    )


def _slow_graph(project: Path, marker: Path, *, loop_forever: bool = False) -> PipelineGraph:
    return _graph(
        project,
        [_parquet("quotes"), _code("slow", _marker_code(marker, loop_forever=loop_forever))],
        [_edge("quotes", "slow")],
    )


async def _await_marker(marker: Path, *, timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    while not marker.exists():
        assert time.monotonic() < deadline, "the check never reached its row callback"
        await asyncio.sleep(0.02)


@pytest.mark.slow
async def test_a_check_runs_in_the_worker_and_verifies_a_cold_generation_only_there(
    project: Path, worker_pool: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The server reads only the published pointer; every part is hashed in the worker."""
    import haute._source_cache as source_cache
    from haute._source_cache import SourceCacheStore

    worker_pool.start()
    seeded = _seeded_graph(project)
    csv_node = _csv_input(project, "cold")
    SourceCacheStore(project)._verified_generations.clear()

    def no_server_hash(*_args: object, **_kwargs: object) -> str:
        raise AssertionError("the server must never hash a snapshot part")

    monkeypatch.setattr(source_cache, "content_hash", no_server_hash)
    dumped = seeded.model_dump()
    graph = _graph(
        project,
        [*dumped["nodes"], csv_node, _code("reads_cold", "df = cold")],
        [*dumped["edges"], _edge("cold", "reads_cold")],
    )
    changed = ("region_band", "region_rate", "big_only", "with_regions", "reads_cold")

    result = await run_data_check(_request(graph, *changed), session_id="end-to-end")

    assert result.outcome == "checked", result.check
    DATA_CHECK_VIEW.validate_python(result.check)
    kinds = {finding["kind"] for finding in result.check["findings"]}
    assert {"banding_all_default", "rating_misses", "rows_emptied", "join_partial"} <= kinds
    cold = next(node for node in result.check["nodes"] if node["node"] == "reads_cold")
    assert cold["status"] == "checked"
    assert cold["inputs"] == [{"input": "cold", "rows": 2, "truncated": False}]
    stored = result.as_dict()["binding"]
    assert stored["source_generation"] and stored["freshness_tokens"]
    assert data_check_visibility(result, graph) == "current"


@pytest.mark.slow
async def test_an_occupied_slot_and_a_second_session_never_wait(
    project: Path, worker_pool: Any
) -> None:
    from tests._data_check_worker_hooks import hold_worker

    worker_pool.start()
    graph = _seeded_graph(project)
    holder = threading.Thread(
        target=worker_pool.run,
        args=(hold_worker, 4.0),
        kwargs={"affinity_key": "editor", "timeout_seconds": 60},
    )
    holder.start()
    await asyncio.sleep(0.5)
    started = time.monotonic()
    busy = await run_data_check(_request(graph, "big_only"), session_id="occupied")
    assert busy.reason == "worker_busy"
    assert time.monotonic() - started < 2.0
    await asyncio.to_thread(holder.join, 60)

    marker = project / "first.started"
    first = asyncio.ensure_future(
        run_data_check(_request(_slow_graph(project, marker), "slow"), session_id="first")
    )
    await _await_marker(marker)
    started = time.monotonic()
    second = await run_data_check(_request(graph, "big_only"), session_id="second")
    assert second.reason == "worker_busy"
    assert time.monotonic() - started < 2.0
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first


@pytest.mark.slow
async def test_an_editor_preview_preempts_a_check_and_runs_on_the_replacement_worker(
    project: Path, worker_pool: Any
) -> None:
    from haute._interactive_workers import run_in_interactive_worker
    from tests._data_check_worker_hooks import worker_pid

    worker_pool.start()
    marker = project / "preempted.started"
    check = asyncio.ensure_future(
        run_data_check(_request(_slow_graph(project, marker), "slow"), session_id="preempted")
    )
    await _await_marker(marker)
    check_process = worker_pool._slots[0].process

    preview_pid = await run_in_interactive_worker(
        worker_pid, affinity_key="editor-preview", timeout_seconds=60
    )
    result = await check

    assert result.reason == "superseded_by_preview"
    assert preview_pid != check_process.pid
    assert not check_process.is_alive()


@pytest.mark.slow
async def test_a_check_stops_at_its_deadline_a_stopped_turn_and_a_newer_check(
    project: Path, worker_pool: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A row callback that never reaches a checkpoint is stopped with its worker."""
    from tests._data_check_worker_hooks import worker_pid

    worker_pool.start()
    monkeypatch.setattr(data_check, "DATA_CHECK_DEADLINE_SECONDS", 3.0)
    marker = project / "forever.started"
    hung_process = worker_pool._slots[0].process
    started = time.monotonic()

    result = await run_data_check(
        _request(_slow_graph(project, marker, loop_forever=True), "slow"), session_id="hung"
    )

    assert result.reason == "deadline"
    # The deadline plus terminating the worker and starting its replacement.
    assert time.monotonic() - started < 3.0 + 30.0
    assert marker.exists() and not hung_process.is_alive()
    replacement_pid = await asyncio.to_thread(
        worker_pool.run, worker_pid, affinity_key="next", timeout_seconds=60
    )
    assert replacement_pid != hung_process.pid

    monkeypatch.setattr(data_check, "DATA_CHECK_DEADLINE_SECONDS", 60.0)
    stopped_marker = project / "stopped.started"
    turn = ExecutionCancellationToken()
    stopped = asyncio.ensure_future(
        run_data_check(
            _request(_slow_graph(project, stopped_marker), "slow"),
            session_id="stopped",
            cancellation=turn,
        )
    )
    await _await_marker(stopped_marker)
    turn.cancel()
    assert (await stopped).reason == "cancelled"

    # A newer check in the same session stops the older one and then runs.
    older_marker = project / "older.started"
    older = asyncio.ensure_future(
        run_data_check(_request(_slow_graph(project, older_marker), "slow"), session_id="same")
    )
    await _await_marker(older_marker)
    newer = await run_data_check(_request(_seeded_graph(project), "big_only"), session_id="same")
    assert (await older).reason == "superseded"
    assert newer.outcome == "checked", newer.check


@pytest.mark.slow
async def test_slow_binding_delayed_dispatch_and_a_starting_worker_end_at_the_deadline(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import queue

    import haute._interactive_workers as workers
    from tests._data_check_worker_hooks import SLOW_HASH_ENV, hold_worker

    monkeypatch.setenv("HAUTE_INTERACTIVE_EXECUTION_MODE", "process")
    monkeypatch.setenv("HAUTE_WORKER_MEMORY_ENFORCEMENT", "best_effort")
    monkeypatch.setenv(SLOW_HASH_ENV, "120")
    pool = workers.InteractiveWorkerPool(
        size=1,
        polars_threads=2,
        preload_modules=("haute.routes.pipeline", "tests._data_check_worker_hooks"),
    )
    monkeypatch.setattr(workers, "_POOL", pool)
    monkeypatch.setattr(data_check, "DATA_CHECK_DEADLINE_SECONDS", 3.0)
    pool.start()
    graph = _seeded_graph(project)
    try:
        started = time.monotonic()
        slow_binding = await run_data_check(_request(graph, "big_only"), session_id="slow")
        assert slow_binding.reason == "deadline"
        assert time.monotonic() - started < 3.0 + 30.0

        slot = pool._slots[0]
        real_queue = slot.request_queue

        class _UnreadQueue:
            def put(self, _payload: object, timeout: float | None = None) -> None:
                assert timeout is not None
                time.sleep(timeout)
                raise queue.Full

            def __getattr__(self, name: str) -> Any:
                return getattr(real_queue, name)

        slot.request_queue = _UnreadQueue()
        started = time.monotonic()
        delayed = await run_data_check(_request(graph, "big_only"), session_id="delayed")
        assert delayed.reason == "deadline"
        assert time.monotonic() - started < 3.0 + 30.0

        starting = threading.Event()
        proceed = threading.Event()
        real_start = pool._start_slot

        def slow_start(*, index: int, generation: int) -> Any:
            starting.set()
            assert proceed.wait(60)
            return real_start(index=index, generation=generation)

        monkeypatch.setattr(pool, "_start_slot", slow_start)
        timing_out = threading.Thread(
            target=lambda: pytest.raises(
                workers.InteractiveWorkerTimeoutError,
                pool.run,
                hold_worker,
                60.0,
                affinity_key="editor",
                timeout_seconds=0.5,
            )
        )
        timing_out.start()
        assert await asyncio.to_thread(starting.wait, 60)
        started = time.monotonic()
        while_starting = await run_data_check(_request(graph, "big_only"), session_id="starting")
        assert while_starting.reason == "worker_busy"
        assert time.monotonic() - started < 2.0
        proceed.set()
        await asyncio.to_thread(timing_out.join, 60)
    finally:
        pool.close()


def test_the_remaining_finding_kinds_compare_exact_ratios(project: Path) -> None:
    pl.DataFrame(
        {"region": ["north", "north", "south", "nowhere"], "loading": [1.1, 1.2, 0.9, 0.0]}
    ).write_parquet(project / "data" / "regions.parquet")
    pl.DataFrame({"region": ["nowhere"], "loading": [0.0]}).write_parquet(
        project / "data" / "elsewhere.parquet"
    )
    graph = _graph(
        project,
        [
            _parquet("quotes"),
            _parquet("regions"),
            _parquet("elsewhere"),
            _node(
                "half_banded",
                "banding",
                {
                    "factors": [
                        {
                            "banding": "categorical",
                            "column": "region",
                            "outputColumn": "zone",
                            "rules": [
                                {"value": "north", "assignment": "N"},
                                {"value": "south", "assignment": "S"},
                                {"value": "Atlantis", "assignment": "A"},
                            ],
                            "default": "Other",
                        }
                    ]
                },
            ),
            _node(
                "boundary_rate",
                "ratingStep",
                {
                    "tables": [
                        {
                            "factors": ["region"],
                            "outputColumn": "region_factor",
                            "entries": [
                                {"region": region, "value": 1.0}
                                for region in ("north", "south", "east")
                            ],
                            "defaultValue": "1.0",
                        }
                    ]
                },
            ),
            _node("fans_out", "edgeJoin", {"how": "left", "on": "region"}),
            _node("unmatched", "edgeJoin", {"how": "inner", "on": "region"}),
            _code(
                "sparse",
                "df = quotes.with_columns(flag=pl.when(pl.col('region').is_in(['north', 'south']))"
                ".then(1))",
            ),
        ],
        [
            _edge("quotes", "half_banded"),
            _edge("quotes", "boundary_rate"),
            _edge("quotes", "fans_out", role="base"),
            _edge("regions", "fans_out", role="join"),
            _edge("quotes", "unmatched", role="base"),
            _edge("elsewhere", "unmatched", role="join"),
            _edge("quotes", "sparse"),
        ],
    )

    outcome = _measure(graph, "half_banded", "boundary_rate", "fans_out", "unmatched", "sparse")

    by_kind = {(finding["node"], finding["kind"]): finding for finding in outcome.findings}
    # North and south claim four rows: six of ten default, and the third rule claims none.
    mostly = by_kind[("half_banded", "banding_mostly_default")]
    assert (mostly["defaulted"], mostly["rows"], mostly["share"]) == (6, 10, 0.6)
    assert by_kind[("half_banded", "banding_rules_unclaimed")]["rules"] == [2]
    boundary = by_kind[("boundary_rate", "rating_misses")]
    assert (boundary["missed"], boundary["rows"], boundary["severity"]) == (3, 10, "advisory")
    fan_out = by_kind[("fans_out", "join_fan_out")]
    assert (fan_out["base_rows"], fan_out["output_rows"]) == (10, 12)
    assert fan_out["duplicate_key_tuples"] == {"base": 4, "join": 1}
    assert by_kind[("unmatched", "join_unmatched")]["join_rows"] == 1
    mostly_null = by_kind[("sparse", "column_mostly_null")]
    assert (mostly_null["nulls"], mostly_null["rows"], mostly_null["share"]) == (6, 10, 0.6)
    assert outcome.records["unmatched"]["outputs"][0]["rows"] == 0
    assert ("unmatched", "rows_emptied") in by_kind


def test_rating_and_banding_thresholds_sit_on_exact_ratios(project: Path) -> None:
    pl.DataFrame(
        {"region": ["north"] * 9 + ["west"] + ["north"] * 9 + ["west"], "premium": [1.0] * 20}
    ).head(10).write_parquet(project / "data" / "quotes.parquet")
    graph = _graph(
        project,
        [
            _parquet("quotes"),
            _node(
                "rated",
                "ratingStep",
                {
                    "tables": [
                        {
                            "factors": ["region"],
                            "outputColumn": "region_factor",
                            "entries": [{"region": "north", "value": 1.0}],
                            "defaultValue": "1.0",
                        }
                    ]
                },
            ),
        ],
        [_edge("quotes", "rated")],
    )
    [misses] = _findings(_measure(graph, "rated"), "rating_misses")
    assert (misses["missed"], misses["rows"], misses["severity"]) == (1, 10, "advisory")

    pl.DataFrame({"region": ["north"] * 19 + ["west"], "premium": [1.0] * 20}).write_parquet(
        project / "data" / "quotes.parquet"
    )
    [below] = _findings(_measure(graph, "rated"), "rating_misses")
    assert (below["missed"], below["rows"], below["severity"]) == (1, 20, "informational")


def test_error_records_classify_configuration_preamble_and_internal_failures(
    project: Path,
) -> None:
    from haute.assistant._tools import execution_error_record

    graph = _graph(
        project,
        [
            _parquet("quotes"),
            _node(
                "misbanded",
                "banding",
                {
                    "factors": [
                        {
                            "banding": "breakpoints",
                            "column": "region",
                            "outputColumn": "band",
                            "rules": [{"boundary": "10", "label": "low"}],
                        }
                    ]
                },
            ),
        ],
        [_edge("quotes", "misbanded")],
    )
    configuration = _measure(graph, "misbanded").records["misbanded"]
    assert configuration["status"] == "failed"
    assert configuration["error"]["class"] == "configuration"
    assert configuration["error"]["type"] == "ConfigSettingError"
    assert configuration["error"]["text"]
    assert configuration["inputs"] == [{"input": "quotes", "rows": 10, "truncated": False}]

    preamble = PipelineGraph.model_validate(
        {
            **_graph(
                project,
                [_parquet("quotes"), _code("coded", "df = quotes")],
                [_edge("quotes", "coded")],
            ).model_dump(),
            "preamble": "raise ValueError('preamble secret 4242')",
        }
    )
    failed = _measure(preamble, "coded").records["coded"]
    assert (failed["error"]["class"], failed["error"]["type"]) == ("authored_code", "PreambleError")
    assert failed["error"]["text"] is None

    internal = execution_error_record(
        KeyError("private detail"), graph=graph, node="misbanded", submitted=(), policy=_POLICY
    )
    assert internal["class"] == "internal"
    assert "private detail" not in json.dumps(internal)


@pytest.mark.parametrize(
    ("make", "reason"),
    [
        ("busy", "worker_busy"),
        ("preempted", "superseded_by_preview"),
        ("timeout", "deadline"),
        ("superseded", "superseded"),
        ("cancelled", "cancelled"),
        ("rss", "memory_limited"),
        ("crash_memory", "memory_limited"),
        ("remote_budget", "memory_limited"),
        ("remote_other", "internal_error"),
        ("crash", "internal_error"),
    ],
)
def test_each_worker_outcome_maps_to_one_not_run_reason(make: str, reason: str) -> None:
    import haute._interactive_workers as workers
    from haute._execution_context import ExecutionMemoryLimitExceededError

    def remote(error: type[BaseException]) -> workers.InteractiveWorkerRemoteError:
        return workers.InteractiveWorkerRemoteError(
            remote_type=error.__name__,
            remote_module=error.__module__,
            remote_message="private",
            remote_traceback="private",
            public_payload=None,
        )

    errors: dict[str, workers.InteractiveWorkerError] = {
        "busy": workers.InteractiveWorkerBusyError("held"),
        "preempted": workers.InteractiveWorkerPreemptedError(),
        "timeout": workers.InteractiveWorkerTimeoutError(30.0),
        "superseded": workers.InteractiveWorkerStoppedError("superseded"),
        "cancelled": workers.InteractiveWorkerStoppedError("cancelled"),
        "rss": workers.InteractiveWorkerMemoryLimitError(rss_bytes=2, limit_bytes=1),
        "crash_memory": workers.InteractiveWorkerCrashedError(-9, memory_limited=True),
        "remote_budget": remote(ExecutionMemoryLimitExceededError),
        "remote_other": remote(ValueError),
        "crash": workers.InteractiveWorkerCrashedError(1),
    }

    assert data_check._worker_failure_reason(errors[make]) == reason


async def test_an_admission_refusal_and_a_defect_are_results_never_errors(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HAUTE_INTERACTIVE_EXECUTION_MODE", "process")
    monkeypatch.setenv("HAUTE_PREVIEW_PROCESS_RSS_LIMIT_BYTES", "1")
    graph = _seeded_graph(project)

    refused = await run_data_check(_request(graph, "big_only"), session_id="refused")
    assert refused.reason == "admission_refused"
    assert refused.check["detail"] == "process_rss_limit_exceeded"

    def broken(*_args: object) -> None:
        raise RuntimeError("a defect in the check")

    monkeypatch.setattr(data_check, "server_exclusions", broken)
    defect = await run_data_check(_request(graph, "big_only"), session_id="defect")
    assert defect.reason == "internal_error"
    DATA_CHECK_VIEW.validate_python(defect.check)


# ---------------------------------------------------------------------------
# The dry-run, the change card and a stopped turn
# ---------------------------------------------------------------------------

_PIPELINE = '''"""Pipeline: checked"""

import haute

pipeline = haute.Pipeline("checked")


@pipeline.data_input(config="config/data_input/quotes.json")
def quotes(): ...
'''


def _band_ops(name: str = "region_band") -> list[dict[str, Any]]:
    """A categorical banding of quotes' regions whose one rule matches no quote."""
    return [
        {
            "op": "add_node",
            "node_type": "banding",
            "name": name,
            "config": {
                "factors": [
                    {
                        "banding": "categorical",
                        "column": "region",
                        "outputColumn": f"{name}_group",
                        "rules": [{"value": "Atlantis", "assignment": "Lost"}],
                        "default": "Other",
                    }
                ]
            },
        },
        {"op": "add_edge", "source": "quotes", "target": name},
    ]


@pytest.fixture()
def pipeline(project: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """*project*'s quotes as a saved pipeline the assistant's tools dry-run and apply."""
    from haute.assistant import _tools
    from haute.assistant._ops import PlanStore

    (project / "config" / "data_input").mkdir(parents=True)
    (project / "config" / "data_input" / "quotes.json").write_text(
        json.dumps(make_file_input_config("data/quotes.parquet")), encoding="utf-8"
    )
    (project / "main.py").write_text(_PIPELINE, encoding="utf-8")
    monkeypatch.setattr(_tools, "_PLAN_STORE", PlanStore())
    monkeypatch.setattr(_tools, "mutations_readiness", lambda _root: (True, None))
    return project


def _policy_without_checks(monkeypatch: pytest.MonkeyPatch) -> None:
    """The project's policy with aggregate statistics withheld, its files unchanged."""
    from haute.assistant import _tools

    withheld = replace(_POLICY, allow_aggregate_statistics=False)
    monkeypatch.setattr(_tools, "resolve_egress_policy", lambda _root: withheld)


async def _dry_run(ops: list[dict[str, Any]], *, session_id: str = "s") -> dict[str, Any]:
    from haute.assistant._tools import build_tool_executor

    execute = build_tool_executor("main.py", session_id=session_id)
    return dict(await execute("dry_run_graph_edits", {"summary": "Band regions.", "ops": ops}))


@pytest.mark.slow
async def test_a_dry_runs_check_leaves_its_plan_hash_and_evidence_unchanged(
    pipeline: Path, worker_pool: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from haute.assistant import _tools

    worker_pool.start()
    checked = await _dry_run(_band_ops())

    check = checked.pop("data_check")
    assert check["outcome"] == "checked", check
    DATA_CHECK_VIEW.validate_python(check)
    assert [(finding["kind"], finding["node"]) for finding in check["findings"]] == [
        ("banding_all_default", "region_band")
    ]
    stored = _tools._PLAN_STORE.data_check(checked["plan_hash"])
    assert stored is not None and stored.check == check
    assert stored.binding.plan_hash == checked["plan_hash"]

    _policy_without_checks(monkeypatch)
    unchecked = await _dry_run(_band_ops())

    # The same plan, hash, tier, evidence, warnings and changes, without a check.
    assert unchecked == checked
    # A fresh dry-run that attempts no check leaves none beside the plan.
    assert _tools._PLAN_STORE.data_check(checked["plan_hash"]) is None


async def test_no_check_is_attempted_unless_the_policy_permits_one(
    pipeline: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from haute.assistant._tools import DataCheckGate, dry_run_graph_edits

    # Thread mode, as the replay suite runs: a check is attempted and reports why
    # it could not run, and the dry-run is a success.
    attempted = await _dry_run(_band_ops())
    assert attempted["data_check"]["outcome"] == "not_run"
    assert attempted["data_check"]["reason"] == "worker_mode_unsupported"

    attempts: list[object] = []

    async def never(request: DataCheckRequest, **_kwargs: object) -> DataCheckResult:
        attempts.append(request)
        raise AssertionError("no check may be attempted")

    monkeypatch.setattr(data_check, "run_data_check", never)
    _policy_without_checks(monkeypatch)
    withheld = await _dry_run(_band_ops())
    assert "plan_hash" in withheld
    assert not {"data_check", "data_check_omitted"} & set(withheld)

    public = replace(_POLICY, max_sensitivity="public")
    ceiling = await dry_run_graph_edits(
        "main.py",
        _band_ops(),
        summary="Band regions.",
        config_visibility=None,
        data_check=DataCheckGate(public, "s"),
    )
    assert "plan_hash" in ceiling
    assert not {"data_check", "data_check_omitted"} & set(ceiling)
    assert attempts == []


async def test_the_check_is_sized_on_the_fully_attributed_dry_run_result(
    pipeline: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A check fits the room the attributed result leaves, so a successful
    dry-run never becomes ``tool_result_too_large``."""
    from haute.assistant import _tools

    oversized = _oversized(pipeline, monkeypatch)

    async def fake_check(_request: DataCheckRequest, **_kwargs: object) -> DataCheckResult:
        return oversized

    with monkeypatch.context() as withheld:
        _policy_without_checks(withheld)
        bare = await _dry_run(_band_ops())
    bare_size = _tools._json_size(bare)
    monkeypatch.setattr(data_check, "run_data_check", fake_check)

    # Room for the note exactly: measured without the result's attribution, the
    # room would also have fitted an emptied check, and the dry-run would fail.
    note_cost = (
        _tools._json_size(data_check.DATA_CHECK_OMITTED_NOTE)
        + _tools._json_size("data_check_omitted")
        + 2
    )
    monkeypatch.setattr(_tools, "_MAX_TOOL_CONTEXT_BYTES", bare_size + note_cost)
    noted = await _dry_run(_band_ops())
    assert "error" not in noted, noted
    assert noted.pop("data_check_omitted") == data_check.DATA_CHECK_OMITTED_NOTE
    assert noted == bare

    monkeypatch.setattr(_tools, "_MAX_TOOL_CONTEXT_BYTES", bare_size + 2_000)
    reduced = await _dry_run(_band_ops())
    assert _tools._json_size(reduced) <= bare_size + 2_000
    view = reduced.pop("data_check")
    assert view["detail_truncated"] is True and view["nodes_omitted"] > 0
    DATA_CHECK_VIEW.validate_python(view)
    assert reduced == bare

    monkeypatch.setattr(_tools, "_MAX_TOOL_CONTEXT_BYTES", bare_size + 10)
    assert await _dry_run(_band_ops()) == bare
    # Whatever reached the model, the stored check is whole.
    assert _tools._PLAN_STORE.data_check(bare["plan_hash"]) is oversized


async def _apply_with_check(
    ops: list[dict[str, Any]], check: Callable[[PipelineGraph, str], DataCheckResult]
) -> dict[str, Any]:
    """Dry-run *ops*, keep *check(candidate graph, plan hash)* beside the plan, apply it."""
    from haute.assistant import _tools

    dry = _tools.application_service(session_id=None).dry_run(
        "main.py", ops, summary="Band regions."
    )
    assert _tools._PLAN_STORE.record_data_check(
        dry.plan.plan_hash, check(dry.result_graph, dry.plan.plan_hash)
    )
    applied = await _tools.apply_graph_plan("main.py", dry.plan.plan_hash, session_id="s")
    assert "error" not in applied, applied
    return dict(applied["change"])


def _measured(node: str) -> Callable[[PipelineGraph, str], DataCheckResult]:
    """The worker half of *node*'s check, run here on a plan's candidate graph."""

    def measure(graph: PipelineGraph, plan_hash: str) -> DataCheckResult:
        stored = _stored(graph, _measure(graph, node), node)
        return DataCheckResult(stored.check, replace(stored.binding, plan_hash=plan_hash))

    return measure


async def test_an_apply_carries_its_plans_findings_labelled_against_the_saved_graph(
    pipeline: Path,
) -> None:
    current = await _apply_with_check(_band_ops("region_band"), _measured("region_band"))
    assert current["data_check"] == {
        "visibility": "current",
        "outcome": "checked",
        "scenario": "live",
        "findings": [
            {
                "severity": "advisory",
                "node": "region_band",
                "text": "All 10 rows fell into the default band of region_band_group.",
            }
        ],
        "findings_omitted": 0,
        "not_checked": None,
    }

    def then_refresh_quotes(graph: PipelineGraph, plan_hash: str) -> DataCheckResult:
        measured = _measured("second_band")(graph, plan_hash)
        refreshed = pl.DataFrame({**_QUOTES, "premium": [1.0] * 10})
        refreshed.write_parquet(pipeline / "data" / "quotes.parquet")
        return measured

    earlier = await _apply_with_check(_band_ops("second_band"), then_refresh_quotes)
    assert earlier["data_check"]["visibility"] == "earlier_inputs"
    assert [finding["node"] for finding in earlier["data_check"]["findings"]] == ["second_band"]

    def foreign(graph: PipelineGraph, plan_hash: str) -> DataCheckResult:
        measured = _measured("third_band")(graph, plan_hash)
        return DataCheckResult(
            measured.check, replace(measured.binding, graph_digest="v8:another-graph")
        )

    other = await _apply_with_check(_band_ops("third_band"), foreign)
    assert "data_check" not in other


def test_a_card_hides_another_scenarios_findings_and_says_why_nothing_was_checked(
    project: Path,
) -> None:
    from haute.assistant._change_record import change_data_check

    graph = _seeded_graph(project)
    stored = _stored(graph, _measure(graph, "region_band"), "region_band")
    hidden = change_data_check(stored, "other_scenario")
    assert hidden is not None
    assert (hidden.visibility, hidden.scenario, hidden.findings) == ("other_scenario", "live", [])
    assert change_data_check(stored, "other_graph") is None
    assert change_data_check(None, "current") is None

    binding = DataCheckBinding("plan", "digest", "live")
    busy = data_check._not_run(binding, "worker_busy", time.monotonic())
    card = change_data_check(busy, "current")
    assert card is not None and card.findings == []
    assert card.not_checked == "Data not checked: the preview worker was busy."
    records = [
        data_check._not_checked_record("premium", data_check._Exclusion("sink_only")),
        data_check._not_checked_record("rates", data_check._input_not_prepared("quotes")),
    ]
    nothing = data_check._not_run(binding, "no_checkable_nodes", time.monotonic(), nodes=records)
    named = change_data_check(nothing, "current")
    assert named is not None and named.not_checked == (
        "Data not checked: premium (it produces no data), rates (preview the input quotes first)."
    )


@pytest.mark.slow
async def test_a_stopped_turn_cancels_its_check_and_keeps_the_dry_run(
    pipeline: Path, worker_pool: Any
) -> None:
    """Stopping the turn while the check runs stops the check's worker; the
    dry-run's result, with a ``cancelled`` check, is the turn's record of it."""
    from haute.assistant import _tools
    from haute.assistant._loop import run_turn
    from haute.assistant._providers import ToolCallRequest, TurnStop
    from haute.assistant._session import SessionStore
    from haute.assistant._tools import build_tool_executor

    worker_pool.start()
    marker = pipeline / "stopped.started"
    code = (
        "# Wait in a row callback\n"
        "import time\n"
        "from pathlib import Path\n"
        "def slow(series):\n"
        f"    Path({marker.as_posix()!r}).write_text('started')\n"
        "    time.sleep(120)\n"
        "    return series\n"
        "df = df.with_columns(pl.col('premium').map_batches(slow, return_dtype=pl.Float64))"
    )
    ops = [
        {
            "op": "add_node",
            "node_type": "polars",
            "name": "slow",
            "config": {
                "steps": [
                    {"id": "start", "kind": "source", "input": "quotes"},
                    {"id": "logic", "kind": "free_code", "code": code},
                ]
            },
        },
        {"op": "add_edge", "source": "quotes", "target": "slow"},
    ]

    class OneDryRun:
        async def stream_turn(self, *, system: str, messages: object, tools: object) -> Any:
            yield ToolCallRequest("dry", "dry_run_graph_edits", {"summary": "Slow.", "ops": ops})
            yield TurnStop("tool_use", _one_token_usage())

    store = SessionStore()
    session = store.create("main.py")

    async def consume() -> None:
        async for _event in run_turn(
            store,
            session.id,
            "add a slow node",
            provider=OneDryRun(),  # type: ignore[arg-type]
            tools=[],
            execute_tool=build_tool_executor("main.py", session_id=session.id),
            system_prompt="s",
            turn_timeout=120.0,
            max_tool_calls=8,
        ):
            pass

    turn = asyncio.ensure_future(consume())
    await _await_marker(marker)
    check_process = worker_pool._slots[0].process
    turn.cancel()
    with pytest.raises(asyncio.CancelledError):
        await turn

    assert not check_process.is_alive()
    [result] = [message for message in session.history[-1].messages if message.role == "tool"]
    content = result.content
    assert isinstance(content, dict) and "error" not in content
    assert (content["data_check"]["outcome"], content["data_check"]["reason"]) == (
        "not_run",
        "cancelled",
    )
    stored = _tools._PLAN_STORE.data_check(str(content["plan_hash"]))
    assert stored is not None and stored.reason == "cancelled"


def _one_token_usage() -> Any:
    from haute.assistant._providers import ProviderUsage

    return ProviderUsage(input_tokens=1, output_tokens=1)


# ---------------------------------------------------------------------------
# A saved node's data: inspect_node's data part (ASSIST-42)
# ---------------------------------------------------------------------------


def _inspect(graph: PipelineGraph, node: str, column: str | None = None) -> dict[str, Any]:
    """An inspection's worker half, in-process, as the object the data part returns."""
    return dict(_inspected(NodeDataCheckRequest(graph, node, column, _POLICY)).check)


def _inspected(request: NodeDataCheckRequest) -> DataCheckResult:
    """An inspection's worker half, in-process, as the check the data part fits."""
    graph = request.graph
    scope = data_check._inspection_scope(request, flatten_graph(graph))
    exclusions = server_exclusions(graph, scope.flat, scope.nodes)
    job = data_check._scope_job(scope, exclusions)
    assert job is not None
    context = create_admitted_execution_context(
        operation="assistant_data_check", profile=ExecutionProfile.PREVIEW_EAGER
    )
    try:
        outcome = measure_candidate(job, context)
    finally:
        context.release_admission()
    excluded = {
        node_id: data_check._not_checked_record(node_id, exclusion)
        for node_id, exclusion in exclusions.items()
        if exclusion is not None
    }
    result = data_check._checked_result(
        DataCheckBinding(None, "digest", graph.active_source),
        outcome,
        scope.nodes,
        excluded,
        time.monotonic(),
        inspection=True,
    )
    NODE_DATA_VIEW.validate_python(result.check)
    return result


def _record(view: Mapping[str, Any], node: str) -> Mapping[str, Any]:
    return next(record for record in view["nodes"] if record["node"] == node)


# A stepped Transform whose free-code step casts text to a number, which fails on rows.
_FAILING_STEPS = {
    "steps": [
        {"id": "start", "kind": "source", "input": "quotes"},
        {
            "id": "logic",
            "kind": "free_code",
            "code": "# Read the region as a number\n"
            "df = df.with_columns(pl.col('region').cast(pl.Int64))",
        },
    ]
}


def test_an_inspection_reports_repeated_downstream_failures_once_at_their_cause(
    project: Path,
) -> None:
    """Three nodes below a failing step would each fail with it in a preview; the
    inspection reports one error, with its step, at the node that raised it."""
    graph = _graph(
        project,
        [
            _parquet("quotes"),
            _node("as_number", "polars", _FAILING_STEPS),
            _code("regions_only", "df = as_number.select('region')"),
            _code("doubled", "df = regions_only.with_columns(twice=pl.col('region') * 2)"),
            _code("final", "df = doubled"),
        ],
        [
            _edge("quotes", "as_number"),
            _edge("as_number", "regions_only"),
            _edge("regions_only", "doubled"),
            _edge("doubled", "final"),
        ],
    )

    view = _inspect(graph, "final")

    assert [record["node"] for record in view["nodes"]] == [
        "quotes",
        "as_number",
        "regions_only",
        "doubled",
        "final",
    ]
    assert _record(view, "quotes")["status"] == "checked"
    failed = _record(view, "as_number")
    assert failed["status"] == "failed"
    assert failed["inputs"] == [{"input": "quotes", "rows": 10, "truncated": False}]
    assert failed["error"]["class"] == "authored_code"
    assert failed["error"]["step"] == {"id": "logic", "number": 2}
    assert failed["error"]["text"] is None and failed["error"]["withheld"]
    for node in ("regions_only", "doubled", "final"):
        assert _record(view, node) == {
            "node": node,
            "status": "upstream_failed",
            "failed_node": "as_number",
            "at_or_upstream": False,
        }
    assert [(finding["kind"], finding["node"]) for finding in view["findings"]] == [
        ("execution_failed", "as_number")
    ]
    assert view["column"] is None


def _loaded_graph(project: Path) -> PipelineGraph:
    """Quotes left-joined to a region table that lacks one of their four regions."""
    return _graph(
        project,
        [
            _parquet("quotes"),
            _parquet("regions"),
            _node("with_regions", "edgeJoin", {"how": "left", "on": "region", "validate": "m:1"}),
            _code(
                "loaded",
                "df = with_regions.with_columns(loaded=pl.col('premium') * pl.col('loading'))",
            ),
        ],
        [
            _edge("quotes", "with_regions", role="base"),
            _edge("regions", "with_regions", role="join"),
            _edge("with_regions", "loaded"),
        ],
    )


def test_an_inspection_follows_a_columns_nulls_to_the_join_that_makes_them(
    project: Path,
) -> None:
    graph = _loaded_graph(project)

    view = _inspect(graph, "loaded", "loading")

    assert [record["node"] for record in view["nodes"]] == [
        "quotes",
        "regions",
        "with_regions",
        "loaded",
    ]
    assert view["column"] == {
        "name": "loading",
        "first_null_node": "with_regions",
        "lineage": [
            {
                "node": "regions",
                "port": None,
                "rows": 3,
                "nulls": 0,
                "share": 0.0,
                "input_nulls": None,
                "truncated": False,
            },
            {
                "node": "with_regions",
                "port": None,
                "rows": 10,
                "nulls": 3,
                "share": 0.3,
                "input_nulls": 0,
                "truncated": False,
            },
            {
                "node": "loaded",
                "port": None,
                "rows": 10,
                "nulls": 3,
                "share": 0.3,
                "input_nulls": 3,
                "truncated": False,
            },
        ],
    }
    joined = _record(view, "with_regions")
    assert (joined["join"]["base_rows"], joined["join"]["matched_base_rows"]) == (10, 7)
    # `loading` arrives unchanged from regions: only `column` counts it at the join.
    assert joined["outputs"][0]["columns"] == []
    # A column no measured frame carries is an empty lineage, never an error.
    assert _inspect(graph, "loaded", "nowhere")["column"] == {
        "name": "nowhere",
        "first_null_node": None,
        "lineage": [],
    }


def test_an_inspection_keeps_the_eight_nodes_nearest_the_inspected_node(project: Path) -> None:
    """The lineage's first nodes fall outside the cap; a failure there is reported
    once, at or upstream of the producer the nearest checked node reads."""
    chain = [f"n{index}" for index in range(1, 10)]
    nodes = [_parquet("quotes"), _node("n1", "polars", _FAILING_STEPS)]
    edges = [_edge("quotes", "n1")]
    for previous, name in zip(chain, chain[1:], strict=False):
        nodes.append(_code(name, f"df = {previous}"))
        edges.append(_edge(previous, name))

    view = _inspect(_graph(project, nodes, edges), "n9")

    assert [record["node"] for record in view["nodes"]] == ["quotes", *chain]
    for node in ("quotes", "n1"):
        assert (_record(view, node)["status"], _record(view, node)["reason"]) == (
            "not_checked",
            "node_cap",
        )
    for node in chain[1:]:
        assert _record(view, node) == {
            "node": node,
            "status": "upstream_failed",
            "failed_node": "n1",
            "at_or_upstream": True,
        }
    [failure] = view["findings"]
    assert (failure["kind"], failure["node"], failure["at_or_upstream"]) == (
        "execution_failed",
        "n1",
        True,
    )


def test_a_failure_inside_a_submodel_occurrence_is_reported_against_the_occurrence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The occurrence runs flattened, but its runtime node ids stay behind the
    submodel boundary: the failure names the occurrence the model can name."""
    import shutil

    from haute.routes._helpers import parse_pipeline_to_graph

    copy = tmp_path / "p"
    shutil.copytree(
        Path(__file__).parent / "assistant_eval" / "projects" / "submodel_pricing", copy
    )
    module = copy / "modules" / "vehicle_factors.py"
    module.write_bytes(
        module.read_bytes().replace(b'pl.col("vehicle_year")', b'pl.col("vehicle_yr")')
    )
    monkeypatch.chdir(copy)
    set_project_root(copy)
    graph = parse_pipeline_to_graph(copy / "pipeline.py")

    view = _inspect(graph, "premium")

    assert [record["node"] for record in view["nodes"]] == ["policies", "premium"]
    premium = _record(view, "premium")
    assert (premium["status"], premium["failed_node"]) == ("upstream_failed", "vehicle_factors")
    [failure] = view["findings"]
    assert (failure["kind"], failure["node"]) == ("execution_failed", "vehicle_factors")
    assert "submodel_runtime" not in json.dumps(view)


def test_an_inspection_too_large_for_its_room_is_reduced_then_omitted(project: Path) -> None:
    view = _inspect(_loaded_graph(project), "loaded", "loading")
    result = DataCheckResult(view, DataCheckBinding(None, "digest", "live"))

    assert fit_node_data_check(result, 256_000) == {"data": view}
    reduced = fit_node_data_check(result, _compact_size(view))["data"]
    assert reduced["detail_truncated"] is True
    assert reduced["column"] == view["column"]
    NODE_DATA_VIEW.validate_python(reduced)
    # Room for exactly the note, its key and that key's separators.
    room = _compact_size(NODE_DATA_OMITTED_NOTE) + _compact_size("data_omitted") + 2
    assert fit_node_data_check(result, room) == {"data_omitted": NODE_DATA_OMITTED_NOTE}
    assert fit_node_data_check(result, room - 1) == {}
    # A dry-run's view refuses an inspection's column.
    with pytest.raises(ValueError, match="column"):
        DATA_CHECK_VIEW.validate_python(view)


async def test_the_data_part_answers_in_thread_mode_and_is_withheld_without_the_flag(
    pipeline: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A check that cannot run is the part's answer, never an error; `column` needs
    the part; and a policy without the flag withholds the part like any other."""
    from haute.assistant._tools import build_tool_executor

    execute = build_tool_executor("main.py", session_id="s")
    answered = dict(
        await execute("inspect_node", {"node": "quotes", "parts": ["data"], "column": "premium"})
    )
    assert "error" not in answered, answered
    assert (answered["data"]["outcome"], answered["data"]["reason"]) == (
        "not_run",
        "worker_mode_unsupported",
    )
    NODE_DATA_VIEW.validate_python(answered["data"])
    assert len(str(answered["project_revision"])) == 64
    assert "withheld" not in answered

    refused = dict(await execute("inspect_node", {"node": "quotes", "column": "premium"}))
    assert refused["error"]["code"] == "invalid_request"
    assert refused["error"]["validation_reason"] == "column_without_data"

    _policy_without_checks(monkeypatch)
    withheld = dict(
        await build_tool_executor("main.py", session_id="s")(
            "inspect_node", {"node": "quotes", "parts": ["schema", "data"]}
        )
    )
    assert "schema" in withheld and "data" not in withheld
    assert withheld["withheld"] == [
        {"part": "data", "required_policy": "allow_aggregate_statistics = true"}
    ]


async def test_a_failing_schema_part_leaves_the_data_part_to_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Live: schema and data on a node whose step reads a missing column. The
    schema part's failure made the whole call an error, so the data part, which
    exists to diagnose a broken node, never answered. Each part answers alone."""
    import shutil

    from haute.assistant import _tools

    copy = tmp_path / "p"
    shutil.copytree(Path(__file__).parent / "assistant_eval" / "projects" / "broken_pricing", copy)
    monkeypatch.chdir(copy)
    set_project_root(copy)
    quotes = json.loads((copy / "config" / "data_input" / "quotes.json").read_text("utf-8"))
    build_test_input_snapshot(quotes, base_dir=copy)
    monkeypatch.setattr(_tools, "resolve_egress_policy", lambda _root: _POLICY)

    async def in_process(
        request: NodeDataCheckRequest,
        *,
        session_id: str,
        cancellation: ExecutionCancellationToken,
    ) -> DataCheckResult:
        return _inspected(request)

    monkeypatch.setattr(data_check, "run_node_data_check", in_process)

    result = dict(
        await _tools.build_tool_executor("pipeline.py", session_id="s")(
            "inspect_node", {"node": "rating_features", "parts": ["data", "schema"]}
        )
    )

    assert "error" not in result, result
    assert "schema" not in result
    failed = _record(result["data"], "rating_features")
    assert (failed["status"], failed["error"]["step"]) == ("failed", {"id": "logic", "number": 2})
    assert [(finding["kind"], finding["node"]) for finding in result["data"]["findings"]] == [
        ("execution_failed", "rating_features")
    ]
    [(part, error)] = result["part_errors"].items()
    assert part == "schema"
    assert (error["code"], error["step"], error["retryable"]) == (
        "schema_unresolvable",
        "logic",
        True,
    )
    assert [column["name"] for column in error["inputs"]["quotes"]][:2] == [
        "quote_id",
        "driver_age",
    ]
    assert "part" not in error
    assert len(str(result["project_revision"])) == 64


async def test_a_stopped_turn_stops_an_inspection_whose_check_runs_outside_the_save_lock(
    pipeline: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from haute.assistant import _tools

    started = asyncio.Event()
    lock_held: list[bool] = []

    async def until_stopped(
        request: NodeDataCheckRequest,
        *,
        session_id: str,
        cancellation: ExecutionCancellationToken,
    ) -> DataCheckResult:
        assert (request.node, session_id) == ("quotes", "stop")
        lock_held.append(_tools.save_lock.locked())
        started.set()
        while not cancellation.cancelled:
            await asyncio.sleep(0.01)
        return data_check._not_run(
            DataCheckBinding(None, "digest", "live"), "cancelled", time.monotonic()
        )

    monkeypatch.setattr(data_check, "run_node_data_check", until_stopped)
    call = asyncio.ensure_future(
        _tools.build_tool_executor("main.py", session_id="stop")(
            "inspect_node", {"node": "quotes", "parts": ["data"]}
        )
    )
    await asyncio.wait_for(started.wait(), 30)
    call.cancel()
    result = await call

    assert lock_held == [False]
    assert (result["data"]["outcome"], result["data"]["reason"]) == ("not_run", "cancelled")
