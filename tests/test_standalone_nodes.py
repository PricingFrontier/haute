"""The standalone runtime of configured nodes (``haute._standalone_nodes``).

A saved pipeline file declares each configured node, and its decorator performs
the node's work when the file runs on its own. These tests execute hand-written
files from ``tmp_path`` so every ``config=`` path resolves as a user's does.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import numpy as np
import polars as pl
import pytest

import haute
import haute._standalone_nodes as standalone_nodes
from haute._mlflow_io import ScoringModel
from haute._sandbox import _get_project_root, set_project_root
from haute._standalone_nodes import has_empty_body, run_configured_node
from haute._types import NodeType
from haute.errors import ConfigError, ExecutionError
from tests.conftest import write_node_config


def _run_file(directory: Path, source: str, name: str = "main.py") -> dict[str, Any]:
    """Execute *source* as the pipeline file *name* in *directory*; return its namespace."""
    path = directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    namespace: dict[str, Any] = {"__file__": str(path), "__name__": "pipeline_under_test"}
    exec(compile(source, str(path), "exec"), namespace)
    return namespace


def _collect(frame: pl.LazyFrame | pl.DataFrame) -> pl.DataFrame:
    return frame.lazy().collect()


def _constant(directory: Path, name: str, **values: object) -> str:
    """Write a Constant sidecar holding *values*; return its ``config=`` path."""
    return write_node_config(
        directory,
        NodeType.CONSTANT,
        name,
        {"values": [{"name": key, "value": value} for key, value in values.items()]},
    )


@pytest.fixture()
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A Haute project root (``haute.toml`` in a git checkout) that the run treats as its own."""
    (tmp_path / ".git").mkdir()
    (tmp_path / "haute.toml").write_text('[project]\nname = "standalone"\n', encoding="utf-8")
    original = _get_project_root()
    monkeypatch.chdir(tmp_path)
    set_project_root(tmp_path)
    try:
        yield tmp_path
    finally:
        set_project_root(original)


# ---------------------------------------------------------------------------
# Declarations: the decorator performs the node's work
# ---------------------------------------------------------------------------


def test_a_sidecar_type_without_config_is_called_as_a_plain_function() -> None:
    pipeline = haute.Pipeline("plain")

    @pipeline.polars
    def rows() -> pl.LazyFrame:
        return pl.LazyFrame({"x": [1, 2]})

    @pipeline.banding
    def band(rows: pl.LazyFrame) -> pl.LazyFrame:
        return rows.with_columns(band=pl.col("x") * 10)

    pipeline.connect("rows", "band")

    assert pipeline.nodes[1].kind == "transform"
    assert _collect(pipeline.run())["band"].to_list() == [10, 20]


def test_a_data_input_declaration_loads_its_sidecar_source(project: Path) -> None:
    pl.DataFrame({"quote_id": [1, 2]}).write_parquet(project / "quotes.parquet")
    config = write_node_config(
        project,
        NodeType.DATA_INPUT,
        "quotes",
        {"inputType": "file", "format": "parquet", "path": "quotes.parquet"},
    )
    namespace = _run_file(
        project,
        "import haute\n\n"
        'pipeline = haute.Pipeline("p")\n\n\n'
        f'@pipeline.data_input(config="{config}")\n'
        "def quotes(): ...\n",
    )

    assert _collect(namespace["pipeline"].run()).to_dicts() == [{"quote_id": 1}, {"quote_id": 2}]


def test_a_constant_declaration_reads_its_sidecar_on_every_run(tmp_path: Path) -> None:
    """A sidecar edit changes the next run without re-importing the file."""
    config = _constant(tmp_path, "rates", base="100")
    namespace = _run_file(
        tmp_path,
        "import haute\n\n"
        'pipeline = haute.Pipeline("p")\n\n\n'
        f'@pipeline.constant(config="{config}")\n'
        "def rates(): ...\n",
    )
    assert _collect(namespace["pipeline"].run()).to_dicts() == [{"base": 100.0}]

    _constant(tmp_path, "rates", base="120")

    assert _collect(namespace["pipeline"].run()).to_dicts() == [{"base": 120.0}]


def test_a_live_switch_declaration_selects_the_active_scenarios_input(tmp_path: Path) -> None:
    live = _constant(tmp_path, "live_rows", source="live")
    batch = _constant(tmp_path, "batch_rows", source="batch")
    switch = write_node_config(
        tmp_path,
        NodeType.LIVE_SWITCH,
        "switch",
        {"input_scenario_map": {"live_rows": "live", "batch_rows": "batch"}},
    )
    namespace = _run_file(
        tmp_path,
        "import haute\n\n"
        'pipeline = haute.Pipeline("p")\n\n\n'
        f'@pipeline.constant(config="{live}")\n'
        "def live_rows(): ...\n\n\n"
        f'@pipeline.constant(config="{batch}")\n'
        "def batch_rows(): ...\n\n\n"
        f'@pipeline.live_switch(config="{switch}")\n'
        "def switch(live_rows, batch_rows): ...\n\n\n"
        'pipeline.connect("live_rows", "switch")\n'
        'pipeline.connect("batch_rows", "switch")\n',
    )

    # run() executes the batch scenario.
    assert _collect(namespace["pipeline"].run()).to_dicts() == [{"source": "batch"}]


