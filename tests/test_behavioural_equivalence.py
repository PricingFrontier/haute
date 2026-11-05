"""Each behavioural type interprets its config once (PCFG-R09).

A saved pipeline file's standalone runtime (``haute._standalone_nodes``) and the
canvas builder share the function that interprets a behavioural type's config, and
each keeps only what is genuinely its own (``specs/pipeline-config/low-level.md``,
"One interpretation per behavioural type"). For every behavioural type one config and
frame run through the builder and through ``run_configured_node``, and the frames must
be equal; each type's own rules are pinned beside.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import numpy as np
import polars as pl
import pytest
from polars.testing import assert_frame_equal

from haute._code_extraction import INCOMPLETE_STEPS_MESSAGE
from haute._config_io import config_path_for_node, node_config_json
from haute._mlflow_io import ScoringModel
from haute._model_scorer import _scenario_ctx
from haute._model_source import IncompleteModelSourceError
from haute._node_specs import NODE_SPECS
from haute._optimiser_io import _load_artifact_cached, _load_mlflow_cached
from haute._project_context import current_project
from haute._standalone_nodes import FunctionKind, run_configured_node
from haute._types import GraphNode, NodeData, NodeType, PipelineGraph
from haute._workbench_nodes import given_config, project_form_tables
from haute._workbench_output import WorkbenchOutputTables
from haute.deploy._bundler import collect_artifacts
from haute.errors import ConfigError
from haute.executor import _build_node_fn
from tests._project import use_project
from tests._workbench_helpers import form_table, workbench_project
from tests.conftest import write_node_config

NAME = "node"


@pytest.fixture()
def project(tmp_path: Path) -> Iterator[Path]:
    """A Haute project root that both runs read their files from."""
    (tmp_path / ".git").mkdir()
    (tmp_path / "haute.toml").write_text('[project]\nname = "equivalence"\n', encoding="utf-8")
    original = current_project().root
    use_project(tmp_path)
    try:
        yield tmp_path
    finally:
        use_project(original)


@pytest.fixture(autouse=True)
def _fresh_artifact_caches() -> Iterator[None]:
    _load_artifact_cached.cache_clear()
    _load_mlflow_cached.cache_clear()
    yield
    _load_artifact_cached.cache_clear()
    _load_mlflow_cached.cache_clear()


def _function(directory: Path, source: str) -> Callable[..., Any]:
    """The function *source* defines, as a pipeline file in *directory* defines it."""
    path = directory / "main.py"
    namespace: dict[str, Any] = {"__file__": str(path), "__name__": "pipeline_under_test"}
    exec(compile(f"import polars as pl\n\n{source}", str(path), "exec"), namespace)
    return namespace[NAME]


def _declaration(directory: Path, inputs: Sequence[str]) -> Callable[..., Any]:
    return _function(directory, f"def {NAME}({', '.join(inputs)}): ...\n")


def _canvas(
    node_type: NodeType,
    config: dict[str, Any],
    frames: Sequence[pl.LazyFrame],
    inputs: Sequence[str],
    **build: Any,
) -> Any:
    """What the canvas builder's function makes of *frames*."""
    node = GraphNode(id=NAME, data=NodeData(label=NAME, nodeType=node_type, config=config))
    _name, fn, _is_source = _build_node_fn(node, source_names=list(inputs), **build)
    return fn(*frames)


def _standalone(
    project: Path,
    node_type: NodeType,
    config: dict[str, Any],
    frames: Sequence[pl.LazyFrame],
    inputs: Sequence[str],
    *,
    kind: FunctionKind = "declaration",
    fn: Callable[..., Any] | None = None,
    target_ports: Sequence[str | None] | None = None,
) -> Any:
    """What a saved file's decorator makes of *frames*, its config in the sidecar a save
    writes."""
    path = config_path_for_node(node_type, NAME)
    (project / path).parent.mkdir(parents=True, exist_ok=True)
    (project / path).write_text(node_config_json(node_type, config, node_label=NAME), "utf-8")
    return _run_standalone(
        project,
        node_type,
        path.as_posix(),
        frames,
        inputs,
        kind=kind,
        fn=fn,
        target_ports=target_ports,
    )


