"""Target relationships and key checks over an Explore node's data point (EDA-E10).

These pin what the server answers: bounded per-feature levels ranked by how much
of the target's variance they explain, an exact uniqueness check of the chosen
key columns, and the same cache-required, caching and cancellation behaviour as
every other synchronous analysis.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import polars as pl
import pytest
from fastapi import HTTPException

from tests.conftest import make_edge, make_graph

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

_TERMINAL = {"completed", "error", "cancelled", "superseded", "timed_out", "memory_limited"}


@pytest.fixture(autouse=True)
def _pinned_admission_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin modest budgets so these tests do not depend on the host's free RAM."""
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


def _write_source(project: Path, frame: pl.DataFrame) -> None:
    frame.write_parquet(project / "quotes.parquet")


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
                "path": str(project / "quotes.parquet"),
                "arguments": {},
            },
        },
    }


_EXPLORE = {"id": "explore", "data": {"label": "Explore", "nodeType": "explore", "config": {}}}


def _graph(project: Path) -> dict[str, Any]:
    return make_graph(
        {
            "source_file": str(project / "main.py"),
            "preamble": "import polars as pl",
            "nodes": [_source_node(project), _EXPLORE],
            "edges": [make_edge("source", "explore").model_dump()],
        }
    ).model_dump()


def _graph_with_transform(project: Path) -> dict[str, Any]:
    return make_graph(
        {
            "source_file": str(project / "main.py"),
            "preamble": "import polars as pl",
            "nodes": [
                _source_node(project),
                {
                    "id": "shaped",
                    "data": {
                        "label": "shaped",
                        "nodeType": "polars",
                        "config": {"code": "df = source"},
                    },
                },
                _EXPLORE,
            ],
            "edges": [
                make_edge("source", "shaped").model_dump(),
                make_edge("shaped", "explore").model_dump(),
            ],
        }
    ).model_dump()


def _build(client: TestClient, graph: dict[str, Any]) -> None:
    body = {"graph": graph, "node_id": "explore", "source": "live"}
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


def _body(graph: dict[str, Any], **extra: Any):
    from haute.schemas import ExploreRelationshipsRequest

    return ExploreRelationshipsRequest.model_validate(
        {"graph": graph, "node_id": "explore", "source": "live", **extra}
    )


def _service():
    from haute.routes._explore_relationships import ExploreRelationshipsService
    from haute.routes.node_data import _node_data_service

    return ExploreRelationshipsService(_node_data_service)


def _ask(graph: dict[str, Any], **extra: Any):
    return _service().relationships(_body(graph, **extra))


def _refused(graph: dict[str, Any], **extra: Any) -> str:
    with pytest.raises(HTTPException) as caught:
        _ask(graph, **extra)
    assert caught.value.status_code == 422
    return str(caught.value.detail)


def _relationship(response, feature: str):
    for relationship in response.relationships:
        if relationship.feature == feature:
            return relationship
    raise AssertionError(feature)


@pytest.fixture()
def cached(client: TestClient, project: Path):
    """Write a source, cache the Explore node's point, and return its graph."""

    def make(frame: pl.DataFrame) -> dict[str, Any]:
        _write_source(project, frame)
        graph = _graph(project)
        _build(client, graph)
        return graph

    return make


# ------------------------------------------------------------ cache state


def test_nothing_is_read_until_the_point_is_cached(client: TestClient, project: Path) -> None:
    _write_source(project, pl.DataFrame({"y": [1.0], "x": ["a"]}))
    graph = _graph_with_transform(project)

    response = _ask(graph, target="y", features=["x"])

    assert response.status == "cache_required"
    assert response.relationships == []
    assert response.key_check is None


# ------------------------------------------------------------ validation


def test_requests_that_ask_nothing_answerable_are_refused(cached) -> None:
    graph = cached(
        pl.DataFrame(
            {
                "y": [1.0, 2.0],
                "w": [1.0, 1.0],
                "x": ["a", "b"],
                "s": ["p", "q"],
            }
        )
    )

    assert "at least one" in _refused(graph, features=[" "], key_columns=[])
    assert "target" in _refused(graph, features=["x"])
    assert "differ" in _refused(graph, target="y", weight="y", features=["x"])
    assert "'y'" in _refused(graph, target="y", features=["y"])
    assert "'w'" in _refused(graph, target="y", weight="w", features=["w"])
    assert "'nope'" in _refused(graph, target="y", features=["x", "nope"])
    assert "numeric or boolean" in _refused(graph, target="s", features=["x"])