def test_an_edge_join_declaration_joins_its_base_and_join_inputs(tmp_path: Path) -> None:
    namespace = _run_file(
        tmp_path,
        "import haute\n"
        "import polars as pl\n\n"
        'pipeline = haute.Pipeline("p")\n\n\n'
        "@pipeline.polars\n"
        "def quotes() -> pl.LazyFrame:\n"
        '    return pl.LazyFrame({"region": ["N", "S"]})\n\n\n'
        "@pipeline.polars\n"
        "def factors() -> pl.LazyFrame:\n"
        '    return pl.LazyFrame({"region": ["N"], "factor": [1.5]})\n\n\n'
        '@pipeline.edge_join(how="left", on=["region"])\n'
        "def priced(quotes, factors): ...\n\n\n"
        'pipeline.connect("quotes", "priced", target_port="base")\n'
        'pipeline.connect("factors", "priced", target_port="join")\n',
    )

    assert _collect(namespace["pipeline"].run()).to_dicts() == [
        {"region": "N", "factor": 1.5},
        {"region": "S", "factor": None},
    ]


def test_an_output_declaration_assembles_its_document(tmp_path: Path) -> None:
    rows = _constant(tmp_path, "rows", premium="120")
    output = write_node_config(
        tmp_path,
        NodeType.OUTPUT,
        "response",
        {
            "outputMapping": [
                {
                    "source_port": "rows",
                    "source_column": "premium",
                    "output_path": "$[:].quote.premium",
                    "enabled": True,
                }
            ]
        },
    )
    namespace = _run_file(
        tmp_path,
        "import haute\n\n"
        'pipeline = haute.Pipeline("p")\n\n\n'
        f'@pipeline.constant(config="{rows}")\n'
        "def rows(): ...\n\n\n"
        f'@pipeline.output(config="{output}")\n'
        "def response(rows): ...\n\n\n"
        'pipeline.connect("rows", "response")\n',
    )

    document = _collect(namespace["pipeline"].run())

    assert "premium" not in document.columns, "an Output assembles, it does not pass through"
    assert document.to_dicts() == [{"quote": {"premium": 120.0}}]


def _stub_scoring_model() -> ScoringModel:
    """A CatBoost-flavoured model over a mock estimator predicting 0.5 per row."""
    model = MagicMock()
    model.feature_names_ = ["a"]
    model.predict.side_effect = lambda frame: np.full(len(frame), 0.5)
    del model.predict_proba
    return ScoringModel(
        model=model, feature_names=["a"], cat_feature_names=frozenset(), flavor="catboost"
    )


def test_a_model_score_declaration_appends_its_predictions(tmp_path: Path) -> None:
    features = _constant(tmp_path, "features", a="1.0")
    score = write_node_config(
        tmp_path,
        NodeType.MODEL_SCORE,
        "score",
        {
            "sourceType": "run",
            "run_id": "run123",
            "artifact_path": "model.cbm",
            "task": "regression",
            "output_column": "prediction",
        },
    )
    namespace = _run_file(
        tmp_path,
        "import haute\n\n"
        'pipeline = haute.Pipeline("p")\n\n\n'
        f'@pipeline.constant(config="{features}")\n'
        "def features(): ...\n\n\n"
        f'@pipeline.model_score(config="{score}")\n'
        "def score(features): ...\n\n\n"
        'pipeline.connect("features", "score")\n',
    )

    with patch("haute._mlflow_io.load_mlflow_model", return_value=_stub_scoring_model()):
        scored = _collect(namespace["pipeline"].run())

    assert scored.to_dicts() == [{"a": 1.0, "prediction": 0.5}]