def _run_standalone(
    project: Path,
    node_type: NodeType,
    sidecar: str,
    frames: Sequence[pl.LazyFrame],
    inputs: Sequence[str],
    *,
    kind: FunctionKind = "declaration",
    fn: Callable[..., Any] | None = None,
    target_ports: Sequence[str | None] | None = None,
) -> Any:
    """What a saved file's decorator makes of *frames*, its config the *sidecar* file."""
    return run_configured_node(
        node_type,
        kind,
        name=NAME,
        config={"config": sidecar},
        fn=fn or _declaration(project, inputs),
        frames=tuple(frames),
        target_ports=target_ports,
    )


def _collected(result: Any) -> Any:
    if isinstance(result, WorkbenchOutputTables):
        return {name: frame.collect() for name, frame in result.items()}
    return result.lazy().collect()


def _assert_equal(canvas: Any, standalone: Any) -> None:
    canvas, standalone = _collected(canvas), _collected(standalone)
    if isinstance(canvas, dict):
        assert list(canvas) == list(standalone)
        for name, frame in canvas.items():
            assert_frame_equal(frame, standalone[name])
    else:
        assert_frame_equal(canvas, standalone)


# ---------------------------------------------------------------------------
# One config, one frame, the same result: one case per behavioural type
# ---------------------------------------------------------------------------

_BANDING = {
    "factors": [
        {
            "banding": "categorical",
            "column": "region",
            "output_column": "band",
            "rules": [
                {"value": "N", "assignment": "north"},
                {"value": "S", "assignment": "south"},
            ],
        }
    ]
}
_RATING_STEP = {
    "tables": [
        {
            "factors": ["band"],
            "output_column": "factor",
            "on_missing": "neutral",
            "entries": [{"band": "north", "value": 2.0}, {"band": "south", "value": 0.5}],
        }
    ],
    "combined_outputs": [
        {"output_column": "premium", "operation": "multiply", "base_value": 100.0}
    ],
}
_EXPANDER = {
    "column_name": "price",
    "min_value": 1.0,
    "max_value": 2.0,
    "step_count": 3,
    "step_column": "idx",
}
_QUOTE_RESPONSE = {
    "output_mapping": [
        {
            "source_port": "rows",
            "source_column": "premium",
            "output_path": "$[:].quote.premium",
            "enabled": True,
        }
    ]
}
_SWITCH = {"input_scenario_map": {"live_rows": "live", "batch_rows": "batch"}}
_ONLINE_ARTIFACT = {
    "version": "v1",
    "created_at": "2026-02-24T14:00:00Z",
    "mode": "online",
    "lambdas": {"predicted_volume": 0.5},
    "objective": "predicted_income",
    "constraints": {"predicted_volume": {"min": 0.9}},
    "quote_id": "quote_id",
    "scenario_index": "scenario_index",
    "scenario_value": "scenario_value",
}
_MLFLOW_SCORE = {
    "source_type": "run",
    "run_id": "run123",
    "artifact_path": "model.cbm",
    "task": "regression",
    "output_column": "prediction",
}


def _regions() -> pl.LazyFrame:
    return pl.LazyFrame({"region": ["N", "S", "W"], "premium": [120.0, 80.0, 40.0]})


def _scenarios() -> pl.LazyFrame:
    return pl.LazyFrame(
        {
            "quote_id": ["q1", "q1", "q1", "q2", "q2", "q2"],
            "scenario_index": [0, 1, 2, 0, 1, 2],
            "scenario_value": [0.9, 1.0, 1.1, 0.9, 1.0, 1.1],
            "predicted_income": [90.0, 100.0, 110.0, 45.0, 50.0, 55.0],
            "predicted_volume": [1.0, 0.9, 0.7, 1.0, 0.95, 0.8],
        }
    )


def _stub_scoring_model(prediction: float = 0.5) -> ScoringModel:
    """A CatBoost-flavoured model over a mock estimator predicting *prediction* per row."""
    model = MagicMock()
    model.feature_names_ = ["a"]
    model.predict.side_effect = lambda frame: np.full(len(frame), prediction)
    del model.predict_proba
    return ScoringModel(
        model=model, feature_names=["a"], cat_feature_names=frozenset(), flavor="catboost"
    )


def test_every_behavioural_type_has_an_equivalence_case() -> None:
    behavioural = {key for key, spec in NODE_SPECS.items() if spec.behavioural}

    assert behavioural == {
        NodeType.BANDING,
        NodeType.RATING_STEP,
        NodeType.SCENARIO_EXPANDER,
        NodeType.OUTPUT,
        NodeType.WORKBENCH_OUTPUT,
        NodeType.LIVE_SWITCH,
        NodeType.OPTIMISER_APPLY,
        NodeType.MODEL_SCORE,
    }