def test_a_nested_dtype_feature_cannot_be_grouped(cached) -> None:
    graph = cached(
        pl.DataFrame(
            {
                "y": [1.0, 2.0],
                "x": [[1, 2], [3, 4]],
            }
        )
    )

    assert "cannot be grouped" in _refused(graph, target="y", features=["x"])


# ------------------------------------------------------------ numeric features


def test_a_numeric_feature_is_read_as_ten_bins_and_a_missing_level(cached) -> None:
    xs = [float(v) for v in range(11)] + [None]
    ys = [2.0 * v for v in range(11)] + [100.0]
    graph = cached(pl.DataFrame({"x": xs, "y": ys, "c": [5.0] * 12}))

    response = _ask(graph, target="y", features=["x", "c"])

    assert response.status == "ok"
    assert response.total_rows == response.used_rows == 12
    x = _relationship(response, "x")
    assert x.kind == "numeric" and not x.levels_truncated
    assert [level.kind for level in x.levels] == ["bin"] * 10 + ["missing"]
    assert x.levels[0].label == "0 to 1"
    assert x.levels[9].label == "9 to 10"
    assert [level.rows for level in x.levels] == [1] * 9 + [2, 1]
    assert x.levels[9].target_mean == pytest.approx(19.0)
    assert x.levels[10].label == "(missing)"
    assert x.levels[10].target_mean == pytest.approx(100.0)

    constant = _relationship(response, "c")
    assert [(level.label, level.kind, level.rows) for level in constant.levels] == [
        ("5", "bin", 12)
    ]
    assert constant.strength == 0.0


# ------------------------------------------------------------ categorical features


def test_a_categorical_feature_keeps_its_heaviest_levels_and_folds_the_rest(cached) -> None:
    graph = cached(
        pl.DataFrame(
            {
                "x": ["a"] * 4 + ["b"] * 3 + ["c"] * 2 + ["d"] + [None],
                "y": [1.0] * 4 + [2.0] * 3 + [3.0] * 2 + [5.0] + [7.0],
            }
        )
    )

    response = _ask(graph, target="y", features=["x"], level_limit=2)

    x = _relationship(response, "x")
    assert x.kind == "categorical" and x.levels_truncated
    assert [(level.label, level.kind, level.rows) for level in x.levels] == [
        ("a", "value", 4),
        ("b", "value", 3),
        ("Other", "other", 3),
        ("(missing)", "missing", 1),
    ]
    assert x.levels[2].target_mean == pytest.approx((3.0 * 2 + 5.0) / 3)
    assert x.levels[3].target_mean == pytest.approx(7.0)

    untruncated = _relationship(_ask(graph, target="y", features=["x"]), "x")
    assert not untruncated.levels_truncated
    assert [level.label for level in untruncated.levels] == ["a", "b", "c", "d", "(missing)"]


# ------------------------------------------------------------ strength


def test_strength_is_the_share_of_variance_the_levels_explain(cached) -> None:
    graph = cached(
        pl.DataFrame(
            {
                "y": [1.0, 1.0, 5.0, 5.0, 9.0, 9.0],
                "decides": ["a", "a", "b", "b", "c", "c"],
                "independent": ["p", "q", "p", "q", "p", "q"],
            }
        )
    )

    response = _ask(graph, target="y", features=["independent", "decides"])

    assert [r.feature for r in response.relationships] == ["decides", "independent"]
    assert response.relationships[0].strength == pytest.approx(1.0, abs=1e-9)
    assert response.relationships[1].strength == pytest.approx(0.0, abs=1e-9)


# ------------------------------------------------------------ weights


def test_rows_without_a_usable_weight_are_left_out(cached) -> None:
    graph = cached(
        pl.DataFrame(
            {
                "y": [1.0, 3.0, 50.0, 60.0, None],
                "w": [1.0, 3.0, -1.0, None, 1.0],
                "x": ["a", "a", "a", "a", "a"],
            }
        )
    )

    response = _ask(graph, target="y", weight="w", features=["x"])

    assert response.total_rows == 5
    assert response.used_rows == 2
    (level,) = _relationship(response, "x").levels
    assert level.rows == 2
    assert level.weight == pytest.approx(4.0)
    assert level.target_mean == pytest.approx((1.0 + 9.0) / 4.0)


# ------------------------------------------------------------ key check