# ---------------------------------------------------------------------------
# Hooks: the decorator hands its work to the user's code
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "node_type",
    [NodeType.MODEL_SCORE, NodeType.RATING_STEP, NodeType.SCENARIO_EXPANDER, NodeType.EXPLORE],
)
def test_a_hook_receives_the_nodes_work_as_df_then_its_other_inputs(
    node_type: NodeType, monkeypatch: pytest.MonkeyPatch
) -> None:
    produced = pl.LazyFrame({"produced": [1]})
    first, second = pl.LazyFrame({"a": [1]}), pl.LazyFrame({"b": [2]})
    worked_on: list[tuple[Any, ...]] = []

    def work(node_type: NodeType, **kwargs: Any) -> pl.LazyFrame:
        worked_on.append(tuple(kwargs["frames"]))
        return produced

    monkeypatch.setattr(standalone_nodes, "_run_configured_work", work)
    received: dict[str, pl.LazyFrame] = {}

    def hook(df: pl.LazyFrame, other: pl.LazyFrame) -> pl.LazyFrame:
        received.update(df=df, other=other)
        return df

    result = run_configured_node(
        node_type,
        "hook",
        name="node",
        config={"config": "config/node.json"},
        fn=hook,
        frames=(first, second),
    )

    assert worked_on == [(first, second)]
    assert received["df"] is produced
    assert received["other"] is second
    assert result is produced


def test_a_data_input_hook_runs_on_the_loaded_rows(project: Path) -> None:
    pl.DataFrame({"quote_id": [1, 2, 3]}).write_parquet(project / "quotes.parquet")
    config = write_node_config(
        project,
        NodeType.DATA_INPUT,
        "quotes",
        {"inputType": "file", "format": "parquet", "path": "quotes.parquet"},
    )
    namespace = _run_file(
        project,
        "import haute\n"
        "import polars as pl\n\n"
        'pipeline = haute.Pipeline("p")\n\n\n'
        f'@pipeline.data_input(config="{config}")\n'
        "def quotes(df: pl.LazyFrame) -> pl.LazyFrame:\n"
        '    df = df.filter(pl.col("quote_id") > 1)\n'
        "    return df\n",
    )

    assert _collect(namespace["pipeline"].run())["quote_id"].to_list() == [2, 3]


def test_an_external_file_hook_receives_its_inputs_and_the_loaded_object(project: Path) -> None:
    (project / "factor.json").write_text('{"factor": 3}', encoding="utf-8")
    config = write_node_config(
        project, NodeType.EXTERNAL_FILE, "lookup", {"path": "factor.json", "fileType": "json"}
    )
    namespace = _run_file(
        project,
        "import haute\n"
        "import polars as pl\n\n"
        'pipeline = haute.Pipeline("p")\n\n\n'
        "@pipeline.polars\n"
        "def rows() -> pl.LazyFrame:\n"
        '    return pl.LazyFrame({"value": [2]})\n\n\n'
        f'@pipeline.external_file(config="{config}")\n'
        "def lookup(rows: pl.LazyFrame, *, obj) -> pl.LazyFrame:\n"
        "    df = rows\n"
        '    df = df.with_columns(scaled=pl.col("value") * obj["factor"])\n'
        "    return df\n\n\n"
        'pipeline.connect("rows", "lookup")\n',
    )

    assert _collect(namespace["pipeline"].run())["scaled"].to_list() == [6]


def test_a_hook_that_returns_nothing_fails_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        standalone_nodes, "_run_configured_work", lambda node_type, **kwargs: pl.LazyFrame()
    )

    def forgot_to_return(df: pl.LazyFrame) -> None:
        df.head(1)

    with pytest.raises(ExecutionError, match="must return its frame"):
        run_configured_node(
            NodeType.RATING_STEP,
            "hook",
            name="rate",
            config={"config": "config/rating_step/rate.json"},
            fn=forgot_to_return,
            frames=(pl.LazyFrame({"a": [1]}),),
        )


# ---------------------------------------------------------------------------
# Function shapes: what registration accepts
# ---------------------------------------------------------------------------


def _ellipsis(): ...


def _pass():
    pass


def _docstring():
    """Only a description."""


def _docstring_then_ellipsis():
    """A description."""
    ...


@pytest.mark.parametrize("fn", [_ellipsis, _pass, _docstring, _docstring_then_ellipsis])
def test_a_body_that_does_nothing_is_empty(fn: Any) -> None:
    assert has_empty_body(fn)


def _returns_its_input(df):
    return df


def _assigns(df):
    df = df.head(1)
    return df


def _calls():
    print("side effect")


@pytest.mark.parametrize("fn", [_returns_its_input, _assigns, _calls])
def test_a_body_that_does_anything_is_not_empty(fn: Any) -> None:
    assert not has_empty_body(fn)


def test_a_declaration_on_a_code_type_rejects_a_body() -> None:
    pipeline = haute.Pipeline("shapes")

    with pytest.raises(ConfigError, match="has a function body.*make the first parameter df"):

        @pipeline.data_input(config="config/data_input/quotes.json")
        def quotes() -> pl.LazyFrame:
            return pl.LazyFrame({"a": [1]})


def test_a_hook_without_code_is_rejected() -> None:
    pipeline = haute.Pipeline("shapes")

    with pytest.raises(ConfigError, match="takes df but has no code"):

        @pipeline.rating_step(config="config/rating_step/rate.json")
        def rate(df): ...