def test_banding_bands_alike(project: Path) -> None:
    frames = [_regions()]
    canvas = _canvas(NodeType.BANDING, _BANDING, frames, ["rows"])
    standalone = _standalone(project, NodeType.BANDING, _BANDING, frames, ["rows"])

    _assert_equal(canvas, standalone)
    assert _collected(canvas)["band"].to_list() == ["north", "south", None]


def test_a_rating_step_rates_alike(project: Path) -> None:
    frames = [pl.LazyFrame({"band": ["north", "south", "east"]})]
    canvas = _canvas(NodeType.RATING_STEP, _RATING_STEP, frames, ["rows"])
    standalone = _standalone(project, NodeType.RATING_STEP, _RATING_STEP, frames, ["rows"])

    _assert_equal(canvas, standalone)
    assert _collected(canvas)["premium"].to_list() == [200.0, 50.0, 100.0]


def test_an_expander_expands_alike(project: Path) -> None:
    frames = [pl.LazyFrame({"quote_id": [1, 2]})]
    canvas = _canvas(NodeType.SCENARIO_EXPANDER, _EXPANDER, frames, ["rows"])
    standalone = _standalone(project, NodeType.SCENARIO_EXPANDER, _EXPANDER, frames, ["rows"])

    _assert_equal(canvas, standalone)
    assert _collected(canvas).height == 6


def test_a_quote_response_assembles_alike(project: Path) -> None:
    frames = [_regions()]
    canvas = _canvas(NodeType.OUTPUT, _QUOTE_RESPONSE, frames, ["rows"], source_ports=["rows"])
    standalone = _standalone(project, NodeType.OUTPUT, _QUOTE_RESPONSE, frames, ["rows"])

    _assert_equal(canvas, standalone)
    assert "premium" not in _collected(canvas).columns


def test_a_workbench_output_fills_its_tables_alike(project: Path) -> None:
    workbench_project(
        project,
        [
            form_table("policy", "input", ("state", "str")),
            form_table("pricing", "output", ("premium", "float")),
        ],
    )
    # The canvas is given the form's tables as every run's graph is; the saved file's
    # decorator reads them from the same form.
    given = given_config(
        NodeType.WORKBENCH_OUTPUT, {}, project_form_tables(project, owner="Workbench Output")
    )
    frames = [pl.LazyFrame({"premium": [12.5], "other": ["x"]})]
    canvas = _canvas(
        NodeType.WORKBENCH_OUTPUT, given, frames, ["priced"], target_handles=["pricing"]
    )
    standalone = _standalone(
        project, NodeType.WORKBENCH_OUTPUT, {}, frames, ["priced"], target_ports=["pricing"]
    )

    _assert_equal(canvas, standalone)
    # The table's own columns only: the frame's other column is not one of them.
    assert _collected(canvas)["pricing"].to_dicts() == [
        {"quote_reference": None, "quote_option": None, "premium": 12.5}
    ]


@pytest.mark.parametrize("scenario", ["live", "batch"])
def test_a_source_switch_routes_alike(project: Path, scenario: str) -> None:
    frames = [pl.LazyFrame({"source": ["live"]}), pl.LazyFrame({"source": ["batch"]})]
    inputs = ["live_rows", "batch_rows"]
    canvas = _canvas(NodeType.LIVE_SWITCH, _SWITCH, frames, inputs, source=scenario)
    token = _scenario_ctx.set(scenario)
    try:
        standalone = _standalone(project, NodeType.LIVE_SWITCH, _SWITCH, frames, inputs)
    finally:
        _scenario_ctx.reset(token)

    _assert_equal(canvas, standalone)
    assert _collected(canvas).to_dicts() == [{"source": scenario}]


def test_an_apply_optimisation_applies_its_artifact_alike(project: Path) -> None:
    (project / "artifact.json").write_text(json.dumps(_ONLINE_ARTIFACT), encoding="utf-8")
    config = {"source_type": "file", "artifact_path": "artifact.json"}
    frames = [_scenarios()]
    canvas = _canvas(NodeType.OPTIMISER_APPLY, config, frames, ["scored"])
    standalone = _standalone(project, NodeType.OPTIMISER_APPLY, config, frames, ["scored"])

    _assert_equal(canvas, standalone)
    applied = _collected(canvas)
    assert applied.height == 2, "the artifact chooses one scenario per quote"
    assert "__optimiser_version__" in applied.columns


