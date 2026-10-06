"""Whole-dataset rating factor levels (RAT-B03).

The Rating Step editor listed the levels it could see in a preview, so a level
that appears only outside those rows could not be chosen and the rows carrying
it silently took the table's default. These pin what the server answers
instead: the levels of the whole data point the node reads, keyed the way the
lookup keys them, and what it says when the data is not there to read.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import polars as pl
import pytest

from tests.conftest import make_edge, make_graph

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

_TERMINAL = {"completed", "error", "cancelled", "superseded", "timed_out", "memory_limited"}


@pytest.fixture(autouse=True)
def _pinned_admission_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin modest budgets so these tests do not depend on the host's free RAM.

    Unpinned, a build reserves most of available RAM from a process-wide
    in-flight budget, so a second heavy operation in the same process — or less
    free RAM later in a long parallel run — turns every terminal status into
    `memory_limited`. These tests are about levels, not memory policy.
    """
    monkeypatch.setenv("HAUTE_NODE_SNAPSHOT_MEMORY_LIMIT_MB", "1024")
    monkeypatch.setenv("HAUTE_EXPLORE_MEMORY_LIMIT_MB", "1024")


@pytest.fixture(autouse=True)
def _clean_jobs():
    from haute.routes.node_data import _node_data_service, _store

    _store.clear_all()
    yield
    for thread in list(_node_data_service._threads.values()):
        thread.join(60)
    _store.clear_all()


