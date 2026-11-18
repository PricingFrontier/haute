"""A node's own failure is named by a Data Output's Write and by training, as a preview
shows it on the node (specs/optimiser/error-detail-policy.md).

The failure is classified where it is raised, in the sink's worker and the preparation's
child alike, so the node's name and its message cross the process boundary, and no answer
carries a file path.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import polars as pl
import pytest
from fastapi.testclient import TestClient

from tests._preview_targets import node_target
from tests._project import project_app, use_project
from tests.test_data_output_seeding import _graph, _inline_worker, _write
from tests.test_training_seeding import _train

_RAISED = "no rate for region 7"
_ROWS = 40


@pytest.fixture()
def project(haute_scratch: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    use_project(haute_scratch)
    for profile in ("LAZY_SINK", "TRAINING"):
        monkeypatch.setenv(f"HAUTE_{profile}_MEMORY_LIMIT_MB", "1024")
    (haute_scratch / "main.py").write_text("# pipeline\n", encoding="utf-8")
    pl.DataFrame(
        {
            "id": list(range(_ROWS)),
            "a": [float(value % 7) for value in range(_ROWS)],
            "b": [float(value % 5) for value in range(_ROWS)],
            "y": [float(value % 3) for value in range(_ROWS)],
        }
    ).write_parquet(haute_scratch / "quotes.parquet")
    pl.DataFrame(
        {"id": list(range(_ROWS)), "d": [value / 10 for value in range(_ROWS)]}
    ).write_parquet(haute_scratch / "claims.parquet")
    return haute_scratch


def _failing(project: Path, *, with_training: bool = False) -> dict[str, Any]:
    """The seeding graph, its node ``B`` raising the analyst's own error."""
    graph = _graph(project, with_training=with_training)
    for node in graph["nodes"]:
        if node["id"] == "B":
            node["data"]["config"]["code"] = f"raise ValueError({_RAISED!r})"
    return graph


def _named(detail: Any) -> None:
    assert isinstance(detail, dict), detail
    assert detail["error_code"] == "node_failed"
    assert detail["node_id"] == "B"
    assert detail["message"] == f"'B' failed: {_RAISED}"


@pytest.mark.parametrize("worker", ["in_process", "isolated"])
def test_a_write_names_the_node_that_failed(
    project: Path, monkeypatch: pytest.MonkeyPatch, worker: str
) -> None:
    if worker == "in_process":
        _inline_worker(monkeypatch)

    written = _write(monkeypatch, _failing(project))

    assert written.status_code == 422, written.body
    _named(written.body["detail"])
    assert str(project) not in str(written.body)


def test_training_names_the_node_that_failed(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = _train(monkeypatch, _failing(project, with_training=True))

    assert run.job["status"] == "contract_error", run.job
    assert run.job["message"] == f"'B' failed: {_RAISED}"
    _named(run.job["error_detail"])
    assert str(project) not in str(run.job["message"])


def test_the_preview_shows_the_message_the_write_names(project: Path) -> None:
    response = TestClient(project_app()).post(
        "/api/pipeline/preview", json={"graph": _failing(project), "target": node_target("B")}
    )

    assert response.status_code == 200, response.text
    assert _RAISED in str(response.json())