def test_a_model_scoring_scores_alike(project: Path) -> None:
    frames = [pl.LazyFrame({"a": [1.0, 2.0]})]
    with patch("haute._mlflow_io.load_mlflow_model", return_value=_stub_scoring_model()):
        canvas = _collected(_canvas(NodeType.MODEL_SCORE, _MLFLOW_SCORE, frames, ["features"]))
        standalone = _collected(
            _standalone(project, NodeType.MODEL_SCORE, _MLFLOW_SCORE, frames, ["features"])
        )

    _assert_equal(canvas, standalone)
    assert canvas.to_dicts() == [{"a": 1.0, "prediction": 0.5}, {"a": 2.0, "prediction": 0.5}]


# ---------------------------------------------------------------------------
# Model Scoring: each caller's own rules
# ---------------------------------------------------------------------------


def test_a_model_scoring_with_no_source_passes_through_on_the_canvas_and_is_refused_standalone(
    project: Path,
) -> None:
    config = {"output_column": "prediction"}
    frame = pl.LazyFrame({"a": [1.0]})

    assert_frame_equal(
        _collected(_canvas(NodeType.MODEL_SCORE, config, [frame], ["features"])),
        frame.collect(),
    )
    with pytest.raises(ConfigError, match="modelScore node has no model source"):
        _standalone(project, NodeType.MODEL_SCORE, config, [frame], ["features"])


def test_a_model_scoring_with_an_incomplete_chosen_source_is_refused_alike(
    project: Path,
) -> None:
    config = {"source_type": "run", "output_column": "prediction"}
    frame = pl.LazyFrame({"a": [1.0]})

    with pytest.raises(IncompleteModelSourceError) as canvas:
        _canvas(NodeType.MODEL_SCORE, config, [frame], ["features"])
    with pytest.raises(IncompleteModelSourceError) as standalone:
        _standalone(project, NodeType.MODEL_SCORE, config, [frame], ["features"])

    assert str(canvas.value) == str(standalone.value)
    assert "run_id" in str(canvas.value)


def test_a_model_scorings_incomplete_steps_are_reported_before_its_source_is_read() -> None:
    # The source is incomplete too: reading it first would report that instead.
    config = {
        "source_type": "run",
        "output_column": "prediction",
        "steps": [{"id": "f", "kind": "filter", "match": "all", "conditions": []}],
    }
    loaded = MagicMock(side_effect=AssertionError("the model was loaded"))

    with patch("haute._mlflow_io.load_mlflow_model", loaded):
        node = GraphNode(
            id=NAME, data=NodeData(label=NAME, nodeType=NodeType.MODEL_SCORE, config=config)
        )
        _name, fn, _is_source = _build_node_fn(node, source_names=["features"])
        with pytest.raises(NotImplementedError, match=INCOMPLETE_STEPS_MESSAGE.split(".")[0]):
            fn(pl.LazyFrame({"a": [1.0]}))

    loaded.assert_not_called()


def test_a_model_scorings_hook_runs_once_after_the_scoring(project: Path) -> None:
    calls: list[int] = []
    hook = _function(
        project,
        f"def {NAME}(df):\n"
        "    calls.append(1)\n"
        '    return df.with_columns(pl.col("prediction") + 1)\n',
    )
    hook.__globals__["calls"] = calls
    frame = pl.LazyFrame({"a": [1.0]})

    with patch("haute._mlflow_io.load_mlflow_model", return_value=_stub_scoring_model()):
        result = _collected(
            _standalone(
                project, NodeType.MODEL_SCORE, _MLFLOW_SCORE, [frame], ["df"], kind="hook", fn=hook
            )
        )

    # Applied twice, the hook would add 2 to the model's 0.5.
    assert result.to_dicts() == [{"a": 1.0, "prediction": 1.5}]
    assert calls == [1]


# ---------------------------------------------------------------------------
# Apply Optimisation: each caller's own rules
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "config",
    [
        pytest.param({}, id="no-source"),
        pytest.param({"source_type": ""}, id="blank-source"),
        pytest.param({"source_type": "file"}, id="file-without-artifact"),
        pytest.param({"source_type": "run"}, id="run-without-id"),
        pytest.param({"source_type": "registered"}, id="registered-without-model"),
    ],
)
def test_an_apply_optimisation_without_a_complete_source_passes_its_first_frame_through(
    project: Path, config: dict[str, Any]
) -> None:
    first, second = pl.LazyFrame({"first": [1]}), pl.LazyFrame({"second": [2]})
    inputs = ["scored", "ratebook"]

    canvas = _canvas(NodeType.OPTIMISER_APPLY, config, [first, second], inputs)
    standalone = _standalone(project, NodeType.OPTIMISER_APPLY, config, [first, second], inputs)

    assert_frame_equal(_collected(canvas), first.collect())
    assert_frame_equal(_collected(standalone), first.collect())