def test_the_key_check_counts_distinct_duplicate_and_missing_keys(cached) -> None:
    graph = cached(
        pl.DataFrame(
            {
                "id": [1, 2, 3, 4],
                "dup": [1, 1, 1, 2],
                "a": [1, 1, 2, 2],
                "b": ["x", "y", "x", "y"],
                "gap": [1, None, 2, 3],
            }
        )
    )

    single = _ask(graph, key_columns=["id"]).key_check
    assert single is not None and single.unique and single.distinct_keys == 4

    duplicated = _ask(graph, key_columns=["dup"]).key_check
    assert duplicated is not None and not duplicated.unique
    assert (duplicated.distinct_keys, duplicated.duplicate_rows) == (2, 2)

    assert not _ask(graph, key_columns=["a"]).key_check.unique
    combined = _ask(graph, key_columns=["a", "b"])
    assert combined.key_check.unique
    assert combined.key_check.columns == ["a", "b"]
    assert combined.used_rows == 0 and combined.relationships == []

    nulls = _ask(graph, key_columns=["gap"]).key_check
    assert nulls.null_key_rows == 1 and nulls.duplicate_rows == 0 and not nulls.unique


# ------------------------------------------------------------ caching and cancellation


def test_an_identical_question_is_answered_from_the_cache(cached) -> None:
    graph = cached(pl.DataFrame({"y": [1.0, 2.0], "x": ["a", "b"], "z": ["p", "q"]}))
    service = _service()

    with patch.object(service, "_compute", wraps=service._compute) as compute:
        first = service.relationships(_body(graph, target="y", features=["x"]))
        second = service.relationships(_body(graph, target="y", features=["x"]))
        assert compute.call_count == 1
        assert first == second
        service.relationships(_body(graph, target="y", features=["x", "z"]))
        assert compute.call_count == 2


def test_a_cancelled_token_stops_the_analysis(cached) -> None:
    from haute._execution_context import ExecutionCancellationToken, ExecutionCancelledError

    graph = cached(pl.DataFrame({"y": [1.0, 2.0], "x": ["a", "b"]}))
    token = ExecutionCancellationToken()
    token.cancel()

    with pytest.raises(ExecutionCancelledError):
        _service().relationships(_body(graph, target="y", features=["x"]), cancellation_token=token)


# ------------------------------------------------------------ route


def test_the_route_answers_and_refuses(client: TestClient, cached) -> None:
    graph = cached(pl.DataFrame({"y": [1.0, 2.0], "x": ["a", "b"]}))
    body = {"graph": graph, "node_id": "explore", "source": "live"}

    ok = client.post("/api/explore/relationships", json={**body, "target": "y", "features": ["x"]})
    assert ok.status_code == 200, ok.text
    assert ok.json()["status"] == "ok"
    assert ok.json()["relationships"][0]["feature"] == "x"

    refused = client.post("/api/explore/relationships", json={**body, "features": ["x"]})
    assert refused.status_code == 422
    assert isinstance(refused.json()["detail"], str)


def test_unhashable_features_and_key_columns_are_refused() -> None:
    """Object columns cannot reach a parquet-backed point, so the check is tested directly."""
    from types import SimpleNamespace
    from typing import Any, cast

    from haute.routes._explore_relationships import _Question

    frame = pl.DataFrame(
        {"y": [1.0, 2.0], "obj": pl.Series([object(), object()], dtype=pl.Object)}
    ).lazy()
    leased = cast(Any, SimpleNamespace(scan=frame, data_version="v1"))
    context = cast(Any, None)

    def refused(question: _Question) -> str:
        with pytest.raises(HTTPException) as caught:
            _service()._compute(question, leased, context)
        assert caught.value.status_code == 422
        return str(caught.value.detail)

    as_feature = _Question(
        target="y", weight=None, features=("obj",), key_columns=(), level_limit=12
    )
    as_key = _Question(target=None, weight=None, features=(), key_columns=("obj",), level_limit=12)
    assert "cannot be grouped" in refused(as_feature)
    assert "cannot be compared for uniqueness" in refused(as_key)


def test_numeric_features_keep_distinct_large_integers_apart(cached) -> None:
    base = 10**18
    graph = cached(
        pl.DataFrame(
            {
                "y": [1.0, 2.0, 3.0],
                "x": pl.Series([base, base + 1, base + 2], dtype=pl.Int64),
            }
        )
    )

    x = _relationship(_ask(graph, target="y", features=["x"]), "x")

    # A Float64 cast would call these one value; exact integer bins keep three.
    assert [(level.label, level.rows) for level in x.levels] == [
        (f"{base} to {base + 1}", 1),
        (f"{base + 1} to {base + 2}", 1),
        (f"{base + 2} to {base + 2}", 1),
    ]
    assert x.strength == pytest.approx(1.0, abs=1e-9)