def test_a_code_less_type_rejects_any_body_and_reads_df_as_an_input() -> None:
    pipeline = haute.Pipeline("shapes")

    with pytest.raises(ConfigError, match=r"Its node type \(banding\) carries no code"):

        @pipeline.banding(config="config/banding/band.json")
        def band(df):
            return df

    @pipeline.banding(config="config/banding/banded.json")
    def banded(df): ...

    assert pipeline.nodes[-1].kind == "declaration"
    assert pipeline.nodes[-1].input_arity.min_inputs == 1


def test_a_configured_node_defined_outside_a_file_fails_when_it_needs_its_sidecar() -> None:
    namespace: dict[str, Any] = {"__name__": "no_file"}
    source = (
        "import haute\n\n"
        'pipeline = haute.Pipeline("p")\n\n\n'
        '@pipeline.constant(config="config/constant/rates.json")\n'
        "def rates(): ...\n"
    )
    exec(compile(source, "<string>", "exec"), namespace)

    with pytest.raises(ConfigError, match="not defined in a file"):
        namespace["pipeline"].run()


# ---------------------------------------------------------------------------
# What the decorator returns
# ---------------------------------------------------------------------------


def test_calling_a_configured_node_runs_it_while_a_transform_stays_as_written(
    tmp_path: Path,
) -> None:
    config = _constant(tmp_path, "rates", base="100")
    namespace = _run_file(
        tmp_path,
        "import haute\n"
        "import polars as pl\n\n"
        'pipeline = haute.Pipeline("p")\n\n\n'
        f'@pipeline.constant(config="{config}")\n'
        "def rates(): ...\n\n\n"
        "@pipeline.polars\n"
        "def doubled(rates: pl.LazyFrame) -> pl.LazyFrame:\n"
        '    df = rates.with_columns(pl.col("base") * 2)\n'
        "    return df\n\n\n"
        'pipeline.connect("rates", "doubled")\n',
    )
    rates_node, doubled_node = namespace["pipeline"].nodes

    rates = namespace["rates"]()

    assert _collect(rates).to_dicts() == [{"base": 100.0}]
    assert namespace["rates"].__wrapped__ is rates_node.fn
    assert namespace["doubled"] is doubled_node.fn
    assert _collect(namespace["doubled"](rates)).to_dicts() == [{"base": 200.0}]


def test_calling_a_source_with_a_frame_fails_loudly(tmp_path: Path) -> None:
    config = _constant(tmp_path, "rates", base="100")
    namespace = _run_file(
        tmp_path,
        "import haute\n\n"
        'pipeline = haute.Pipeline("p")\n\n\n'
        f'@pipeline.constant(config="{config}")\n'
        "def rates(): ...\n",
    )

    with pytest.raises(ExecutionError, match="is a source and takes no input frames"):
        namespace["rates"](pl.LazyFrame({"base": [1]}))


def test_calling_an_instance_fails_as_a_run_does() -> None:
    pipeline = haute.Pipeline("instances")

    @pipeline.polars
    def original(x: pl.LazyFrame) -> pl.LazyFrame:
        df = x
        return df

    @pipeline.instance(of="original", inputMapping={"x": "y"})
    def copy(y): ...

    with pytest.raises(ExecutionError, match="instanceOf"):
        copy(pl.LazyFrame({"a": [1]}))


# ---------------------------------------------------------------------------
# Submodel definitions: sidecars belong to the owning pipeline
# ---------------------------------------------------------------------------


def test_a_submodel_definition_reads_its_sidecars_from_the_owning_pipeline(
    tmp_path: Path,
) -> None:
    config = _constant(tmp_path, "rates", base="100")
    namespace = _run_file(
        tmp_path,
        "import haute\n\n"
        "submodel = haute.Submodel(\n"
        '    "rates",\n'
        '    definition_id="rates",\n'
        "    input_ports=[],\n"
        '    output_ports=[{"name": "rates", "source": {"nodeId": "rates"}}],\n'
        '    pipeline_dir="..",\n'
        ")\n\n\n"
        f'@submodel.constant(config="{config}")\n'
        "def rates(): ...\n",
        name="modules/rates.py",
    )

    assert namespace["submodel"].pipeline_dir == ".."
    assert _collect(namespace["rates"]()).to_dicts() == [{"base": 100.0}]


@pytest.mark.parametrize("value", ["", "modules", "../config", "./..", "/"])
def test_a_submodel_pipeline_dir_only_climbs_folders(value: str) -> None:
    with pytest.raises(ValueError, match="pipeline_dir"):
        haute.Submodel("s", definition_id="s", input_ports=[], output_ports=[], pipeline_dir=value)