def test_an_apply_optimisation_naming_an_artifact_without_a_source_type_is_refused_alike(
    project: Path,
) -> None:
    config = {"artifact_path": "artifact.json"}
    frames = [_scenarios()]
    message = "optimiserApply node with artifact_path requires source_type='file'"

    with pytest.raises(ConfigError, match=message):
        _canvas(NodeType.OPTIMISER_APPLY, config, frames, ["scored"])
    # A save refuses this config, so the sidecar is written as a hand edit would leave it.
    sidecar = write_node_config(project, NodeType.OPTIMISER_APPLY, NAME, config)
    with pytest.raises(ConfigError, match=message):
        _run_standalone(project, NodeType.OPTIMISER_APPLY, sidecar, frames, ["scored"])


def test_an_mlflow_apply_optimisations_artifact_path_is_ignored_at_runtime_refused_by_bundler(
    project: Path,
) -> None:
    """Today's recorded disagreement: the runtime downloads the run's fixed artifact."""
    config = {"source_type": "run", "run_id": "run-1", "artifact_path": "custom/other.json"}
    (project / "downloaded.json").write_text(json.dumps(_ONLINE_ARTIFACT), encoding="utf-8")
    backend = MagicMock(identity="local:x", tracking_uri="file:///x", registry_uri="file:///x")
    frames = [_scenarios()]

    with (
        patch(
            "mlflow.artifacts.download_artifacts", return_value=str(project / "downloaded.json")
        ) as download,
        patch("mlflow.tracking.MlflowClient"),
        patch("haute._mlflow_utils.resolve_backend", return_value=backend),
    ):
        canvas = _collected(_canvas(NodeType.OPTIMISER_APPLY, config, frames, ["scored"]))
        _load_mlflow_cached.cache_clear()
        standalone = _collected(
            _standalone(project, NodeType.OPTIMISER_APPLY, config, frames, ["scored"])
        )

    _assert_equal(canvas, standalone)
    assert canvas.height == 2
    assert [call.args[0] for call in download.call_args_list] == [
        "runs:/run-1/optimiser_result.json",
        "runs:/run-1/optimiser_result.json",
    ]
    node = GraphNode(
        id=NAME, data=NodeData(label=NAME, nodeType=NodeType.OPTIMISER_APPLY, config=config)
    )
    with pytest.raises(ValueError, match="with artifact_path must set source_type='file'"):
        collect_artifacts(PipelineGraph(nodes=[node], edges=[]), [], project)


# ---------------------------------------------------------------------------
# Data Input: the builder and the standalone read anchor a relative path alike
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("pipeline", "data"),
    [
        pytest.param("main.py", "quotes.parquet", id="pipeline-at-the-root"),
        pytest.param("rating/main.py", "rating/quotes.parquet", id="beside-a-nested-pipeline"),
        pytest.param("rating/main.py", "quotes.parquet", id="at-the-root-of-a-nested-pipeline"),
    ],
)
def test_a_data_input_anchors_a_relative_path_alike(
    project: Path, pipeline: str, data: str
) -> None:
    """The builder resolves through ``resolve_data_input`` from the project's pipeline
    directory, a saved file's decorator through ``resolve_data_input_from_config`` from the
    file's own: with the file in the project's pipeline directory both read one file."""
    (project / "haute.toml").write_text(
        f'[project]\nname = "equivalence"\npipeline = "{pipeline}"\n', encoding="utf-8"
    )
    use_project(project)
    pipeline_dir = (project / pipeline).parent
    pipeline_dir.mkdir(parents=True, exist_ok=True)
    (project / data).parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame({"quote_id": [1, 2]}).write_parquet(project / data)
    config = {"input_type": "file", "format": "parquet", "path": "quotes.parquet"}

    canvas = _canvas(NodeType.DATA_INPUT, config, [], [])
    standalone = _standalone(pipeline_dir, NodeType.DATA_INPUT, config, [], [])

    _assert_equal(canvas, standalone)
    assert _collected(canvas)["quote_id"].to_list() == [1, 2]
