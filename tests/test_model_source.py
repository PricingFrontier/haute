"""The Model Scoring node's model source is parsed once (MSC-01, MSC-03).

Every consumer that loads a Model Scoring model parses the node config through
``parse_model_source`` and loads through ``load_scoring_model``, so a config
cannot load a different model — or fail differently — in one context than in
another.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any
from unittest.mock import ANY, patch

import pytest

from haute._model_source import (
    FileModelSource,
    IncompleteModelSourceError,
    RegisteredModelSource,
    RunModelSource,
    load_scoring_model,
    parse_model_source,
    require_model_source,
)
from haute.errors import ConfigError
from tests._source_files import source_files

INVALID_SOURCES: dict[str, dict[str, Any]] = {
    "run without run_id": {"sourceType": "run", "run_id": ""},
    "registered without model": {"sourceType": "registered", "registered_model": ""},
    "file without model_path": {"sourceType": "file", "model_path": ""},
    "unknown sourceType": {"sourceType": "mlflow", "run_id": "abc"},
    "version beside alias": {
        "sourceType": "registered",
        "registered_model": "pricing",
        "version": "3",
        "alias": "champion",
    },
}


def _config(source: dict[str, Any]) -> dict[str, Any]:
    return {**source, "artifact_path": "model.cbm", "task": "regression"}


def _parse_error(config: dict[str, Any]) -> ConfigError:
    with pytest.raises(ConfigError) as caught:
        parse_model_source(config)
    return caught.value


def _same_error(actual: BaseException, expected: ConfigError) -> None:
    assert type(actual) is type(expected)
    assert str(actual) == str(expected)
    assert actual.context == expected.context  # type: ignore[attr-defined]


def _build(config: dict[str, Any]) -> None:
    from haute._builders import _build_node_fn
    from haute.graph_utils import GraphNode, NodeData

    node = GraphNode(id="score", data=NodeData(label="score", nodeType="modelScore", config=config))
    _build_node_fn(node, source="live")


def _score_from_config(config: dict[str, Any], tmp_path: Path) -> None:
    import polars as pl

    from haute._model_scorer import score_from_config

    path = tmp_path / "config" / "model_scoring" / "score.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(config))
    score_from_config(pl.LazyFrame({"a": [1.0]}), config=str(path), base_dir=str(tmp_path))


def _explain(config: dict[str, Any], _tmp_path: Path) -> None:
    from haute._model_explainability import explain_model_score_from_config

    explain_model_score_from_config(
        config,
        {"a": 1.0},
        {"prediction": 0.5},
        prediction_column="prediction",
        prediction_value=None,
    )


@pytest.mark.parametrize("source", list(INVALID_SOURCES.values()), ids=list(INVALID_SOURCES))
@pytest.mark.parametrize(
    "consumer",
    [
        pytest.param(lambda config, _tmp: _build(config), id="builder"),
        pytest.param(_score_from_config, id="score_from_config"),
        pytest.param(_explain, id="trace_explanation"),
    ],
)
@patch("haute._mlflow_io.load_mlflow_model")
def test_an_invalid_source_fails_identically_in_every_consumer(
    mock_load: Any, consumer: Any, source: dict[str, Any], tmp_path: Path
) -> None:
    config = _config(source)
    expected = _parse_error(config)

    with pytest.raises(ConfigError) as caught:
        consumer(config, tmp_path)

    _same_error(caught.value, expected)
    mock_load.assert_not_called()


@pytest.mark.parametrize(
    ("source", "missing_field"),
    [
        ({"sourceType": "run"}, "run_id"),
        ({"sourceType": "registered", "registered_model": ""}, "registered_model"),
        ({"sourceType": "file"}, "model_path"),
    ],
)
def test_an_empty_identifier_is_an_incomplete_source(
    source: dict[str, Any], missing_field: str
) -> None:
    error = _parse_error(source)

    assert isinstance(error, IncompleteModelSourceError)
    assert error.context["missing_field"] == missing_field


@pytest.mark.parametrize(
    "config",
    [
        {"sourceType": 1, "run_id": "abc"},
        {"sourceType": "run", "run_id": 7},
        {"sourceType": "run", "run_id": "abc", "artifact_path": ["model.cbm"]},
        {"sourceType": "registered", "registered_model": "m", "alias": " champion"},
        {"sourceType": "file", "model_path": ["model.cbm"]},
    ],
    ids=[
        "non-string type",
        "non-string run_id",
        "non-string artifact",
        "malformed alias",
        "non-string model_path",
    ],
)
def test_a_malformed_source_field_is_a_config_error_not_an_incomplete_source(
    config: dict[str, Any],
) -> None:
    error = _parse_error(config)

    assert not isinstance(error, IncompleteModelSourceError)


def test_an_untouched_node_has_no_source_and_require_refuses_it() -> None:
    assert parse_model_source({}) is None
    assert parse_model_source({"sourceType": ""}) is None
    with pytest.raises(ConfigError, match="sourceType"):
        require_model_source({"output_column": "pred"})


@pytest.mark.parametrize(
    ("config", "expected"),
    [
        (
            {"sourceType": "run", "run_id": "abc"},
            RunModelSource(run_id="abc", artifact_path="", mlflow_destination=""),
        ),
        (
            {
                "sourceType": "run",
                "run_id": "abc",
                "artifact_path": "model.cbm",
                "mlflow_destination": "databricks",
                "version": "9",
            },
            RunModelSource(
                run_id="abc", artifact_path="model.cbm", mlflow_destination="databricks"
            ),
        ),
        (
            {"sourceType": "registered", "registered_model": "pricing"},
            RegisteredModelSource(
                registered_model="pricing",
                version="latest",
                alias="",
                artifact_path="",
                mlflow_destination="",
            ),
        ),
        (
            {"sourceType": "registered", "registered_model": "pricing", "alias": "champion"},
            RegisteredModelSource(
                registered_model="pricing",
                version="",
                alias="champion",
                artifact_path="",
                mlflow_destination="",
            ),
        ),
        (
            {"sourceType": "registered", "registered_model": "pricing", "version": "4"},
            RegisteredModelSource(
                registered_model="pricing",
                version="4",
                alias="",
                artifact_path="",
                mlflow_destination="",
            ),
        ),
        (
            # A file source never reads MLflow fields.
            {
                "sourceType": "file",
                "model_path": "models/freq.cbm",
                "run_id": "abc",
                "mlflow_destination": "databricks",
            },
            FileModelSource(model_path="models/freq.cbm"),
        ),
    ],
    ids=["run defaults", "run fields", "registered latest", "registered alias", "pinned", "file"],
)
def test_the_parser_owns_the_source_defaults(config: dict[str, Any], expected: object) -> None:
    assert parse_model_source(config) == expected


@patch("haute._mlflow_io.load_mlflow_model")
def test_a_run_source_loads_without_registry_fields(mock_load: Any) -> None:
    load_scoring_model(
        RunModelSource(run_id="abc", artifact_path="model.cbm", mlflow_destination="local"),
        "classification",
    )

    mock_load.assert_called_once_with(
        source_type="run",
        run_id="abc",
        artifact_path="model.cbm",
        task="classification",
        destination="local",
        backend=ANY,
    )


@patch("haute._mlflow_io.load_mlflow_model")
def test_a_registered_source_loads_by_alias(mock_load: Any) -> None:
    load_scoring_model(
        require_model_source(
            {"sourceType": "registered", "registered_model": "pricing", "alias": "champion"}
        ),
        "regression",
    )

    mock_load.assert_called_once_with(
        source_type="registered",
        registered_model="pricing",
        version="",
        alias="champion",
        artifact_path="",
        task="regression",
        destination="",
        backend=ANY,
    )


@pytest.mark.parametrize("source", list(INVALID_SOURCES.values()), ids=list(INVALID_SOURCES))
def test_the_deploy_passthrough_guard_chains_the_parse_failure(source: dict[str, Any]) -> None:
    from haute.deploy._scorer import _validate_deploy_model_score_source
    from haute.errors import DeployError
    from haute.graph_utils import GraphNode, NodeData

    config = _config(source)
    expected = _parse_error(config)
    node = GraphNode(id="score", data=NodeData(label="score", nodeType="modelScore", config=config))

    with pytest.raises(DeployError, match="passthrough") as caught:
        _validate_deploy_model_score_source(node, {})

    _same_error(caught.value.__cause__, expected)  # type: ignore[arg-type]


def test_load_mlflow_model_is_called_only_through_the_source_seam() -> None:
    package = Path(__file__).resolve().parents[1] / "src" / "haute"
    callers: list[str] = []
    for path in source_files(package):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name == "load_mlflow_model":
                callers.append(path.relative_to(package).as_posix())

    assert callers and set(callers) == {"_model_source.py"}
