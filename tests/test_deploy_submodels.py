"""A deployed pipeline runs its submodels (BUG-18).

Deploy resolves the graph that runs, each submodel occurrence expanded into its own
nodes, as ``haute run`` and the editor execute it. Before, deploy kept the occurrence
as a placeholder whose builder passed its input through, so the served pipeline
skipped the submodel and answered without it, with no error.

Each test builds a project from the ``minimal_live_quote`` example, deploys it, and
scores through the bundle the deploy writes, as the served container does.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import polars as pl
import pytest

from haute._sandbox import bound_project_root
from haute._types import NodeType, PipelineGraph
from haute.deploy import DeployConfig, resolve_config
from haute.deploy._config import ResolvedDeploy
from haute.deploy._container import write_bundle
from haute.deploy._mlflow import _stage_artifacts
from haute.deploy._pruner import find_deploy_input_nodes, find_output_node, prune_for_deploy
from haute.deploy._scorer import score_graph
from haute.errors import ExecutionError
from haute.parser import parse_pipeline_file

_EXAMPLE = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "haute"
    / "assistant"
    / "assets"
    / "examples"
    / "minimal_live_quote"
)

_CONTAINER_DEPLOY = (
    '\n[deploy]\ntarget = "container"\n\n[deploy.container]\nbase_image = "python:3.11.9-slim"\n'
)

_PIPELINE_HEAD = '''\
"""A live quote priced through submodels."""

import polars as pl

import haute

pipeline = haute.Pipeline("submodel_quote", description="Prices the quote through submodels.")


@pipeline.api_input(config="config/request.json")
def quote(): ...


'''

_FACTORS = """\
import haute
import polars as pl

submodel = haute.Submodel(
    "factors",
    definition_id="definition_factors",
    input_ports=[{"name": "rows", "targets": [{"nodeId": "scaled", "handleId": None}]}],
    output_ports=[{"name": "factored", "source": {"nodeId": "factor", "handleId": None}}],
    pipeline_dir="..",
)


@submodel.polars
def scaled(rows: pl.LazyFrame) -> pl.LazyFrame:
    return rows.with_columns(fixture_value=pl.col("fixture_value") * 3)


@submodel.polars
def factor(scaled: pl.LazyFrame) -> pl.LazyFrame:
    return scaled.with_columns(factor=pl.lit(3))


submodel.connect("scaled", "factor")
"""

# A definition whose node ``rates`` reads a rates file. Node names are unique across a
# pipeline and its submodels, so two artefacts share a file name only as two
# occurrences of one definition, whose keys differ only in the occurrence.
_RATES = """\
import haute
import polars as pl

submodel = haute.Submodel(
    "rated",
    definition_id="definition_rated",
    input_ports=[{"name": "rows", "targets": [{"nodeId": "rates", "handleId": None}]}],
    output_ports=[{"name": "rated", "source": {"nodeId": "rates", "handleId": None}}],
    pipeline_dir="..",
)


@submodel.external_file(config="config/external_file/rates.json")
def rates(rows: pl.LazyFrame, *, obj) -> pl.LazyFrame:
    df = rows
    df = df.with_columns(fixture_value=pl.col("fixture_value") * obj["factor"])
    return df
"""


def _project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    body: str,
    modules: dict[str, str],
    output: dict[str, list[str]],
    files: dict[str, str] | None = None,
) -> Path:
    """A deployable project: the example's quote input, *body*, *modules*, a response
    mapping each of *output*'s ports' columns to a field, and any other *files*."""
    project = tmp_path / "project"
    monkeypatch.chdir(tmp_path)
    shutil.copytree(_EXAMPLE, project)
    (project / "pipeline.py").write_text(_PIPELINE_HEAD + body, encoding="utf-8")
    (project / "modules").mkdir()
    for name, source in modules.items():
        (project / "modules" / f"{name}.py").write_text(source, encoding="utf-8")
    mapping = [
        {"source_port": port, "source_column": column, "output_path": f"$[:].{column}"}
        for port, columns in output.items()
        for column in columns
    ]
    (project / "config" / "output.json").write_text(
        json.dumps({"outputMapping": [{**m, "enabled": True} for m in mapping]}),
        encoding="utf-8",
    )
    toml_path = project / "haute.toml"
    toml_path.write_text(
        toml_path.read_text(encoding="utf-8") + _CONTAINER_DEPLOY, encoding="utf-8"
    )
    for relative, text in (files or {}).items():
        path = project / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return project


def _resolve(project: Path) -> ResolvedDeploy:
    return resolve_config(DeployConfig.from_toml(project / "haute.toml"))


def _score_bundle(resolved: ResolvedDeploy, build: Path, quotes: list[dict]) -> list[dict]:
    """Score *quotes* as the served container does: from the bundle's manifest and files,
    in the bundle's directory."""
    manifest = json.loads(write_bundle(resolved, build).read_text(encoding="utf-8"))
    with bound_project_root(build):
        scored = score_graph(
            graph=PipelineGraph.model_validate(manifest["pruned_graph"]),
            input_df=pl.DataFrame(quotes),
            input_node_ids=manifest["input_node_ids"],
            output_node_id=manifest["output_node_id"],
            artifact_paths={
                name: str(build / path) for name, path in manifest["artifacts"].items()
            },
        )
    return scored.to_dicts()


