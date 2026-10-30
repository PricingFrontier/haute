"""SUB-R01: a type moves to one-node reuse only after this cross-path proof."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import polars as pl
import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient

from haute._config_io import collect_node_configs
from haute._flatten import flatten_executable_graph
from haute._pipeline_recovery import load_pipeline_editor_document
from haute._project import get_project_root
from haute._sandbox import bound_project_root
from haute._submodel_instances import qualified_runtime_node_id
from haute._types import (
    SINK_ONLY_NODE_TYPES,
    GraphEdge,
    GraphNode,
    NodeData,
    NodeType,
    PipelineGraph,
)
from haute.cli import cli
from haute.codegen import graph_to_code_multi
from haute.deploy import DeployConfig, resolve_config
from haute.errors import ParseError
from haute.executor import execute_graph
from haute.parser import parse_pipeline_file
from haute.routes._helpers import invalidate_pipeline_index, parse_pipeline_to_graph, pipeline_dir
from haute.routes._save_pipeline import SavePipelineService
from haute.routes._submodel_ops import create_node_instance_graph
from tests._git_helpers import git_run, init_repo
from tests.test_deploy_submodels import _score_bundle
from tests.test_optimiser_apply import _make_ratebook_artifact
from tests.test_optimiser_routes_real_library import _scored_frame

PROOF_TYPES = [
    NodeType.POLARS,
    pytest.param(
        NodeType.EXTERNAL_FILE,
        marks=pytest.mark.xfail(
            strict=True,
            raises=NameError,
            reason="SUB-R01: Load File deploy loses its remapped policies input",
        ),
    ),
    NodeType.DATA_INPUT,
    pytest.param(
        NodeType.DATA_OUTPUT,
        marks=pytest.mark.xfail(
            strict=True,
            raises=ParseError,
            reason="SUB-R01: Data Output occurrences share a forbidden write destination",
        ),
    ),
    NodeType.CONSTANT,
    NodeType.BANDING,
    NodeType.RATING_STEP,
    NodeType.MODEL_SCORE,
    NodeType.EDGE_JOIN,
    NodeType.SCENARIO_EXPANDER,
    pytest.param(
        NodeType.MODELLING,
        marks=pytest.mark.xfail(
            strict=True,
            raises=AssertionError,
            reason="SUB-R01: Model Training uses the qualified child id as an artifact path",
        ),
    ),
    NodeType.EXPLORE,
    NodeType.OPTIMISER,
    NodeType.OPTIMISER_APPLY,
]


@pytest.fixture(autouse=True)
def _isolated_project(tmp_path: Path) -> None:
    """Neither standalone execution nor Git discovery may inherit the checkout."""
    init_repo(tmp_path)
    (tmp_path / "haute.toml").write_text(
        '[project]\nname = "instance-proof"\n', encoding="utf-8", newline="\n"
    )


def test_node_instance_project_is_self_contained(tmp_path: Path) -> None:
    assert Path(git_run(tmp_path, "rev-parse", "--show-toplevel")).resolve() == tmp_path.resolve()
    assert get_project_root(tmp_path) == tmp_path.resolve()


def _node(name: str, kind: NodeType = NodeType.POLARS, **config: object) -> GraphNode:
    return GraphNode(id=name, data=NodeData(label=name, nodeType=kind, config=config))


def _node_identities(graph: PipelineGraph) -> dict[str, tuple[str, NodeType, dict[str, float]]]:
    return {node.id: (node.data.label, node.data.nodeType, node.position) for node in graph.nodes}


def _edge(
    source: str, target: str, source_handle: str | None = None, target_handle: str | None = None
) -> GraphEdge:
    return GraphEdge(
        id=f"{source}/{source_handle}->{target}/{target_handle}",
        source=source,
        target=target,
        sourceHandle=source_handle,
        targetHandle=target_handle,
    )


def _input_rows(kind: NodeType) -> list[dict]:
    if kind == NodeType.CONSTANT:
        return [{"quote_id": "q1", "fixture_value": 10, "region": "London"}]
    if kind == NodeType.MODELLING:
        return [
            {
                "quote_id": f"q{i}",
                "fixture_value": float(i + 1),
                "region": "London" if i % 2 else "Manchester",
            }
            for i in range(40)
        ]
    if kind == NodeType.OPTIMISER:
        return (
            _scored_frame(n_quotes=6, n_steps=3)
            .rename({"expected_income": "fixture_value"})
            .to_dicts()
        )
    return [
        {"quote_id": "q1", "fixture_value": 10, "region": "London"},
        {"quote_id": "q2", "fixture_value": 20, "region": "Manchester"},
    ]


def _case(kind: NodeType, root: Path) -> tuple[dict, list[dict], str]:
    rows = _input_rows(kind)
    column = "premium"
    if kind == NodeType.POLARS:
        config = {"code": "df = policies.with_columns(premium=pl.col('fixture_value') * 2)"}
        expected = [{**r, "premium": r["fixture_value"] * 2} for r in rows]
    elif kind == NodeType.EXTERNAL_FILE:
        (root / "rates.json").write_text('{"factor": 3}', encoding="utf-8")
        config = {
            "fileType": "json",
            "path": "rates.json",
            "code": "df = policies.with_columns(premium=pl.col('fixture_value') * obj['factor'])",
        }
        expected = [{**r, "premium": r["fixture_value"] * 3} for r in rows]
    elif kind == NodeType.DATA_INPUT:
        pl.DataFrame({"quote_id": ["q1", "q2"], "premium": [31, 42]}).write_parquet(
            root / "fixed.parquet"
        )
        config = {"inputType": "file", "format": "parquet", "path": "fixed.parquet"}
        expected = [{"quote_id": "q1", "premium": 31}, {"quote_id": "q2", "premium": 42}]
    elif kind == NodeType.CONSTANT:
        config = {
            "values": [{"name": "premium", "value": "17"}, {"name": "quote_id", "value": "q1"}]
        }
        expected = [{"premium": 17.0, "quote_id": "q1"}]
    elif kind == NodeType.BANDING:
        config = {
            "factors": [
                {
                    "banding": "categorical",
                    "column": "region",
                    "outputColumn": "premium",
                    "rules": [
                        {"value": "London", "assignment": "high"},
                        {"value": "Manchester", "assignment": "low"},
                    ],
                }
            ]
        }
        expected = [{**rows[0], "premium": "high"}, {**rows[1], "premium": "low"}]
    elif kind == NodeType.RATING_STEP:
        config = {
            "tables": [
                {
                    "factors": ["region"],
                    "outputColumn": "premium",
                    "entries": [
                        {"region": "London", "value": 1.5},
                        {"region": "Manchester", "value": 2.0},
                    ],
                }
            ]
        }
        expected = [{**rows[0], "premium": 1.5}, {**rows[1], "premium": 2.0}]
    elif kind == NodeType.MODEL_SCORE:
        from catboost import CatBoostRegressor

        model = CatBoostRegressor(
            iterations=2,
            depth=1,
            thread_count=1,
            verbose=False,
            random_seed=7,
            allow_writing_files=False,
        )
        model.fit(pl.DataFrame({"fixture_value": [1, 2, 10, 20]}).to_numpy(), [2, 4, 20, 40])
        model.set_feature_names(["fixture_value"])
        model.save_model(str(root / "model.cbm"))
        config = {
            "sourceType": "file",
            "model_path": "model.cbm",
            "task": "regression",
            "output_column": "premium",
            "contract": {"inputs": ["fixture_value"], "outputs": ["premium"]},
        }
        predictions = model.predict([[10], [20]]).tolist()
        expected = [{**row, "premium": value} for row, value in zip(rows, predictions)]
    elif kind == NodeType.EDGE_JOIN:
        config = {"how": "left", "on": ["region"], "maintainOrder": "left_right"}
        expected = [{**rows[0], "premium": 7}, {**rows[1], "premium": 9}]
    elif kind == NodeType.SCENARIO_EXPANDER:
        config = {
            "quote_id": "quote_id",
            "column_name": "premium",
            "min_value": 1.0,
            "max_value": 2.0,
            "stepCount": 2,
            "step_column": "step",
        }
        expected = [
            {**r, "premium": value, "step": step}
            for r in rows
            for step, value in enumerate((1.0, 2.0))
        ]
    elif kind == NodeType.OPTIMISER_APPLY:
        (root / "optimisation.json").write_text(
            json.dumps(_make_ratebook_artifact()), encoding="utf-8"
        )
        config = {
            "sourceType": "file",
            "artifact_path": "optimisation.json",
            "version_column": "version",
            "optimised_value_column": "premium",
            "ratebook_input": "policies",
        }
        expected = [
            {**rows[0], "premium": 1.05, "region_optimised_factor": 1.05, "version": "rb_v1"},
            {**rows[1], "premium": 0.98, "region_optimised_factor": 0.98, "version": "rb_v1"},
        ]
    elif kind == NodeType.DATA_OUTPUT:
        config = {"outputType": "file", "format": "parquet", "path": "result.parquet"}
        expected, column = rows, "fixture_value"
    elif kind == NodeType.EXPLORE:
        config = {}
        expected, column = rows, "fixture_value"
    elif kind == NodeType.MODELLING:
        config = {
            "algorithm": "catboost",
            "task": "regression",
            "target": "fixture_value",
            "loss_function": "RMSE",
            "feature_columns": ["region"],
            "params": {"iterations": 1, "depth": 1},
            "evaluation": {
                "schema_version": 1,
                "strategy": "random",
                "seed": 42,
                "validation": {"method": "single", "size": 0.2},
            },
        }
        expected, column = rows, "fixture_value"
    else:
        assert kind == NodeType.OPTIMISER
        config = {
            "mode": "online",
            "objective": "fixture_value",
            "constraints": {"volume": {"min": 0.90}},
            "quote_id": "quote_id",
            "scenario_index": "scenario_index",
            "scenario_value": "scenario_value",
            "max_iter": 20,
            "tolerance": 1e-4,
        }
        expected, column = rows, "fixture_value"
    return config, expected, column


def _proof_graph(kind: NodeType, root: Path) -> tuple[PipelineGraph, list[dict], str]:
    config, expected, column = _case(kind, root)
    rows = _input_rows(kind)
    (root / "quote.json").write_text(json.dumps(rows), encoding="utf-8")
    pl.DataFrame(
        [{**r, "quote_id": f"owner_{r['quote_id']}", "fixture_value": 1} for r in rows]
    ).write_parquet(root / "policies.parquet")
    request = _node(
        "request",
        NodeType.API_INPUT,
        path="quote.json",
        tables=[
            {
                "path": "$[:]",
                "label": "claims_data",
                "emit": True,
                "row_id_column": "quote_id",
                "columns": [
                    {
                        "name": key,
                        "path": f"$[:].{key}",
                        "type": {str: "str", int: "int", float: "float"}[type(value)],
                        "status": "Confirmed",
                        "selected": True,
                        "levels": None,
                    }
                    for key, value in rows[0].items()
                ],
            }
        ],
    )
    has_input = kind not in {NodeType.DATA_INPUT, NodeType.CONSTANT}
    nodes = [
        request,
        _node(
            "policies",
            NodeType.DATA_INPUT,
            inputType="file",
            format="parquet",
            path="policies.parquet",
        ),
        _node("rate_calc", kind, **config),
    ]
    edges = (
        [
            _edge(
                "policies",
                "rate_calc",
                target_handle="base" if kind == NodeType.EDGE_JOIN else None,
            )
        ]
        if has_input
        else []
    )
    if kind == NodeType.EDGE_JOIN:
        nodes.append(
            _node(
                "factors",
                code=(
                    "df = pl.LazyFrame({'region': ['London', 'Manchester', 'Glasgow'], "
                    "'premium': [7, 9, 11]})"
                ),
            )
        )
        edges.append(_edge("factors", "rate_calc", target_handle="join"))
    plain = PipelineGraph(nodes=nodes, edges=edges, pipeline_name="proof")
    graph = create_node_instance_graph(
        plain, "rate_calc", "rate_calc", read_utility=lambda _: None
    ).graph
    if has_input:
        graph.edges.append(_edge("request", "rate_calc_2", "claims_data", "in__policies"))
    if kind == NodeType.EDGE_JOIN:
        graph.edges.append(_edge("factors", "rate_calc_2", target_handle="in__factors"))
    sink = kind in SINK_ONLY_NODE_TYPES
    port = "claims_data" if sink else "rate_calc_2"
    if not has_input:
        port = "combined"
        graph.nodes.append(
            _node(
                "combined",
                code=(
                    "df = pl.concat([claims_data.select('quote_id'), "
                    "rate_calc_2.select('premium')], how='horizontal_extend')"
                ),
            )
        )
        graph.edges.extend(
            [
                _edge("request", "combined", "claims_data"),
                _edge("rate_calc_2", "combined", "out__rate_calc"),
            ]
        )
    columns = (["quote_id"] if "quote_id" in expected[0] else []) + [column]
    graph.nodes.append(
        _node(
            "response",
            NodeType.OUTPUT,
            outputMapping=[
                {
                    "source_port": port,
                    "source_column": c,
                    "output_path": f"$[:].{c}",
                    "enabled": True,
                }
                for c in columns
            ],
        )
    )
    response_edge = _edge(
        "request" if sink else "rate_calc_2" if has_input else "combined",
        "response",
        "claims_data" if sink else "out__rate_calc" if has_input else None,
    )
    if sink or not has_input:
        graph.edges.insert(0, response_edge)
    else:
        graph.edges.append(response_edge)
    return graph, expected, column


@pytest.mark.parametrize("kind", PROOF_TYPES, ids=str)
def test_one_node_instance_cross_path_proof(
    kind: NodeType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    import tempfile

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.setenv("TMP", str(tmp_path))
    monkeypatch.setenv("TEMP", str(tmp_path))
    pipeline_dir.cache_clear()
    invalidate_pipeline_index()
    (tmp_path / "haute.toml").write_text(
        '[project]\npipeline = "pipeline.py"\n[deploy]\ntarget = "container"\n'
        '[deploy.container]\nbase_image = "python:3.11.9-slim"\n',
        encoding="utf-8",
    )
    with bound_project_root(tmp_path):
        graph, expected, column = _proof_graph(kind, tmp_path)
        target = qualified_runtime_node_id("rate_calc_2", "rate_calc_node")
        raw = graph.model_dump(mode="json", by_alias=True)
        if kind not in SINK_ONLY_NODE_TYPES:
            preview = client.post("/api/pipeline/preview", json={"graph": raw, "node_id": target})
            assert preview.status_code == 200, preview.text
            assert preview.json()["preview"] == expected, preview.json()
            traced = client.post(
                "/api/pipeline/trace",
                json={
                    "graph": raw,
                    "target_node_id": target,
                    "column": column,
                    "row_index": 0,
                    "row_values": expected[0],
                    "seed_plan": preview.json()["seed_plan"],
                },
            )
            assert traced.status_code == 200, traced.text
            assert traced.json()["trace"]["output_value"] == expected[0][column]
        columns = (["quote_id"] if "quote_id" in expected[0] else []) + [column]
        response_rows = [{c: row[c] for c in columns} for row in expected]
        assert execute_graph(flatten_executable_graph(graph))["response"].preview == response_rows
        saved = SavePipelineService(tmp_path).save_graph_transactionally(
            graph=graph,
            name="proof",
            description="",
            preamble=graph.preamble,
            source_file="pipeline.py",
            base_revision=None,
        )
        document = load_pipeline_editor_document(tmp_path / "pipeline.py", project_root=tmp_path)
        assert document.load_status == "ready"
        assert document.source_revision == saved.source_revision
        reloaded = parse_pipeline_to_graph(tmp_path / "pipeline.py", project_root=tmp_path)
        assert _node_identities(reloaded) == _node_identities(graph)
        # A canonical save/reload fixed point includes the definitions, configs and bindings.
        assert graph_to_code_multi(
            reloaded, pipeline_name="proof", source_file="pipeline.py"
        ) == graph_to_code_multi(graph, pipeline_name="proof", source_file="pipeline.py")
        assert collect_node_configs(reloaded) == collect_node_configs(graph)
        assert reloaded.submodels is not None and graph.submodels is not None
        assert reloaded.submodels.keys() == graph.submodels.keys()
        for name, definition in graph.submodels.items():
            loaded = reloaded.submodels[name]
            assert _node_identities(loaded.graph) == _node_identities(definition.graph)
            assert (loaded.input_ports, loaded.output_ports) == (
                definition.input_ports,
                definition.output_ports,
            )
            assert collect_node_configs(loaded.graph) == collect_node_configs(definition.graph)
        assert (
            execute_graph(flatten_executable_graph(reloaded))["response"].preview == response_rows
        )
        run = CliRunner().invoke(cli, ["run", "pipeline.py"], catch_exceptions=False)
        assert run.exit_code == 0, run.output
        assert "response" in run.output
        standalone_rows = tmp_path / "standalone.json"
        standalone = subprocess.run(
            [
                sys.executable,
                "-c",
                "import json, sys; from pathlib import Path; from pipeline import pipeline; "
                "Path(sys.argv[1]).write_text(json.dumps(pipeline.run(source='live').to_dicts()))",
                str(standalone_rows),
            ],
            cwd=tmp_path,
            text=True,
            capture_output=True,
        )
        assert standalone.returncode == 0, standalone.stderr
        assert json.loads(standalone_rows.read_text()) == response_rows
        deployed = resolve_config(DeployConfig.from_toml(tmp_path / "haute.toml"))
        request_rows = json.loads((tmp_path / "quote.json").read_text())
        assert _score_bundle(deployed, tmp_path / "bundle", request_rows) == response_rows
        if kind in {NodeType.MODELLING, NodeType.OPTIMISER}:
            endpoint = (
                "/api/modelling/train" if kind == NodeType.MODELLING else "/api/optimiser/solve"
            )
            started = client.post(endpoint, json={"graph": raw, "node_id": target})
            assert started.status_code == 200, started.text
            job_id = started.json()["job_id"]
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                job = client.get(f"{endpoint}/status/{job_id}").json()
                if job["status"] not in {"pending", "running", "queued", "preparing"}:
                    break
                time.sleep(0.05)
            assert job["status"] == "completed", (
                job["message"],
                job.get("worker_remote_traceback"),
            )
            result = job["result"]
            if kind == NodeType.MODELLING:
                assert result["development_rows"] > 0
                assert Path(result["model_path"]).is_file()
            else:
                from price_contour import OnlineOptimiser

                reference = OnlineOptimiser(
                    objective="fixture_value",
                    constraints={"volume": {"min": 0.90}},
                    max_iter=20,
                    tolerance=1e-4,
                ).solve(
                    _scored_frame(n_quotes=6, n_steps=3).rename(
                        {"expected_income": "fixture_value"}
                    )
                )
                assert result["total_objective"] == pytest.approx(reference.total_objective)
                assert (
                    result["total_objective"] > 6
                )  # The owner's six quotes each have objective 1.


@pytest.mark.parametrize("kind", [NodeType.POLARS, NodeType.MODEL_SCORE], ids=str)
def test_node_instance_acceptance_tracks_shared_edits_and_output_rename(
    kind: NodeType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
) -> None:
    from haute._graph_utils import edge_input_name

    monkeypatch.chdir(tmp_path)
    pipeline_dir.cache_clear()
    invalidate_pipeline_index()
    with bound_project_root(tmp_path):
        config, expected, _ = _case(kind, tmp_path)
        rows = _input_rows(kind)
        original = PipelineGraph(
            nodes=[
                _node(
                    "policies",
                    code=(
                        "df = pl.LazyFrame({'quote_id': ['p1'], "
                        "'fixture_value': [1], 'region': ['London']})"
                    ),
                ),
                _node("claims_data", code=f"df = pl.LazyFrame({rows!r})"),
                _node("rate_calc", kind, **config),
            ],
            edges=[_edge("policies", "rate_calc")],
        )
        service = SavePipelineService(tmp_path)
        saved = service.save_graph_transactionally(
            graph=original,
            name="acceptance",
            description="",
            preamble="",
            source_file="main.py",
            base_revision=None,
        )
        wrapped = client.post(
            "/api/submodel/instance",
            json={
                "node_id": "rate_calc",
                "graph": original.model_dump(mode="json", by_alias=True),
                "source_file": "main.py",
                "base_revision": saved.source_revision,
            },
        )
        assert wrapped.status_code == 200, wrapped.text
        graph = PipelineGraph.model_validate(wrapped.json()["graph"])
        graph.edges.append(_edge("claims_data", "rate_calc_2", target_handle="in__policies"))
        target = qualified_runtime_node_id("rate_calc_2", "rate_calc_node")

        def preview():
            response = client.post(
                "/api/pipeline/preview",
                json={
                    "graph": graph.model_dump(mode="json", by_alias=True),
                    "node_id": target,
                },
            )
            assert response.status_code == 200, response.text
            return response.json()["preview"]

        assert preview() == expected
        child = graph.submodels["rate_calc"].graph.nodes[0]
        if kind == NodeType.POLARS:
            child.data.config["code"] = (
                "df = policies.with_columns(premium=pl.col('fixture_value') * 3)"
            )
            expected = [{**row, "premium": row["fixture_value"] * 3} for row in rows]
        else:
            child.data.config["output_column"] = "edited_premium"
            child.data.config["contract"]["outputs"] = ["edited_premium"]
            expected = [
                {
                    **{k: v for k, v in row.items() if k != "premium"},
                    "edited_premium": row["premium"],
                }
                for row in expected
            ]
        assert preview() == expected
        saved = service.save_graph_transactionally(
            graph=graph,
            name="acceptance",
            description="",
            preamble="",
            source_file="main.py",
            base_revision=saved.source_revision,
        )
        assert (tmp_path / "modules/rate_calc.py").is_file()
        assert (
            'pipeline.connect("claims_data", "rate_calc_2", target_port="policies")'
            in (tmp_path / "main.py").read_text()
        )
        graph = parse_pipeline_file(tmp_path / "main.py")
        assert preview() == expected
        instance = graph.node_map["rate_calc_2"]
        instance.data.label = "claims_score"
        instance.data.config["alias"] = "claims_score"
        graph.nodes.append(_node("consumer", code="df = claims_score"))
        output = _edge("rate_calc_2", "consumer", "out__rate_calc")
        graph.edges.append(output)
        assert edge_input_name(output, instance, submodels=graph.submodels) == "claims_score"
        assert execute_graph(flatten_executable_graph(graph))["consumer"].preview == expected
        graph.nodes.append(
            _node(
                "response",
                NodeType.OUTPUT,
                outputMapping=[
                    {
                        "source_port": "consumer",
                        "source_column": column,
                        "output_path": f"$[:].{column}",
                        "enabled": True,
                    }
                    for column in expected[0]
                ],
            )
        )
        graph.edges.append(_edge("consumer", "response"))
        service.save_graph_transactionally(
            graph=graph,
            name="acceptance",
            description="",
            preamble="",
            source_file="main.py",
            base_revision=saved.source_revision,
        )
        standalone_rows = tmp_path / "renamed.json"
        standalone = subprocess.run(
            [
                sys.executable,
                "-c",
                "import json, sys; from pathlib import Path; from main import pipeline; "
                "Path(sys.argv[1]).write_text(json.dumps(pipeline.run(source='live').to_dicts()))",
                str(standalone_rows),
            ],
            cwd=tmp_path,
            text=True,
            capture_output=True,
        )
        assert standalone.returncode == 0, standalone.stderr
        assert json.loads(standalone_rows.read_text()) == expected