def test_strength_survives_a_large_target_mean_and_is_scale_invariant(cached) -> None:
    graph = cached(
        pl.DataFrame(
            {
                "offset": [1_000_000.0, 1_000_000.0, 1_000_001.0, 1_000_001.0],
                "scaled": [1e-6, 1e-6, 2e-6, 2e-6],
                "flat": [1_000_000.0] * 4,
                "x": ["a", "a", "b", "b"],
            }
        )
    )

    # The levels explain all of each target's variance whatever its offset or scale.
    for target in ("offset", "scaled"):
        x = _relationship(_ask(graph, target=target, features=["x"]), "x")
        assert x.strength == pytest.approx(1.0, abs=1e-9)
    offset = _relationship(_ask(graph, target="offset", features=["x"]), "x")
    assert [level.target_mean for level in offset.levels] == pytest.approx(
        [1_000_000.0, 1_000_001.0]
    )
    # A constant target has no variance to explain, however large it is.
    assert _relationship(_ask(graph, target="flat", features=["x"]), "x").strength == 0.0


def test_duration_and_binary_features_are_labelled_like_the_profile(cached) -> None:
    from datetime import timedelta

    graph = cached(
        pl.DataFrame(
            {
                "y": [1.0, 2.0, 3.0, 4.0],
                "wait": pl.Series(
                    [timedelta(hours=1), timedelta(hours=1), timedelta(hours=2), None],
                    dtype=pl.Duration("us"),
                ),
                "raw": pl.Series([b"ok", b"ok", b"\xff\xfe", None], dtype=pl.Binary),
            }
        )
    )

    response = _ask(graph, target="y", features=["wait", "raw"])

    wait = _relationship(response, "wait")
    raw = _relationship(response, "raw")
    assert wait.kind == raw.kind == "categorical"
    assert [(level.kind, level.rows) for level in wait.levels] == [
        ("value", 2),
        ("value", 1),
        ("missing", 1),
    ]
    assert [level.kind for level in raw.levels] == ["value", "value", "missing"]
    assert raw.levels[0].label == "ok"
    assert "\ufffd" in raw.levels[1].label


def test_every_part_of_the_question_is_part_of_the_cache_key(cached) -> None:
    graph = cached(
        pl.DataFrame(
            {
                "y": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
                "w": [1.0, 1.0, 1.0, 1.0, 1.0, 5.0],
                "z": [6.0, 5.0, 4.0, 3.0, 2.0, 1.0],
                "x": ["a", "b", "c", "a", "b", "c"],
                "k": [1, 1, 2, 2, 3, 3],
            }
        )
    )
    service = _service()
    base = {"target": "y", "features": ["x"]}
    first = service.relationships(_body(graph, **base))

    # Asking again is served from the cache and changes nothing.
    assert service.relationships(_body(graph, **base)) == first

    # Each dimension of the question changes the answer, so each must be in the key.
    by_target = service.relationships(_body(graph, target="z", features=["x"]))
    assert by_target.target == "z"
    assert (
        by_target.relationships[0].levels[0].target_mean
        != first.relationships[0].levels[0].target_mean
    )

    by_weight = service.relationships(_body(graph, weight="w", **base))
    assert by_weight.weight == "w"
    assert by_weight.relationships[0].levels != first.relationships[0].levels

    by_limit = service.relationships(_body(graph, level_limit=2, **base))
    assert (
        by_limit.relationships[0].levels_truncated and not first.relationships[0].levels_truncated
    )

    by_keys = service.relationships(_body(graph, key_columns=["k"], **base))
    assert first.key_check is None
    assert by_keys.key_check is not None and by_keys.key_check.distinct_keys == 3


def test_the_request_limits_and_a_non_numeric_weight_are_enforced(
    client: TestClient, cached
) -> None:
    features = [f"f{index}" for index in range(51)]
    keys = [f"k{index}" for index in range(9)]
    graph = cached(
        pl.DataFrame(
            {
                "y": [1.0, 2.0],
                "label": ["a", "b"],
                **{name: [1.0, 2.0] for name in features},
                **{name: [1, 2] for name in keys},
            }
        )
    )

    def post(**extra: Any):
        # A raw payload: the model itself would refuse the over-limit lists.
        payload = {"graph": graph, "node_id": "explore", "source": "live", **extra}
        return client.post("/api/explore/relationships", json=payload)

    assert post(target="y", features=features[:50]).status_code == 200
    assert post(target="y", features=features).status_code == 422
    assert post(key_columns=keys[:8]).status_code == 200
    assert post(key_columns=keys).status_code == 422
    refused = post(target="y", weight="label", features=["f0"])
    assert refused.status_code == 422
    assert "weight must be numeric" in refused.json()["detail"]