_ONE_SUBMODEL = """\
@pipeline.output(config="config/output.json")
def response(factored): ...


pipeline.submodel("modules/factors.py", "factors")
pipeline.connect("quote", "factors", source_port="quote", target_port="rows")
pipeline.connect("factors", "response", source_port="factored")
"""


def test_a_served_pipeline_answers_with_its_submodels_columns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(
        tmp_path,
        monkeypatch,
        _ONE_SUBMODEL,
        {"factors": _FACTORS},
        {"factored": ["fixture_value", "factor"]},
    )

    resolved = _resolve(project)
    answers = _score_bundle(resolved, tmp_path / "build", [{"quote_id": "q1", "fixture_value": 10}])

    # The submodel's nodes ran: the value is scaled, and its own column answers.
    assert answers == [{"fixture_value": 30, "factor": 3}]
    assert resolved.output_schema == {"fixture_value": "Int64", "factor": "Int32"}
    assert {node.id for node in resolved.pruned_graph.nodes} == {
        "quote",
        "response",
        "submodel_runtime/factors/scaled",
        "submodel_runtime/factors/factor",
    }


def test_artifacts_inside_submodels_bundle_under_flat_distinct_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(
        tmp_path,
        monkeypatch,
        """\
@pipeline.output(config="config/output.json")
def response(rated): ...


pipeline.submodel("modules/rated.py", "first")
pipeline.submodel("modules/rated.py", "second", instance_of="first")
pipeline.connect("quote", "first", source_port="quote", target_port="rows")
pipeline.connect("first", "second", source_port="rated", target_port="rows")
pipeline.connect("second", "response", source_port="rated")
""",
        {"rated": _RATES},
        {"rated": ["fixture_value"]},
        files={
            "data/rates.json": json.dumps({"factor": 2}),
            "config/external_file/rates.json": json.dumps(
                {"path": "data/rates.json", "fileType": "json"}
            ),
        },
    )

    # The Load File reads inside the project, as `haute deploy` run there reads it.
    with bound_project_root(project):
        resolved = _resolve(project)
    answers = _score_bundle(resolved, tmp_path / "build", [{"quote_id": "q1", "fixture_value": 10}])
    (tmp_path / "staged").mkdir()
    staged = _stage_artifacts(resolved, tmp_path / "staged")

    # Each occurrence's node ran with its rates: 10 x 2 x 2.
    assert answers == [{"fixture_value": 40}]
    keys = {"submodel_runtime/first/rates__rates.json", "submodel_runtime/second/rates__rates.json"}
    assert set(resolved.artifacts) == keys
    for folder in (tmp_path / "build" / "artifacts", tmp_path / "staged"):
        files = sorted(folder.iterdir())
        # Flat, and one file per key even where the file system folds case: MLflow
        # copies a model's artefacts into one folder by file name.
        assert all(path.is_file() for path in files)
        assert len({path.name.casefold() for path in files}) == len(keys)
    for key in keys:
        assert staged[key].parent == tmp_path / "staged"
        assert staged[key].read_bytes() == resolved.artifacts[key].read_bytes()


def test_a_response_inside_a_submodel_is_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    priced = """\
import haute
import polars as pl

submodel = haute.Submodel(
    "priced",
    definition_id="definition_priced",
    input_ports=[{"name": "rows", "targets": [{"nodeId": "scaled", "handleId": None}]}],
    output_ports=[],
    pipeline_dir="..",
)


@submodel.polars
def scaled(rows: pl.LazyFrame) -> pl.LazyFrame:
    return rows.with_columns(fixture_value=pl.col("fixture_value") * 3)


@submodel.output(config="config/output.json")
def response(scaled): ...


submodel.connect("scaled", "response")
"""
    project = _project(
        tmp_path,
        monkeypatch,
        """\
pipeline.submodel("modules/priced.py", "priced")
pipeline.connect("quote", "priced", source_port="quote", target_port="rows")
""",
        {"priced": priced},
        {"scaled": ["fixture_value"]},
    )

    resolved = _resolve(project)

    assert resolved.output_node_id == "submodel_runtime/priced/response"
    answers = _score_bundle(resolved, tmp_path / "build", [{"quote_id": "q1", "fixture_value": 10}])
    assert answers == [{"fixture_value": 30}]


def test_the_deploy_scorer_refuses_a_graph_that_still_holds_an_occurrence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The placeholder names itself instead of passing its input through."""
    project = _project(
        tmp_path,
        monkeypatch,
        _ONE_SUBMODEL,
        {"factors": _FACTORS},
        {"factored": ["fixture_value", "factor"]},
    )
    unexpanded = parse_pipeline_file(project / "pipeline.py")
    assert any(node.data.nodeType == NodeType.SUBMODEL for node in unexpanded.nodes)
    output_node_id = find_output_node(unexpanded)
    pruned, _kept, _removed = prune_for_deploy(unexpanded, output_node_id)

    with pytest.raises(ExecutionError, match="Submodel 'factors' reached execution"):
        score_graph(
            graph=pruned,
            input_df=pl.DataFrame([{"quote_id": "q1", "fixture_value": 10}]),
            input_node_ids=find_deploy_input_nodes(pruned),
            output_node_id=output_node_id,
        )