@pytest.fixture()
def project(haute_scratch: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(haute_scratch)
    (haute_scratch / "main.py").write_text("# pipeline\n", encoding="utf-8")
    return haute_scratch


def _source_path(project: Path) -> Path:
    return project / "quotes.parquet"


def _write_source(project: Path, frame: pl.DataFrame) -> Path:
    path = _source_path(project)
    frame.write_parquet(path)
    return path


def _source_node(project: Path) -> dict[str, Any]:
    return {
        "id": "source",
        "data": {
            "label": "source",
            "nodeType": "dataInput",
            "config": {
                "inputType": "file",
                "format": "parquet",
                "mode": "scan",
                "path": str(_source_path(project)),
                "arguments": {},
            },
        },
    }


def _rating_node(tables: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "id": "rating",
        "data": {
            "label": "Rating",
            "nodeType": "ratingStep",
            "config": {"tables": tables or []},
        },
    }


def _graph(project: Path, tables: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """A Data Input feeding a Rating Step, so the point is the input itself."""
    return make_graph(
        {
            "source_file": str(project / "main.py"),
            "preamble": "import polars as pl",
            "nodes": [_source_node(project), _rating_node(tables)],
            "edges": [make_edge("source", "rating").model_dump()],
        }
    ).model_dump()


def _graph_with_transform(project: Path, *, code: str = "df = source") -> dict[str, Any]:
    """The Rating Step behind a transform: its point is that node's built output."""
    return make_graph(
        {
            "source_file": str(project / "main.py"),
            "preamble": "import polars as pl",
            "nodes": [
                _source_node(project),
                {
                    "id": "shaped",
                    "data": {"label": "shaped", "nodeType": "polars", "config": {"code": code}},
                },
                _rating_node(),
            ],
            "edges": [
                make_edge("source", "shaped").model_dump(),
                make_edge("shaped", "rating").model_dump(),
            ],
        }
    ).model_dump()


def _build(client: TestClient, graph: dict[str, Any], *, refresh: bool = False) -> dict[str, Any]:
    """Cache the data the Rating Step reads, and return its point."""
    body = {"graph": graph, "node_id": "rating", "source": "live", "refresh": refresh}
    run = client.post("/api/node-data/run", json=body)
    assert run.status_code == 200, run.text
    payload = run.json()
    if payload["status"] in {"started", "joined"}:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            status = client.get(f"/api/node-data/status/{payload['job_id']}").json()
            if status["status"] in _TERMINAL:
                assert status["status"] == "completed", status
                break
            time.sleep(0.02)
        else:
            raise TimeoutError(payload["job_id"])
    point = client.post(
        "/api/node-data/point", json={"graph": graph, "node_id": "rating", "source": "live"}
    ).json()
    assert point["state"] == "current", point
    return point


def _levels_response(client: TestClient, graph: dict[str, Any], columns: list[str], **extra: Any):
    return client.post(
        "/api/rating/levels",
        json={
            "graph": graph,
            "node_id": "rating",
            "source": "live",
            "columns": columns,
            **extra,
        },
    )


def _levels(
    client: TestClient, graph: dict[str, Any], columns: list[str], **extra: Any
) -> dict[str, Any]:
    response = _levels_response(client, graph, columns, **extra)
    assert response.status_code == 200, response.text
    return response.json()


def _column(payload: dict[str, Any], name: str) -> dict[str, Any]:
    for column in payload["columns"]:
        if column["column"] == name:
            return column
    raise AssertionError(f"{name!r} is not in {[c['column'] for c in payload['columns']]}")


def _values(payload: dict[str, Any], name: str) -> list[str]:
    return [value["value"] for value in _column(payload, name)["values"]]


# ----------------------------------------------------------- what it reads


def test_a_level_absent_from_a_preview_is_still_offered(client: TestClient, project: Path) -> None:
    """The point of the package: a rare level is in the data, so it is listed."""
    _write_source(
        project,
        pl.DataFrame({"region": ["North"] * 999 + ["Orkney"], "premium": [1.0] * 1000}),
    )
    graph = _graph(project)
    _build(client, graph)

    payload = _levels(client, graph, ["region"])

    assert payload["status"] == "ok"
    assert payload["total_rows"] == 1000
    assert _values(payload, "region") == ["North", "Orkney"]
    assert _column(payload, "region")["distinct_count"] == 2


def test_a_level_chosen_from_the_answer_matches_at_lookup_time(
    client: TestClient, project: Path
) -> None:
    """Key agreement: a level as answered is a level the rating step joins on.

    The levels are keyed by the lookup's own expression, so putting one into a
    rating table rates exactly the rows the answer counted — including the ones
    whose text a looser rendering would have altered.
    """
    from haute._rating import apply_rating_step_from_config

    path = _write_source(
        project,
        pl.DataFrame(
            {
                "region": pl.Series(
                    [" North", " North", "South", "South", "South"], dtype=pl.Categorical
                )
            }
        ),
    )
    graph = _graph(project)
    _build(client, graph)

    payload = _levels(client, graph, ["region"])
    chosen = _column(payload, "region")["values"][1]
    assert chosen["value"] == " North", payload

    rated = apply_rating_step_from_config(
        pl.scan_parquet(path),
        {
            "tables": [
                {
                    "factors": ["region"],
                    "outputColumn": "region_factor",
                    "defaultValue": 1.0,
                    "entries": [{"region": chosen["value"], "value": 2.5}],
                }
            ]
        },
    ).collect()

    assert int((rated["region_factor"] == 2.5).sum()) == chosen["count"] == 2


def test_levels_are_the_most_common_first_and_capped_at_the_limit(
    client: TestClient, project: Path
) -> None:
    """What the cap keeps is what the data is mostly made of."""
    _write_source(project, pl.DataFrame({"region": ["c"] * 3 + ["a"] * 2 + ["b"] * 2 + ["d"] * 1}))
    graph = _graph(project)
    _build(client, graph)

    uncapped = _levels(client, graph, ["region"])
    capped = _levels(client, graph, ["region"], value_limit=2)

    # Most common first, and ties broken by the value so the order is stable.
    assert _values(uncapped, "region") == ["c", "a", "b", "d"]
    assert _values(capped, "region") == ["c", "a"]
    # The cap hides levels; it does not change how many there are.
    assert _column(capped, "region")["distinct_count"] == 4


def test_a_blank_is_not_a_level_and_a_missing_value_is_counted_as_one(
    client: TestClient, project: Path
) -> None:
    """Neither is something to rate on, and only one of them is worth counting."""
    _write_source(project, pl.DataFrame({"region": ["North", "", "   ", None, "North"]}))
    graph = _graph(project)
    _build(client, graph)

    payload = _levels(client, graph, ["region"])

    region = _column(payload, "region")
    assert _values(payload, "region") == ["North"]
    assert region["distinct_count"] == 1
    assert region["null_count"] == 1
    assert payload["total_rows"] == 5


def test_every_column_asked_about_is_answered_in_the_order_asked(
    client: TestClient, project: Path
) -> None:
    """The editor asks for the factors of its tables; each gets its own levels."""
    _write_source(
        project,
        pl.DataFrame({"region": ["North", "South"], "cover": ["TPFT", "TPFT"]}),
    )
    graph = _graph(project)
    _build(client, graph)

    payload = _levels(client, graph, ["cover", "region", "cover"])

    # Asked for twice, read once, in the order first asked.
    assert [column["column"] for column in payload["columns"]] == ["cover", "region"]
    assert _values(payload, "cover") == ["TPFT"]
    assert _values(payload, "region") == ["North", "South"]


def test_an_enum_column_is_keyed_as_the_lookup_keys_it(client: TestClient, project: Path) -> None:
    """A dictionary-backed column offers the text, not its physical encoding."""
    _write_source(
        project,
        pl.DataFrame(
            {
                "cover": pl.Series(
                    ["TPFT", "Comprehensive"], dtype=pl.Enum(["TPFT", "Comprehensive"])
                )
            }
        ),
    )
    graph = _graph(project)
    _build(client, graph)

    payload = _levels(client, graph, ["cover"])

    assert sorted(_values(payload, "cover")) == ["Comprehensive", "TPFT"]


# --------------------------------------------------------------- refusals


def test_a_column_the_data_does_not_have_is_refused_by_name(
    client: TestClient, project: Path
) -> None:
    _write_source(project, pl.DataFrame({"region": ["North"]}))
    graph = _graph(project)
    _build(client, graph)

    response = _levels_response(client, graph, ["region", "postcode"])

    assert response.status_code == 422
    assert "'postcode'" in response.json()["detail"]


def test_a_column_that_is_not_text_is_refused(client: TestClient, project: Path) -> None:
    """Raw factor levels come from text; a number's levels are banding's job."""
    _write_source(project, pl.DataFrame({"premium": [1.0, 2.0]}))
    graph = _graph(project)
    _build(client, graph)

    response = _levels_response(client, graph, ["premium"])

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "'premium'" in detail and "text" in detail


def test_a_node_with_no_data_point_is_a_bad_request(client: TestClient, project: Path) -> None:
    _write_source(project, pl.DataFrame({"region": ["North"]}))
    graph = _graph(project)
    graph["edges"] = []

    response = _levels_response(client, graph, ["region"])

    assert response.status_code == 400


def test_levels_are_not_read_until_the_data_is_cached(client: TestClient, project: Path) -> None:
    _write_source(project, pl.DataFrame({"region": ["North"]}))
    graph = _graph_with_transform(project)

    payload = _levels(client, graph, ["region"])

    assert payload["status"] == "cache_required"
    assert payload["columns"] == []
    assert payload["total_rows"] == 0


def test_a_point_that_has_moved_on_is_not_read_at_all(client: TestClient, project: Path) -> None:
    """Levels are never read from a generation the node has left."""
    _write_source(project, pl.DataFrame({"region": ["North", "South"]}))
    graph = _graph_with_transform(project)
    _build(client, graph)
    assert _levels(client, graph, ["region"])["status"] == "ok"

    # Editing the transform changes what the node reads, so its built
    # generation describes data this graph no longer produces.
    edited = _graph_with_transform(project, code="df = source.head(1)")

    payload = _levels(client, edited, ["region"])

    assert payload["status"] == "cache_required"
    assert payload["point"]["state"] == "stale"
    assert payload["columns"] == []


def test_levels_over_their_memory_budget_are_reported_as_such(
    client: TestClient, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Admission failures answer 507 with the execution payload, not a 500."""
    from haute._execution_admission import ExecutionAdmissionError
    from haute._execution_context import ExecutionProfile
    from haute.routes import _synchronous_analysis as analysis_mod

    _write_source(project, pl.DataFrame({"region": ["North"]}))
    graph = _graph(project)

    def refuse_admission(**_kwargs: Any) -> Any:
        raise ExecutionAdmissionError(
            "rating_levels",
            profile=ExecutionProfile.EXPLORE_ANALYSIS,
            memory_limit_bytes=1,
            rss_at_admission_bytes=None,
            reason="in_flight_limit_reached",
        )

    monkeypatch.setattr(analysis_mod, "create_admitted_execution_context", refuse_admission)

    response = _levels_response(client, graph, ["region"])

    assert response.status_code == 507
    assert response.json()["detail"]["error_code"] == "memory_limit"


def test_a_column_whose_name_has_a_space_is_read_as_named(
    client: TestClient, project: Path
) -> None:
    """Execution rates on the column as named, so the levels come from it too."""
    _write_source(project, pl.DataFrame({"region ": ["North", "South"], "region": ["X", "X"]}))
    graph = _graph(project)
    _build(client, graph)

    payload = _levels(client, graph, ["region "])

    assert [column["column"] for column in payload["columns"]] == ["region "]
    assert _values(payload, "region ") == ["North", "South"]


def test_a_request_naming_no_column_is_refused(client: TestClient, project: Path) -> None:
    _write_source(project, pl.DataFrame({"region": ["North"]}))
    graph = _graph(project)

    assert _levels_response(client, graph, []).status_code == 422
    assert _levels_response(client, graph, ["   "]).status_code == 422


# ------------------------------------------------------------ versioning


def _signature(project: Path, graph: dict[str, Any]) -> str:
    from haute._data_points import DataPointResolver
    from haute._node_snapshots import NodeSnapshotStore
    from haute._types import PipelineGraph

    resolver = DataPointResolver(
        PipelineGraph.model_validate(graph), source="live", store=NodeSnapshotStore(project)
    )
    return str(resolver.node_output_signature("shaped"))


def test_a_rebuilt_snapshot_is_read_again_rather_than_answered_from_the_memo(
    client: TestClient, project: Path
) -> None:
    """A refresh is new data under an *unchanged* signature, so it is read again.

    The transform samples its rows, so a forced rebuild produces different data
    from identical inputs: the node, its code, its inputs and therefore its
    signature are all the same across the refresh, and only the generation
    differs. A memo keyed by anything but the generation would answer the second
    question with the first answer — and label it with a version the editor
    would then take for its own point's.
    """
    _write_source(project, pl.DataFrame({"region": ["North"] * 50 + ["South"] * 50}))
    graph = _graph_with_transform(project, code="df = source.collect().sample(fraction=0.5).lazy()")
    first_point = _build(client, graph)
    first_signature = _signature(project, graph)

    first = _levels(client, graph, ["region"])
    assert first["data_version"] == first_point["data_version"]
    assert first["total_rows"] == 50

    second_point = _build(client, graph, refresh=True)
    # Same question, a second answer to it.
    assert _signature(project, graph) == first_signature
    assert second_point["data_version"] != first_point["data_version"]

    second = _levels(client, graph, ["region"])

    assert second["data_version"] == second_point["data_version"]
    assert second["total_rows"] == 50
    assert sorted(_values(second, "region")) == ["North", "South"]
