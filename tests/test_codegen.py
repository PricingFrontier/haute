"""Tests for haute.codegen - graph JSON → Python code generation."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import polars as pl
import pytest

from haute._codegen_builders import (
    _build_extra_kwargs,
    _build_params,
    _retained_api_input_template,
    _sanitize_description,
    _wrap_user_code,
)
from haute._topo import UnknownEdgeEndpointError
from haute.codegen import (
    _generate_node_code,
    _instance_to_code,
    _node_to_code,
    _submodel_node_to_code,
    graph_to_code,
    graph_to_code_multi,
)
from haute.errors import ConfigError, ParseError
from tests.conftest import (
    compile_node_code as _compile_node_code,
)
from tests.conftest import (
    make_graph as _g,
)
from tests.conftest import (
    make_node as _n,
)
from tests.conftest import (
    make_output_config,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _file_input_config(
    path: str,
    *,
    format_name: str | None = None,
    mode: str | None = None,
    code: str | None = None,
    arguments: dict | None = None,
) -> dict:
    """Build an explicit canonical file Data Input config for codegen tests."""
    suffix = Path(path).suffix.lower()
    resolved_format, resolved_mode = {
        ".csv": ("csv", "scan"),
        ".json": ("json", "read"),
        ".jsonl": ("ndjson", "scan"),
        ".ndjson": ("ndjson", "scan"),
        ".parquet": ("parquet", "scan"),
        ".arrow": ("ipc", "scan"),
        ".feather": ("ipc", "scan"),
        ".ipc": ("ipc", "scan"),
    }.get(suffix, ("parquet", "scan"))
    config = {
        "inputType": "file",
        "format": format_name or resolved_format,
        "mode": mode or resolved_mode,
        "path": path,
        "arguments": arguments or {},
    }
    if code is not None:
        config["code"] = code
    return config


def _databricks_input_config(*, code: str | None = None) -> dict:
    config = {
        "inputType": "databricks",
        "http_path": "/sql/1.0/warehouses/test",
        "table": "catalog.schema.tbl",
        "arguments": {},
    }
    if code is not None:
        config["code"] = code
    return config


def _file_output_config(path: str, format_name: str) -> dict:
    return {
        "outputType": "file",
        "format": format_name,
        "mode": "sink" if format_name in {"csv", "parquet"} else "write",
        "path": path,
        "arguments": {},
    }


# ---------------------------------------------------------------------------
# _build_params
# ---------------------------------------------------------------------------


class TestBuildParams:
    def test_no_sources(self):
        assert _build_params([]) == "df: pl.LazyFrame"

    def test_single_source(self):
        assert _build_params(["load_data"]) == "load_data: pl.LazyFrame"

    def test_multiple_sources(self):
        result = _build_params(["a", "b"])
        assert result == "a: pl.LazyFrame, b: pl.LazyFrame"


# ---------------------------------------------------------------------------
# _node_to_code
# ---------------------------------------------------------------------------


class TestNodeToCode:
    @pytest.mark.parametrize(
        "label, config, expected_strings",
        [
            pytest.param(
                "Load Data",
                _file_input_config("data/input.parquet"),
                [
                    "def Load_Data()",
                    "resolve_data_input_from_config",
                    'config="config/data_input/Load_Data.json"',
                ],
                id="parquet",
            ),
            pytest.param(
                "CSV Source",
                _file_input_config("data/input.csv"),
                [
                    "resolve_data_input_from_config",
                    "def CSV_Source()",
                    'config="config/data_input/CSV_Source.json"',
                ],
                id="csv",
            ),
            pytest.param(
                "JSON Source",
                _file_input_config("data/input.json"),
                [
                    "resolve_data_input_from_config",
                    "def JSON_Source()",
                    'config="config/data_input/JSON_Source.json"',
                ],
                id="json",
            ),
            pytest.param(
                "JSONL Source",
                _file_input_config("data/input.jsonl"),
                [
                    "resolve_data_input_from_config",
                    "def JSONL_Source()",
                    'config="config/data_input/JSONL_Source.json"',
                ],
                id="jsonl",
            ),
            pytest.param(
                "DB Source",
                _databricks_input_config(),
                [
                    "resolve_data_input_from_config",
                    'config="config/data_input/DB_Source.json"',
                ],
                id="databricks",
            ),
        ],
    )
    def test_data_source(self, label, config, expected_strings):
        node = _n(
            {
                "id": "src",
                "data": {"label": label, "nodeType": "dataInput", "config": config},
            }
        )
        code = _node_to_code(node)
        for s in expected_strings:
            assert s in code, f"Expected {s!r} in generated code"
        _compile_node_code(code)

    @pytest.mark.parametrize(
        "label, config, expected_strings",
        [
            pytest.param(
                "Load Data",
                _file_input_config(
                    "data/input.parquet",
                    code="df = df.filter(pl.col('x') > 0)",
                ),
                ["df = resolve_data_input_from_config", "filter", "return df"],
                id="parquet_with_code",
            ),
            pytest.param(
                "CSV Source",
                _file_input_config(
                    "data/input.csv",
                    code="df = df.select('a', 'b')",
                ),
                ["df = resolve_data_input_from_config", "select", "return df"],
                id="csv_with_code",
            ),
            pytest.param(
                "DB Source",
                _databricks_input_config(code="df = df.limit(100)"),
                ["resolve_data_input_from_config", "limit", "return df"],
                id="databricks_with_code",
            ),
        ],
    )
    def test_data_source_with_code(self, label, config, expected_strings):
        """DataSource with user code emits boilerplate + user code."""
        node = _n(
            {
                "id": "src",
                "data": {"label": label, "nodeType": "dataInput", "config": config},
            }
        )
        code = _node_to_code(node)
        for s in expected_strings:
            assert s in code, f"Expected {s!r} in generated code"
        # No function parameters (still a source node)
        assert "() -> pl.LazyFrame" in code
        _compile_node_code(code)

    def test_data_source_without_code_unchanged(self):
        """DataSource without code still uses simple return template."""
        node = _n(
            {
                "id": "src",
                "data": {
                    "label": "Load Data",
                    "nodeType": "dataInput",
                    "config": _file_input_config("data/input.parquet"),
                },
            }
        )
        code = _node_to_code(node)
        assert "df = resolve_data_input_from_config" in code
        assert "return df" in code
        _compile_node_code(code)

    def test_data_source_codegen_preserves_non_loader_user_code(self):
        node = _n(
            {
                "id": "src",
                "data": {
                    "label": "Load Data",
                    "nodeType": "dataInput",
                    "config": _file_input_config(
                        "data/input.parquet",
                        code="import math\ndf = df.limit(math.floor(10.9))",
                    ),
                },
            }
        )
        code = _node_to_code(node)
        assert "import math" in code
        assert "df = df.limit(math.floor(10.9))" in code
        _compile_node_code(code)

    def test_transform_with_code(self):
        node = _n(
            {
                "id": "t",
                "data": {
                    "label": "Clean",
                    "nodeType": "polars",
                    "config": {"code": "df = load_data.filter(pl.col('x') > 0)"},
                },
            }
        )
        code = _node_to_code(node, source_names=["load_data"])
        assert "def Clean(load_data: pl.LazyFrame)" in code
        assert "df: pl.LazyFrame" in code
        assert "filter" in code
        assert "return df" in code
        _compile_node_code(code)

    def test_transform_without_code_emits_a_raising_placeholder(self):
        """A no-code transform has no implicit passthrough, even with one
        input: the body raises if run, while the file still saves/compiles."""
        node = _n(
            {
                "id": "t",
                "data": {"label": "Pass", "nodeType": "polars", "config": {}},
            }
        )
        code = _node_to_code(node, source_names=["upstream"])
        assert "def Pass(upstream: pl.LazyFrame)" in code
        assert "raise NotImplementedError" in code
        assert "return upstream" not in code
        _compile_node_code(code)

    def test_transform_without_code_no_sources_emits_a_raising_placeholder(self):
        """An orphan polars transform (no code, no inputs) is incoherent to RUN
        but is an ordinary half-built state to SAVE. It emits a placeholder that
        raises — never ``return df`` where ``df`` is unbound."""
        node = _n(
            {
                "id": "t",
                "data": {"label": "Pass", "nodeType": "polars", "config": {}},
            }
        )
        code = _node_to_code(node, source_names=[])
        assert "raise NotImplementedError" in code
        assert "return df" not in code
        _compile_node_code(code)

    def test_transform_with_code_and_no_sources_has_no_phantom_df_parameter(self):
        node = _n(
            {
                "id": "t",
                "data": {
                    "label": "Construct",
                    "nodeType": "polars",
                    "config": {"code": "df = pl.LazyFrame({'x': [1]})"},
                },
            }
        )

        code = _node_to_code(node, source_names=[])

        assert "def Construct() -> pl.LazyFrame:" in code
        _compile_node_code(code)

    def test_transform_rejects_df_as_a_named_input(self):
        node = _n(
            {
                "id": "t",
                "data": {
                    "label": "Transform",
                    "nodeType": "polars",
                    "config": {"code": "df = df.with_columns(x=pl.lit(1))"},
                },
            }
        )

        with pytest.raises(ConfigError, match="reserved output name"):
            _node_to_code(node, source_names=["df"])

    def test_transform_missing_output_cannot_fall_back_to_a_global_df(self):
        node = _n(
            {
                "id": "t",
                "data": {
                    "label": "MissingOutput",
                    "nodeType": "polars",
                    "config": {"code": "_ = source"},
                },
            }
        )
        code = _node_to_code(node, source_names=["source"])

        class PipelineStub:
            @staticmethod
            def polars(*args, **kwargs):
                if args and callable(args[0]):
                    return args[0]
                return lambda function: function

        namespace = {
            "pipeline": PipelineStub(),
            "pl": pl,
            "df": pl.LazyFrame({"wrong": [9]}),
        }
        exec(code, namespace)

        with pytest.raises(UnboundLocalError):
            namespace["MissingOutput"](pl.LazyFrame({"source": [1]}))

    def test_no_code_transform_with_df_input_still_saves_as_incomplete(self):
        node = _n(
            {
                "id": "t",
                "data": {
                    "label": "Transform",
                    "nodeType": "polars",
                    "config": {"code": ""},
                },
            }
        )

        code = _node_to_code(node, source_names=["df"])

        assert "raise NotImplementedError" in code
        _compile_node_code(code)

    def test_output_references_sidecar_and_passes_through(self):
        node = _n(
            {
                "id": "out",
                "data": {
                    "label": "Output",
                    "nodeType": "output",
                    "config": {
                        "outputMapping": [
                            {
                                "source_port": "transform",
                                "source_column": "a",
                                "output_path": "$[:].a",
                                "enabled": True,
                            },
                        ],
                        "outputFormat": "json",
                    },
                },
            }
        )
        code = _node_to_code(node, source_names=["transform"])
        # v2: the outputMapping lives in the JSON schema mapping; the generated
        # body routes through the shared assembler the executor calls (not a
        # passthrough, not a `.select(...)` baked into the body).
        assert 'config="config/quote_response/Output.json"' in code
        assert "transform.select(" not in code
        assert "assemble_output_from_config(" in code
        assert "return transform" not in code
        assert "source_names=['transform']" in code
        assert "def Output(transform: pl.LazyFrame)" in code
        _compile_node_code(code)

    def test_output_without_fields(self):
        node = _n(
            {
                "id": "out",
                "data": {
                    "label": "Final",
                    "nodeType": "output",
                    "config": make_output_config([]),
                },
            }
        )
        code = _node_to_code(node, source_names=["src"])
        assert "assemble_output_from_config(" in code
        assert "return src" not in code
        assert ".select" not in code
        _compile_node_code(code)

    def test_sink_parquet(self):
        node = _n(
            {
                "id": "s",
                "data": {
                    "label": "Write",
                    "nodeType": "dataOutput",
                    "config": _file_output_config("out.parquet", "parquet"),
                },
            }
        )
        code = _node_to_code(node, source_names=["transform"])
        assert '@pipeline.data_output(config="config/data_output/Write.json"' in code
        assert "def Write(transform: pl.LazyFrame)" in code
        assert "return transform" in code
        assert "bounded_sink" not in code
        _compile_node_code(code)

    def test_sink_csv(self):
        node = _n(
            {
                "id": "s",
                "data": {
                    "label": "Write CSV",
                    "nodeType": "dataOutput",
                    "config": _file_output_config("out.csv", "csv"),
                },
            }
        )
        code = _node_to_code(node)
        assert '@pipeline.data_output(config="config/data_output/Write_CSV.json"' in code
        assert "return df" in code
        assert "bounded_sink" not in code
        _compile_node_code(code)

    def test_model_score(self):
        node = _n(
            {
                "id": "ms",
                "data": {
                    "label": "Score",
                    "nodeType": "modelScore",
                    "config": {
                        "sourceType": "run",
                        "run_id": "abc123",
                        "artifact_path": "model.cbm",
                        "task": "regression",
                        "output_column": "prediction",
                    },
                },
            }
        )
        code = _node_to_code(node)
        assert 'config="config/model_scoring/Score.json"' in code
        # Thin delegation body
        assert "score_from_config" in code
        assert "def Score(df: pl.LazyFrame)" in code
        # B18: base_dir parameter resolves config relative to pipeline file
        assert "base = str(_HAUTE_CONFIG_BASE)" in code
        assert "base_dir=base" in code
        _compile_node_code(code)

    def test_model_score_with_user_code_has_base_dir(self):
        """B18: Model score with user code also passes base_dir to score_from_config."""
        node = _n(
            {
                "id": "ms",
                "data": {
                    "label": "ScorePost",
                    "nodeType": "modelScore",
                    "config": {
                        "sourceType": "run",
                        "run_id": "abc123",
                        "artifact_path": "model.cbm",
                        "task": "regression",
                        "output_column": "prediction",
                        "code": "df = df.with_columns(double_score=pl.col('prediction') * 2)",
                    },
                },
            }
        )
        code = _node_to_code(node)
        assert "score_from_config" in code
        assert "base = str(_HAUTE_CONFIG_BASE)" in code
        assert "base_dir=base" in code
        assert "df = score_from_config(" in code
        assert "df = df.with_columns(double_score=pl.col('prediction') * 2)" in code
        assert "return df" in code
        assert "return result" not in code
        _compile_node_code(code)

    def test_rating_step(self):
        node = _n(
            {
                "id": "rs",
                "data": {
                    "label": "Lookup",
                    "nodeType": "ratingStep",
                    "config": {
                        "tables": [
                            {
                                "factors": ["region"],
                                "outputColumn": "region_factor",
                                "defaultValue": 1.0,
                                "entries": [{"region": "North", "value": 1.1}],
                            }
                        ]
                    },
                },
            }
        )
        code = _node_to_code(node)
        assert 'config="config/rating_step/Lookup.json"' in code
        assert "def Lookup(" in code
        assert "return df" in code
        _compile_node_code(code)

    @pytest.mark.parametrize(
        "label, config, source_names, expected_strings",
        [
            pytest.param(
                "Model",
                {"path": "model.pkl", "fileType": "pickle", "code": "df = obj.predict(df)"},
                ["features"],
                ['config="config/load_file/Model.json"', "load_external_object", "obj"],
                id="pickle",
            ),
            pytest.param(
                "CB Model",
                {
                    "path": "model.cbm",
                    "fileType": "catboost",
                    "modelClass": "regressor",
                    "code": "df = obj.predict(df)",
                },
                [],
                [
                    'config="config/load_file/CB_Model.json"',
                    "load_external_object",
                ],
                id="catboost",
            ),
        ],
    )
    def test_external_file(self, label, config, source_names, expected_strings):
        node = _n(
            {
                "id": "ext",
                "data": {"label": label, "nodeType": "externalFile", "config": config},
            }
        )
        code = (
            _node_to_code(node, source_names=source_names) if source_names else _node_to_code(node)
        )
        for s in expected_strings:
            assert s in code, f"Expected {s!r} in generated code"
        assert config["path"] not in code
        assert config["fileType"] not in code
        if "modelClass" in config:
            assert config["modelClass"] not in code
        _compile_node_code(code)


# ---------------------------------------------------------------------------
# graph_to_code
# ---------------------------------------------------------------------------


class TestGraphToCode:
    def test_generates_valid_python(self):
        graph = _g(
            {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "Source",
                            "nodeType": "dataInput",
                            "config": {"path": "data.parquet"},
                        },
                    },
                    {
                        "id": "t",
                        "data": {
                            "label": "Transform",
                            "nodeType": "polars",
                            "config": {"code": "df = Source.with_columns(y=pl.col('x'))"},
                        },
                    },
                ],
                "edges": [{"id": "e1", "source": "src", "target": "t"}],
            }
        )
        code = graph_to_code(graph, pipeline_name="test_pipe")
        assert "import polars as pl" in code
        assert "import haute" in code
        assert 'Pipeline("test_pipe"' in code
        assert "def Source()" in code
        assert "def Transform(Source: pl.LazyFrame)" in code
        assert 'pipeline.connect("Source", "Transform")' in code
        compile(code, "<test>", "exec")

    def test_preamble_positioned_before_pipeline_def(self):
        graph = _g(
            {
                "nodes": [
                    {
                        "id": "s",
                        "data": {
                            "label": "S",
                            "nodeType": "dataInput",
                            "config": {"path": "d.parquet"},
                        },
                    }
                ],
                "edges": [],
            }
        )
        code = graph_to_code(graph, preamble="import numpy as np")
        lines = code.splitlines()
        preamble_idx = next(i for i, line in enumerate(lines) if "numpy" in line)
        pipeline_idx = next(i for i, line in enumerate(lines) if "haute.Pipeline(" in line)
        assert preamble_idx < pipeline_idx, "Preamble must appear before pipeline definition"
        compile(code, "<test>", "exec")

    def test_empty_graph(self):
        code = graph_to_code(_g({"nodes": [], "edges": []}))
        assert "import polars as pl" in code
        assert "import haute" in code
        assert "Pipeline" in code
        # No nodes, so no @pipeline.<type> decorators or pipeline.connect
        assert not any(line.strip().startswith("@pipeline.") for line in code.splitlines())
        assert "pipeline.connect" not in code
        compile(code, "<test>", "exec")

    def test_description_included(self):
        graph = _g({"nodes": [], "edges": []})
        code = graph_to_code(graph, pipeline_name="p", description="Motor pricing")
        assert "description='Motor pricing'" in code

    def test_multi_node_pipeline_compiles(self):
        """Full 3-node graph with edges generates compilable code."""
        graph = _g(
            {
                "nodes": [
                    {
                        "id": "a",
                        "data": {
                            "label": "Read",
                            "nodeType": "dataInput",
                            "config": {"path": "d.parquet"},
                        },
                    },
                    {
                        "id": "b",
                        "data": {
                            "label": "Clean",
                            "nodeType": "polars",
                            "config": {"code": "df = Read.drop_nulls()"},
                        },
                    },
                    {
                        "id": "c",
                        "data": {
                            "label": "Out",
                            "nodeType": "output",
                            "config": make_output_config(["x"]),
                        },
                    },
                ],
                "edges": [
                    {"id": "e1", "source": "a", "target": "b"},
                    {"id": "e2", "source": "b", "target": "c"},
                ],
            }
        )
        code = graph_to_code(graph)
        compile(code, "<test>", "exec")
        # Verify edges are emitted
        assert 'pipeline.connect("Read", "Clean")' in code
        assert 'pipeline.connect("Clean", "Out")' in code

    def test_optimiser_apply_ratebook_input_returns_selected_source(self):
        graph = _g(
            {
                "nodes": [
                    {
                        "id": "scored-node",
                        "data": {
                            "label": "scored_quotes",
                            "nodeType": "dataInput",
                            "config": _file_input_config("scored.parquet"),
                        },
                    },
                    {
                        "id": "banding-node",
                        "data": {
                            "label": "age_veh_banding",
                            "nodeType": "banding",
                            "config": {"factors": []},
                        },
                    },
                    {
                        "id": "apply-node",
                        "data": {
                            "label": "apply_optimisation",
                            "nodeType": "optimiserApply",
                            "config": {
                                "sourceType": "file",
                                "artifact_path": "artifacts/ratebook.json",
                                "ratebook_input": "age_veh_banding",
                            },
                        },
                    },
                ],
                "edges": [
                    {"id": "e1", "source": "scored-node", "target": "apply-node"},
                    {"id": "e2", "source": "banding-node", "target": "apply-node"},
                ],
            }
        )

        code = graph_to_code(graph)

        assert (
            "def apply_optimisation("
            "scored_quotes: pl.LazyFrame, age_veh_banding: pl.LazyFrame"
            ")" in code
        )
        # The body delegates to the shared apply helper, passing every frame
        # plus the aligned exact input-name list; ratebook_input selection happens at
        # runtime inside the helper (differential harness pins the value).
        assert "apply_optimiser_apply_from_config(" in code
        assert "scored_quotes, age_veh_banding," in code
        assert "source_names=['scored_quotes', 'age_veh_banding']" in code
        assert "source_ids=" not in code
        compile(code, "<test>", "exec")

    def test_optimiser_apply_online_mode_ignores_stale_ratebook_input_return(self):
        graph = _g(
            {
                "nodes": [
                    {
                        "id": "scored-node",
                        "data": {
                            "label": "scored_quotes",
                            "nodeType": "dataInput",
                            "config": _file_input_config("scored.parquet"),
                        },
                    },
                    {
                        "id": "banding-node",
                        "data": {
                            "label": "age_veh_banding",
                            "nodeType": "banding",
                            "config": {"factors": []},
                        },
                    },
                    {
                        "id": "apply-node",
                        "data": {
                            "label": "apply_optimisation",
                            "nodeType": "optimiserApply",
                            "config": {
                                "sourceType": "run",
                                "run_id": "online-run",
                                "optimiser_mode": "online",
                                "ratebook_input": "stale_banding",
                            },
                        },
                    },
                ],
                "edges": [
                    {"id": "e1", "source": "scored-node", "target": "apply-node"},
                    {"id": "e2", "source": "banding-node", "target": "apply-node"},
                ],
            }
        )

        code = graph_to_code(graph)

        # Online artifacts select the first input at RUNTIME inside the shared
        # helper (artifact.mode != "ratebook"), so the body just delegates —
        # no codegen-time return rewrite.
        assert "apply_optimiser_apply_from_config(" in code
        compile(code, "<test>", "exec")

    def test_optimiser_apply_mlflow_source_without_mode_ignores_ratebook_input_return(self):
        graph = _g(
            {
                "nodes": [
                    {
                        "id": "scored-node",
                        "data": {
                            "label": "scored_quotes",
                            "nodeType": "dataInput",
                            "config": {"path": "scored.parquet"},
                        },
                    },
                    {
                        "id": "banding-node",
                        "data": {
                            "label": "age_veh_banding",
                            "nodeType": "banding",
                            "config": {"factors": []},
                        },
                    },
                    {
                        "id": "apply-node",
                        "data": {
                            "label": "apply_optimisation",
                            "nodeType": "optimiserApply",
                            "config": {
                                "sourceType": "run",
                                "run_id": "missing-run",
                                "ratebook_input": "age_veh_banding",
                            },
                        },
                    },
                ],
                "edges": [
                    {"id": "e1", "source": "scored-node", "target": "apply-node"},
                    {"id": "e2", "source": "banding-node", "target": "apply-node"},
                ],
            }
        )

        code = graph_to_code(graph)

        # An MLflow source whose artifact mode is only known at load time still
        # delegates to the shared helper; selection is a runtime concern.
        assert "apply_optimiser_apply_from_config(" in code
        compile(code, "<test>", "exec")

    def test_optimiser_apply_ratebook_input_roundtrips_through_sidecar(self, tmp_path):
        from haute._config_io import collect_node_configs
        from haute.parser import parse_pipeline_source

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "scored-node",
                        "data": {
                            "label": "scored_quotes",
                            "nodeType": "dataInput",
                            "config": _file_input_config("scored.parquet"),
                        },
                    },
                    {
                        "id": "banding-node",
                        "data": {
                            "label": "age_veh_banding",
                            "nodeType": "banding",
                            "config": {"factors": []},
                        },
                    },
                    {
                        "id": "apply-node",
                        "data": {
                            "label": "apply_optimisation",
                            "nodeType": "optimiserApply",
                            "config": {
                                "sourceType": "file",
                                "artifact_path": "artifacts/ratebook.json",
                                "ratebook_input": "age_veh_banding",
                            },
                        },
                    },
                ],
                "edges": [
                    {"id": "e1", "source": "scored-node", "target": "apply-node"},
                    {"id": "e2", "source": "banding-node", "target": "apply-node"},
                ],
            }
        )
        code = graph_to_code(graph)
        for rel_path, content in collect_node_configs(graph).items():
            cfg_file = tmp_path / rel_path
            cfg_file.parent.mkdir(parents=True, exist_ok=True)
            cfg_file.write_text(content)

        parsed = parse_pipeline_source(code, _base_dir=tmp_path)

        parsed_apply = next(n for n in parsed.nodes if n.id == "apply_optimisation")
        assert parsed_apply.data.config["ratebook_input"] == "age_veh_banding"
        assert any(
            e.source == "age_veh_banding" and e.target == "apply_optimisation" for e in parsed.edges
        )

    @pytest.mark.parametrize("banding_source", [None, "stale_banding"])
    def test_ratebook_optimiser_requires_exact_banding_source(self, banding_source):
        config = {"mode": "ratebook", "data_input": "quotes"}
        if banding_source is not None:
            config["banding_source"] = banding_source
        graph = _g(
            {
                "nodes": [
                    {
                        "id": "quotes",
                        "data": {
                            "label": "quotes",
                            "nodeType": "dataInput",
                            "config": _file_input_config("quotes.parquet"),
                        },
                    },
                    {
                        "id": "optimiser",
                        "data": {"label": "optimiser", "nodeType": "optimiser", "config": config},
                    },
                ],
                "edges": [{"id": "e", "source": "quotes", "target": "optimiser"}],
            }
        )
        with pytest.raises(ConfigError, match="banding_source"):
            graph_to_code(graph)

    def test_online_optimiser_rejects_provided_stale_banding_source(self):
        graph = _g(
            {
                "nodes": [
                    {
                        "id": "quotes",
                        "data": {
                            "label": "quotes",
                            "nodeType": "dataInput",
                            "config": _file_input_config("quotes.parquet"),
                        },
                    },
                    {
                        "id": "optimiser",
                        "data": {
                            "label": "optimiser",
                            "nodeType": "optimiser",
                            "config": {
                                "mode": "online",
                                "data_input": "quotes",
                                "banding_source": "stale_banding",
                            },
                        },
                    },
                ],
                "edges": [{"id": "e", "source": "quotes", "target": "optimiser"}],
            }
        )
        with pytest.raises(ConfigError, match="banding_source"):
            graph_to_code(graph)

    @pytest.mark.parametrize("ratebook_input", [None, "stale_banding"])
    def test_ratebook_apply_requires_exact_ratebook_input(self, ratebook_input):
        config = {"optimiser_mode": "ratebook"}
        if ratebook_input is not None:
            config["ratebook_input"] = ratebook_input
        graph = _g(
            {
                "nodes": [
                    {
                        "id": "quotes",
                        "data": {
                            "label": "quotes",
                            "nodeType": "dataInput",
                            "config": _file_input_config("quotes.parquet"),
                        },
                    },
                    {
                        "id": "apply",
                        "data": {"label": "apply", "nodeType": "optimiserApply", "config": config},
                    },
                ],
                "edges": [{"id": "e", "source": "quotes", "target": "apply"}],
            }
        )
        with pytest.raises(ConfigError, match="ratebook_input"):
            graph_to_code(graph)

    def test_apply_without_known_mode_rejects_stale_ratebook_input(self):
        graph = _g(
            {
                "nodes": [
                    {
                        "id": "quotes",
                        "data": {
                            "label": "quotes",
                            "nodeType": "dataInput",
                            "config": _file_input_config("quotes.parquet"),
                        },
                    },
                    {
                        "id": "apply",
                        "data": {
                            "label": "apply",
                            "nodeType": "optimiserApply",
                            "config": {"ratebook_input": "stale"},
                        },
                    },
                ],
                "edges": [{"id": "e", "source": "quotes", "target": "apply"}],
            }
        )
        with pytest.raises(ConfigError, match="ratebook_input"):
            graph_to_code(graph)


# ---------------------------------------------------------------------------
# Live switch codegen
# ---------------------------------------------------------------------------


class TestLiveSwitchCodegen:
    def _switch_node(self, scenario_map=None):
        if scenario_map is None:
            scenario_map = {"live_src": "live", "batch_src": "test_batch"}
        return _n(
            {
                "id": "switch",
                "data": {
                    "label": "Switch",
                    "nodeType": "liveSwitch",
                    "config": {
                        "input_scenario_map": scenario_map,
                        "inputs": ["live_src", "batch_src"],
                    },
                },
            }
        )

    def test_emits_config_ref_with_live_active(self):
        code = _node_to_code(self._switch_node(), source_names=["live_src", "batch_src"])
        assert 'config="config/source_switch/Switch.json"' in code
        # Scenario-aware body: delegates to the shared selector reading the
        # active runtime source, instead of hard-wiring the "live" branch.
        assert "select_live_switch_input(" in code
        assert "_scenario_ctx.get()" in code
        assert "{'live_src': live_src, 'batch_src': batch_src}" in code
        _compile_node_code(code)

    def test_emits_config_ref_with_no_live_mapping(self):
        code = _node_to_code(
            self._switch_node({"live_src": "test_batch", "batch_src": "prod"}),
            source_names=["live_src", "batch_src"],
        )
        assert 'config="config/source_switch/Switch.json"' in code
        # No hard-wired branch — the shared selector picks (or falls back) at
        # runtime based on the active source.
        assert "select_live_switch_input(" in code
        _compile_node_code(code)

    def test_round_trip_preserves_scenario_map(self, tmp_path):
        """Codegen → parse round-trip must preserve input_scenario_map."""
        import json

        scenario_map = {"live_src": "live", "batch_src": "test_batch"}
        node = self._switch_node(scenario_map)
        code = _node_to_code(node, source_names=["live_src", "batch_src"])
        full_code = (
            "import polars as pl\nimport haute\n"
            'pipeline = haute.Pipeline("test")\n\n'
            '@pipeline.data_input(config="config/data_input/live_src.json")\n'
            "def live_src() -> pl.LazyFrame:\n"
            '    return pl.scan_parquet("a.parquet")\n\n'
            '@pipeline.data_input(config="config/data_input/batch_src.json")\n'
            "def batch_src() -> pl.LazyFrame:\n"
            '    return pl.scan_parquet("b.parquet")\n\n'
            f"{code}\n"
            'pipeline.connect("live_src", "Switch")\n'
            'pipeline.connect("batch_src", "Switch")\n'
        )
        # Write config JSON files so the parser can resolve them
        cfg_dir = tmp_path / "config" / "source_switch"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "Switch.json").write_text(
            json.dumps({"input_scenario_map": scenario_map, "inputs": ["live_src", "batch_src"]})
        )
        for name in ("live_src", "batch_src"):
            ds_dir = tmp_path / "config" / "data_input"
            ds_dir.mkdir(parents=True, exist_ok=True)
            (ds_dir / f"{name}.json").write_text(json.dumps(_file_input_config("a.parquet")))

        py_file = tmp_path / "test.py"
        py_file.write_text(full_code)
        from haute.parser import parse_pipeline_file

        graph = parse_pipeline_file(py_file)
        switch_nodes = [n for n in graph.nodes if n.data.nodeType == "liveSwitch"]
        assert len(switch_nodes) == 1
        assert switch_nodes[0].data.config["input_scenario_map"] == scenario_map


# ---------------------------------------------------------------------------
# Safety net: committed pipeline files must have live switch set to "live"
# ---------------------------------------------------------------------------


def _find_pipeline_files() -> list[Path]:
    """Find .py files containing live_switch=True (excluding tests and venv)."""
    results = []
    for py_file in PROJECT_ROOT.rglob("*.py"):
        rel = py_file.relative_to(PROJECT_ROOT)
        if rel.parts[0] in (".venv", "tests"):
            continue
        try:
            text = py_file.read_text()
        except (OSError, UnicodeDecodeError):
            continue
        if re.search(r"live_switch\s*=\s*True", text):
            results.append(py_file)
    return results


class TestLiveSwitchSafety:
    """Ensure the global active_scenario is 'live' before committing.

    The scenario is now a global setting stored in the .haute.json sidecar,
    not a per-node config value. If this test fails, someone left the
    active_scenario on a non-live value — reset it to 'live' before committing.
    """

    @pytest.mark.parametrize(
        "pipeline_file", _find_pipeline_files(), ids=lambda p: str(p.relative_to(PROJECT_ROOT))
    )
    def test_active_scenario_is_live(self, pipeline_file: Path):
        import json

        sidecar = pipeline_file.with_suffix(".haute.json")
        if not sidecar.exists():
            return  # no sidecar → defaults to "live", nothing to check
        data = json.loads(sidecar.read_text())
        active = data.get("active_source", "live")
        assert active == "live", (
            f"{sidecar.relative_to(PROJECT_ROOT)}: active_source is "
            f"'{active}' — must be 'live' before committing."
        )


# ---------------------------------------------------------------------------
# Codegen error-path and edge-case tests
# ---------------------------------------------------------------------------


class TestSelectedColumnsCodegen:
    """Tests for selected_columns code generation.

    The executor handles .select() filtering at runtime based on config.
    Codegen should NOT inject .select() into function bodies — the config
    (JSON sidecar or decorator kwarg) is sufficient.
    """

    def test_no_select_in_banding_body(self):
        """Banding with selected_columns does NOT inject .select() — executor handles it."""
        node = _n(
            {
                "id": "b1",
                "data": {
                    "label": "area_band",
                    "nodeType": "banding",
                    "config": {
                        "factors": [
                            {
                                "banding": "continuous",
                                "column": "area",
                                "outputColumn": "area_factor",
                                "rules": [{"from": 0, "to": 10, "value": "1.0"}],
                            }
                        ],
                        "selected_columns": ["area", "area_factor"],
                    },
                },
            }
        )
        code = _node_to_code(node, ["load_data"])
        assert ".select(" not in code

    def test_no_select_in_source_body(self):
        """DataSource with selected_columns does NOT inject .select() — executor handles it."""
        node = _n(
            {
                "id": "s1",
                "data": {
                    "label": "load_data",
                    "nodeType": "dataInput",
                    "config": {"path": "data.parquet", "selected_columns": ["a", "b"]},
                },
            }
        )
        code = _node_to_code(node, [])
        assert ".select(" not in code

    def test_no_select_without_config(self):
        """No .select() emitted when selected_columns is absent."""
        node = _n(
            {
                "id": "s1",
                "data": {
                    "label": "load_data",
                    "nodeType": "dataInput",
                    "config": {"path": "data.parquet"},
                },
            }
        )
        code = _node_to_code(node, [])
        assert ".select(" not in code

    def test_transform_uses_decorator_kwarg(self):
        """Transform with selected_columns uses decorator kwarg, not .select() in body."""
        node = _n(
            {
                "id": "t1",
                "data": {
                    "label": "my_transform",
                    "nodeType": "polars",
                    "config": {
                        "code": "df = load_data.with_columns(y=pl.col('x') * 2)",
                        "selected_columns": ["x", "y"],
                    },
                },
            }
        )
        code = _node_to_code(node, ["load_data"])
        assert "selected_columns=" in code
        # .select() should NOT be in the function body (only in decorator)
        lines = code.split("\n")
        body_lines = [line for line in lines if not line.startswith("@")]
        assert not any(".select(" in line for line in body_lines)

    def test_transform_no_decorator_kwarg_when_empty(self):
        """Transform without selected_columns uses ``@pipeline.polars`` with no
        ``selected_columns=`` kwarg.  Contract kwargs are a separate adoption
        (see :mod:`tests.test_column_contracts_adoption`) and may appear, but
        there must be no ``selected_columns=`` attribute when the config lacks it.
        """
        node = _n(
            {
                "id": "t1",
                "data": {
                    "label": "my_transform",
                    "nodeType": "polars",
                    "config": {"code": ""},
                },
            }
        )
        # Post Item #22: empty code + no inputs raises, so pass a source.
        code = _node_to_code(node, ["upstream"])
        first_line = code.splitlines()[0]
        assert first_line.startswith("@pipeline.polars"), first_line
        assert "selected_columns" not in first_line


class TestCodegenEdgeCases:
    """Edge cases and error paths for code generation."""

    def test_empty_graph_produces_valid_code(self):
        """An empty graph (no nodes, no edges) should still produce valid Python."""
        code = graph_to_code(_g({"nodes": [], "edges": []}))
        assert "import polars as pl" in code
        assert "import haute" in code
        assert "pipeline.connect" not in code
        compile(code, "<test>", "exec")

    def test_node_with_special_characters_in_label(self):
        """Labels with special chars should be sanitized to valid Python identifiers."""
        node = _n(
            {
                "id": "special",
                "data": {
                    "label": "My Node (v2) - Final!",
                    "nodeType": "polars",
                    "config": {"code": "df = df.with_columns(y=pl.lit(1))"},
                },
            }
        )
        code = _node_to_code(node)
        # Function name should be a valid Python identifier
        assert "def " in code
        # Should compile without errors
        _compile_node_code(code)

    def test_node_with_unicode_in_label(self):
        """Unicode characters in labels should be sanitized."""
        node = _n(
            {
                "id": "unicode",
                "data": {
                    "label": "price_update_cafe",
                    "nodeType": "polars",
                    "config": {"code": "df = df.with_columns(y=pl.lit(1))"},
                },
            }
        )
        code = _node_to_code(node)
        _compile_node_code(code)

    def test_node_with_empty_config_values(self):
        """Nodes with empty/None config values should still produce valid code."""
        node = _n(
            {
                "id": "empty",
                "data": {
                    "label": "EmptyConfig",
                    "nodeType": "polars",
                    "config": {"code": None},
                },
            }
        )
        # Post Item #22: a polars node with no code requires at least one
        # source to be wired (otherwise it raises); pass one here.
        code = _node_to_code(node, source_names=["upstream"])
        assert "def EmptyConfig(" in code
        _compile_node_code(code)

    def test_node_with_empty_string_config(self):
        """Transform with empty string code emits the raising placeholder."""
        node = _n(
            {
                "id": "empty",
                "data": {
                    "label": "EmptyCode",
                    "nodeType": "polars",
                    "config": {"code": ""},
                },
            }
        )
        code = _node_to_code(node, source_names=["upstream"])
        assert "raise NotImplementedError" in code
        assert "return upstream" not in code
        _compile_node_code(code)

    def test_graph_with_edge_referencing_nonexistent_node(self):
        """Codegen must reject rather than silently erase a dangling edge."""
        graph = _g(
            {
                "nodes": [
                    {
                        "id": "a",
                        "data": {
                            "label": "A",
                            "nodeType": "dataInput",
                            "config": {"path": "d.parquet"},
                        },
                    },
                ],
                "edges": [
                    {"id": "e1", "source": "a", "target": "ghost_node"},
                ],
            }
        )
        with pytest.raises(UnknownEdgeEndpointError) as exc_info:
            graph_to_code(graph)

        assert exc_info.value.unknown_node_ids == ("ghost_node",)
        assert tuple(edge.id for edge in exc_info.value.dropped_edges) == ("e1",)

    def test_node_with_very_long_label(self):
        """A node with a very long label (>200 chars) should still produce valid code."""
        long_label = "A" * 250
        node = _n(
            {
                "id": "long",
                "data": {
                    "label": long_label,
                    "nodeType": "polars",
                    "config": {"code": "df = df.with_columns(y=pl.lit(1))"},
                },
            }
        )
        code = _node_to_code(node)
        # Should produce a valid Python function name (even if very long)
        assert f"def {long_label}(" in code
        _compile_node_code(code)

    def test_output_with_none_fields(self):
        """Output node with an empty outputMapping still routes via the assembler."""
        node = _n(
            {
                "id": "out",
                "data": {
                    "label": "Out",
                    "nodeType": "output",
                    "config": make_output_config([]),
                },
            }
        )
        code = _node_to_code(node, source_names=["src"])
        assert "assemble_output_from_config(" in code
        assert "return src" not in code
        assert ".select" not in code
        _compile_node_code(code)

    def test_sink_with_empty_path(self):
        """Sink node with empty path should still generate compilable code."""
        node = _n(
            {
                "id": "s",
                "data": {
                    "label": "Sink",
                    "nodeType": "dataOutput",
                    "config": {"path": "", "format": "parquet"},
                },
            }
        )
        code = _node_to_code(node)
        assert "def Sink(" in code
        _compile_node_code(code)

    def test_data_source_with_no_config_keys(self):
        """Data source with completely empty config should still compile."""
        node = _n(
            {
                "id": "src",
                "data": {
                    "label": "Source",
                    "nodeType": "dataInput",
                    "config": {},
                },
            }
        )
        code = _node_to_code(node)
        assert "def Source()" in code
        _compile_node_code(code)

    def test_external_file_with_empty_code_generates_passthrough(self):
        """External file node with no user code should produce a passthrough."""
        node = _n(
            {
                "id": "ext",
                "data": {
                    "label": "Model",
                    "nodeType": "externalFile",
                    "config": {"path": "model.pkl", "fileType": "pickle", "code": ""},
                },
            }
        )
        code = _node_to_code(node, source_names=["features"])
        assert "df = features" in code
        assert "return df" in code
        _compile_node_code(code)

    def test_external_file_binds_its_documented_df_input(self):
        node = _n(
            {
                "id": "ext",
                "data": {
                    "label": "Model",
                    "nodeType": "externalFile",
                    "config": {
                        "path": "model.pkl",
                        "fileType": "pickle",
                        "code": "df = df.with_columns(prediction=pl.lit(obj))",
                    },
                },
            }
        )

        code = _node_to_code(node, source_names=["features"])

        assert code.index("df = features") < code.index("df = df.with_columns")
        _compile_node_code(code)

    def test_constant_with_empty_values(self):
        """Constant node with empty values list should use default."""
        node = _n(
            {
                "id": "c",
                "data": {
                    "label": "MyConst",
                    "nodeType": "constant",
                    "config": {"values": []},
                },
            }
        )
        code = _node_to_code(node)
        assert "def MyConst()" in code
        assert '"constant": [0]' in code
        _compile_node_code(code)

    def test_constant_with_none_values(self):
        """Constant node with None values list should use default."""
        node = _n(
            {
                "id": "c",
                "data": {
                    "label": "MyConst",
                    "nodeType": "constant",
                    "config": {"values": None},
                },
            }
        )
        code = _node_to_code(node)
        assert '"constant": [0]' in code
        _compile_node_code(code)

    def test_description_with_quotes_escaped(self):
        """Node description containing double quotes should not break code generation."""
        graph = _g(
            {
                "nodes": [
                    {
                        "id": "a",
                        "data": {
                            "label": "A",
                            "nodeType": "dataInput",
                            "config": {"path": "d.parquet"},
                        },
                    }
                ],
                "edges": [],
            }
        )
        code = graph_to_code(graph, description='Motor "premium" model')
        # Should compile without error
        compile(code, "<test>", "exec")


# ---------------------------------------------------------------------------
# Template param consistency (B8): all templates use {first} for return value
# ---------------------------------------------------------------------------


class TestTemplateParamConsistency:
    """Templates must use the first param name (not hardcoded 'df') for return."""

    def test_banding_single_applies_config_to_first_param(self):
        """Banding single-factor should apply its config to the first upstream frame."""
        node = _n(
            {
                "id": "b",
                "data": {
                    "label": "Band",
                    "nodeType": "banding",
                    "config": {
                        "factors": [
                            {
                                "banding": "continuous",
                                "column": "age",
                                "outputColumn": "age_factor",
                                "rules": [
                                    {
                                        "op1": ">=",
                                        "val1": 0,
                                        "op2": "<",
                                        "val2": 100,
                                        "assignment": "1.0",
                                    }
                                ],
                            }
                        ],
                    },
                },
            }
        )
        code = _node_to_code(node, source_names=["upstream_data"])
        assert "apply_banding_from_config(upstream_data" in code
        assert "return df" in code
        _compile_node_code(code)

    def test_banding_multi_applies_config_to_first_param(self):
        """Banding multi-factor should apply its config to the first upstream frame."""
        node = _n(
            {
                "id": "b",
                "data": {
                    "label": "MultiBand",
                    "nodeType": "banding",
                    "config": {
                        "factors": [
                            {
                                "banding": "continuous",
                                "column": "age",
                                "outputColumn": "age_f",
                                "rules": [],
                            },
                            {
                                "banding": "discrete",
                                "column": "region",
                                "outputColumn": "region_f",
                                "rules": [],
                            },
                        ],
                    },
                },
            }
        )
        code = _node_to_code(node, source_names=["my_source"])
        assert "apply_banding_from_config(my_source" in code
        assert "return df" in code
        _compile_node_code(code)

    def test_rating_step_applies_config_to_first_param(self):
        """Rating step should apply its config to the first upstream frame."""
        node = _n(
            {
                "id": "rs",
                "data": {
                    "label": "Rate",
                    "nodeType": "ratingStep",
                    "config": {
                        "tables": [
                            {
                                "factors": ["x"],
                                "outputColumn": "f",
                                "entries": [{"x": "a", "value": 1.0}],
                            }
                        ]
                    },
                },
            }
        )
        code = _node_to_code(node, source_names=["input_df"])
        assert "apply_rating_step_from_config(input_df" in code
        assert "return df" in code
        _compile_node_code(code)

    def test_modelling_returns_first_param(self):
        """Modelling should return the first upstream name, not 'df'."""
        node = _n(
            {
                "id": "m",
                "data": {
                    "label": "Train",
                    "nodeType": "modelling",
                    "config": {"target": "loss", "algorithm": "catboost"},
                },
            }
        )
        code = _node_to_code(node, source_names=["features"])
        assert "return features" in code
        assert "return df" not in code
        _compile_node_code(code)

    def test_templates_default_to_df_without_sources(self):
        """Without source names, templates should use 'df' as default param."""
        node = _n(
            {
                "id": "b",
                "data": {
                    "label": "Band",
                    "nodeType": "banding",
                    "config": {
                        "factors": [
                            {
                                "banding": "continuous",
                                "column": "x",
                                "outputColumn": "x_f",
                                "rules": [],
                            }
                        ],
                    },
                },
            }
        )
        code = _node_to_code(node, source_names=[])
        assert "return df" in code
        _compile_node_code(code)


# ---------------------------------------------------------------------------
# B4: JSON/JSONL data source codegen (regression + new behaviour)
# ---------------------------------------------------------------------------


class TestDataInputFormatCodegen:
    """Verify that canonical Data Input codegen uses one registry boundary."""

    def _make_ds_node(self, path: str, label: str = "Source", **extra_config):
        config = _file_input_config(path)
        config.update(extra_config)
        return _n(
            {
                "id": "src",
                "data": {"label": label, "nodeType": "dataInput", "config": config},
            }
        )

    # -- CSV (regression: existing behaviour must not break) ----------------

    def test_csv_uses_scan_csv(self):
        code = _node_to_code(self._make_ds_node("data/file.csv", "CSVSrc"))
        assert "resolve_data_input_from_config" in code
        assert "scan_parquet" not in code
        assert "read_json" not in code
        _compile_node_code(code)

    # -- Parquet (regression: existing behaviour must not break) -------------

    def test_parquet_uses_scan_parquet(self):
        code = _node_to_code(self._make_ds_node("data/file.parquet", "ParqSrc"))
        assert "resolve_data_input_from_config" in code
        assert "scan_csv" not in code
        assert "read_json" not in code
        _compile_node_code(code)

    # -- JSON (new behaviour) -----------------------------------------------

    def test_json_uses_read_json_lazy(self):
        """JSON data source should route through the shared source boundary."""
        code = _node_to_code(self._make_ds_node("data/quotes.json", "JSONSrc"))
        assert "resolve_data_input_from_config" in code
        assert "read_json" not in code
        assert "scan_parquet" not in code
        assert "scan_csv" not in code
        _compile_node_code(code)

    def test_json_produces_valid_python(self):
        """Generated JSON data source code must be parseable by ast.parse."""
        import ast

        code = _node_to_code(self._make_ds_node("data/input.json", "JsonValid"))
        wrapper = (
            f"import polars as pl\nimport haute\npipeline = haute.Pipeline('test')\n\n{code}\n"
        )
        ast.parse(wrapper)

    def test_json_config_path(self):
        """JSON data source should still emit the config= decorator reference."""
        code = _node_to_code(self._make_ds_node("data/input.json", "JsonCfg"))
        assert 'config="config/data_input/JsonCfg.json"' in code

    # -- JSONL (new behaviour) ----------------------------------------------

    def test_jsonl_uses_scan_ndjson(self):
        """JSONL data source should route through the shared source boundary."""
        code = _node_to_code(self._make_ds_node("data/events.jsonl", "JsonlSrc"))
        assert "resolve_data_input_from_config" in code
        assert "scan_parquet" not in code
        assert "scan_csv" not in code
        assert "read_json" not in code
        _compile_node_code(code)

    def test_jsonl_produces_valid_python(self):
        """Generated JSONL data source code must be parseable by ast.parse."""
        import ast

        code = _node_to_code(self._make_ds_node("data/events.jsonl", "JsonlValid"))
        wrapper = (
            f"import polars as pl\nimport haute\npipeline = haute.Pipeline('test')\n\n{code}\n"
        )
        ast.parse(wrapper)

    def test_jsonl_config_path(self):
        """JSONL data source should still emit the config= decorator reference."""
        code = _node_to_code(self._make_ds_node("data/stream.jsonl", "JsonlCfg"))
        assert 'config="config/data_input/JsonlCfg.json"' in code

    # -- Case-insensitive extension matching --------------------------------

    def test_uppercase_json_extension(self):
        """Path with .JSON (uppercase) should still use the JSON template."""
        code = _node_to_code(self._make_ds_node("data/INPUT.JSON", "UpperJson"))
        assert "resolve_data_input_from_config" in code
        assert "read_json" not in code
        assert "scan_parquet" not in code
        _compile_node_code(code)

    def test_uppercase_jsonl_extension(self):
        """Path with .JSONL (uppercase) should still use the JSONL template."""
        code = _node_to_code(self._make_ds_node("data/EVENTS.JSONL", "UpperJsonl"))
        assert "resolve_data_input_from_config" in code
        assert "scan_parquet" not in code
        _compile_node_code(code)

    def test_uppercase_csv_extension(self):
        """Path with .CSV (uppercase) should still use the CSV template."""
        code = _node_to_code(self._make_ds_node("data/FILE.CSV", "UpperCsv"))
        assert "resolve_data_input_from_config" in code
        assert "scan_parquet" not in code
        _compile_node_code(code)

    # -- Paths with dots in directory names ---------------------------------

    def test_json_with_dots_in_directory(self):
        """Dots in parent directory names must not confuse extension detection."""
        code = _node_to_code(self._make_ds_node("data/v2.1/quotes.json", "DotDir"))
        assert "resolve_data_input_from_config" in code
        assert "read_json" not in code
        assert "scan_parquet" not in code
        _compile_node_code(code)

    def test_jsonl_with_dots_in_directory(self):
        """Dots in parent directory names must not confuse extension detection."""
        code = _node_to_code(self._make_ds_node("data/v3.0.beta/events.jsonl", "DotDirL"))
        assert "resolve_data_input_from_config" in code
        assert "scan_parquet" not in code
        _compile_node_code(code)

    def test_parquet_with_dots_in_directory(self):
        """Parquet path with dots in directory should still use scan_parquet."""
        code = _node_to_code(self._make_ds_node("data/v1.2/file.parquet", "DotDirP"))
        assert "resolve_data_input_from_config" in code
        _compile_node_code(code)

    # -- Consistency with _io.read_source -----------------------------------

    @pytest.mark.parametrize(
        "ext",
        [
            ".csv",
            ".json",
            ".jsonl",
            ".parquet",
        ],
        ids=["csv", "json", "jsonl", "parquet"],
    )
    def test_codegen_uses_shared_source_boundary(self, ext):
        """Codegen must use the same source boundary as runtime execution."""
        path = f"data/file{ext}"
        code = _node_to_code(self._make_ds_node(path, f"Src{ext.strip('.')}"))
        assert "resolve_data_input_from_config" in code
        assert "read_json" not in code
        _compile_node_code(code)

    # -- Unknown extension fails through the shared source boundary ----------

    def test_unknown_extension_uses_shared_source_boundary(self):
        """An unrecognised extension should fail at the shared source boundary."""
        code = _node_to_code(self._make_ds_node("data/file.feather", "FeatherSrc"))
        assert "resolve_data_input_from_config" in code
        assert "scan_parquet" not in code
        _compile_node_code(code)

    # -- Full graph integration with JSON/JSONL data sources ----------------

    def test_json_data_source_in_full_graph(self):
        """A graph with a JSON data source compiles end-to-end."""
        graph = _g(
            {
                "nodes": [
                    {
                        "id": "s",
                        "data": {
                            "label": "JsonData",
                            "nodeType": "dataInput",
                            "config": _file_input_config("data.json"),
                        },
                    },
                    {
                        "id": "t",
                        "data": {
                            "label": "Clean",
                            "nodeType": "polars",
                            "config": {"code": "df = JsonData.drop_nulls()"},
                        },
                    },
                ],
                "edges": [{"id": "e1", "source": "s", "target": "t"}],
            }
        )
        code = graph_to_code(graph)
        assert "resolve_data_input_from_config" in code
        assert "read_json" not in code
        assert "def Clean(JsonData: pl.LazyFrame)" in code
        compile(code, "<test>", "exec")

    def test_jsonl_data_source_in_full_graph(self):
        """A graph with a JSONL data source compiles end-to-end."""
        graph = _g(
            {
                "nodes": [
                    {
                        "id": "s",
                        "data": {
                            "label": "EventLog",
                            "nodeType": "dataInput",
                            "config": _file_input_config("events.jsonl"),
                        },
                    },
                    {
                        "id": "t",
                        "data": {
                            "label": "Filter",
                            "nodeType": "polars",
                            "config": {"code": "df = EventLog.filter(pl.col('x') > 0)"},
                        },
                    },
                ],
                "edges": [{"id": "e1", "source": "s", "target": "t"}],
            }
        )
        code = graph_to_code(graph)
        assert "resolve_data_input_from_config" in code
        assert "scan_ndjson" not in code
        assert "def Filter(EventLog: pl.LazyFrame)" in code
        compile(code, "<test>", "exec")

    # -- Additional edge cases ----------------------------------------------

    def test_ndjson_extension_falls_through_to_parquet(self):
        """.ndjson is NOT a supported user-facing extension — falls through to parquet."""
        code = _node_to_code(self._make_ds_node("data/events.ndjson", "NdjsonSrc"))
        assert "resolve_data_input_from_config" in code
        assert "scan_parquet" not in code
        assert "scan_ndjson" not in code
        _compile_node_code(code)

    def test_empty_path_falls_through_to_parquet(self):
        """Empty path string should fall through to parquet template."""
        code = _node_to_code(self._make_ds_node("", "EmptyPath"))
        assert "resolve_data_input_from_config" in code
        assert "scan_parquet" not in code
        _compile_node_code(code)

    def test_no_extension_falls_through_to_parquet(self):
        """Path with no extension should fall through to parquet template."""
        code = _node_to_code(self._make_ds_node("data/noext", "NoExt"))
        assert "resolve_data_input_from_config" in code
        assert "scan_parquet" not in code
        _compile_node_code(code)

    def test_mixed_case_json_extension(self):
        """Path with .Json (mixed case) should use the JSON template."""
        code = _node_to_code(self._make_ds_node("data/file.Json", "MixedJson"))
        assert "resolve_data_input_from_config" in code
        assert "read_json" not in code
        _compile_node_code(code)

    def test_mixed_case_parquet_extension(self):
        """Path with .Parquet (mixed case) should use the parquet template."""
        code = _node_to_code(self._make_ds_node("data/file.Parquet", "MixedPq"))
        assert "resolve_data_input_from_config" in code
        assert "scan_parquet" not in code
        _compile_node_code(code)


# ---------------------------------------------------------------------------
# API input codegen: case-insensitive extension matching
# ---------------------------------------------------------------------------


class TestApiInputCodegen:
    """API input codegen delegates every current sidecar to one runtime helper."""

    def _make_api_node(self, path: str, label: str = "Input"):
        return _n(
            {
                "id": "inp",
                "data": {"label": label, "nodeType": "apiInput", "config": {"path": path}},
            }
        )

    def test_json_api_input(self):
        code = _node_to_code(self._make_api_node("input.json", "JsonIn"))
        assert "resolve_api_input_from_config" in code
        assert "input.json" not in code
        assert "api_input=True" not in code  # replaced by config= ref
        _compile_node_code(code)

    def test_jsonl_api_input(self):
        code = _node_to_code(self._make_api_node("input.jsonl", "JsonlIn"))
        assert "resolve_api_input_from_config" in code
        assert "input.jsonl" not in code
        _compile_node_code(code)

    def test_csv_api_input(self):
        code = _node_to_code(self._make_api_node("input.csv", "CsvIn"))
        assert "resolve_api_input_from_config" in code
        assert "input.csv" not in code
        _compile_node_code(code)

    def test_uppercase_json_api_input(self):
        """Runtime sidecar resolution owns case-insensitive format dispatch."""
        code = _node_to_code(self._make_api_node("input.JSON", "UpperIn"))
        assert "resolve_api_input_from_config" in code
        assert "input.JSON" not in code
        _compile_node_code(code)

    def test_uppercase_csv_api_input(self):
        """The generated body never bakes the current extension decision."""
        code = _node_to_code(self._make_api_node("input.CSV", "UpperCsv"))
        assert "resolve_api_input_from_config" in code
        assert "input.CSV" not in code
        _compile_node_code(code)

    def test_parquet_api_input(self):
        code = _node_to_code(self._make_api_node("input.parquet", "PqIn"))
        assert "resolve_api_input_from_config" in code
        assert "input.parquet" not in code
        _compile_node_code(code)

    def test_retained_api_input_code_has_no_escaped_path_artifact(self):
        template = _retained_api_input_template("config/custom/quotes.json")
        assert not template.startswith("\\")
        assert '"config/custom/quotes.json"' in template


class TestPreservedBlocksRoundTrip:
    def test_module_block_is_not_preamble_and_reaches_source_fixpoint(self):
        from haute.parser import parse_pipeline_source

        source = """\
import polars as pl
import haute

# haute:preserve-start
VALUE = 7
# haute:preserve-end

pipeline = haute.Pipeline("main")

@pipeline.polars
def transform(df: pl.LazyFrame) -> pl.LazyFrame:
    df = df.with_columns(pl.lit(1).alias("one"))
    return df
"""
        first_graph = parse_pipeline_source(source)
        assert "VALUE = 7" not in (first_graph.preamble or "")
        assert first_graph.preserved_blocks == ["VALUE = 7"]

        first_code = graph_to_code(first_graph)
        assert first_code.count("VALUE = 7") == 1
        second_code = graph_to_code(parse_pipeline_source(first_code))
        assert second_code == first_code

    def test_path_alias_without_generated_config_assignment_remains_preamble(self):
        from haute.parser import parse_pipeline_source

        source = """\
import polars as pl
import haute

from pathlib import Path as _HautePath
USER_PATH = _HautePath("data")

pipeline = haute.Pipeline("main")

@pipeline.polars
def transform(df: pl.LazyFrame) -> pl.LazyFrame:
    return df
"""

        graph = parse_pipeline_source(source)
        preamble = graph.preamble or ""

        assert "from pathlib import Path as _HautePath" in preamble
        assert 'USER_PATH = _HautePath("data")' in preamble

    def test_config_scaffold_never_becomes_preamble_or_source_user_code(
        self,
        haute_scratch,
    ):
        import json

        import polars as pl

        from haute.executor import execute_graph
        from haute.parser import parse_pipeline_source

        data_dir = haute_scratch / "data"
        data_dir.mkdir()
        pl.DataFrame({"value": [7]}).write_parquet(data_dir / "input.parquet")
        config_dir = haute_scratch / "config" / "data_input"
        config_dir.mkdir(parents=True)
        config_dir.joinpath("input_data.json").write_text(
            json.dumps(_file_input_config("data/input.parquet")),
            encoding="utf-8",
        )

        source = '''\
"""Pipeline: main"""

from pathlib import Path as _HautePath

import haute
import polars as pl

_HAUTE_CONFIG_BASE = _HautePath(__file__).resolve().parent

pipeline = haute.Pipeline("main")

@pipeline.data_input(config="config/data_input/input_data.json")
def input_data() -> pl.LazyFrame:
    from haute._project import get_project_root
    from haute.graph_utils import resolve_data_input_from_config
    project_root = get_project_root(_HAUTE_CONFIG_BASE)
    df = resolve_data_input_from_config(
        "config/data_input/input_data.json",
        base_dir=_HAUTE_CONFIG_BASE,
        project_root=project_root,
    )
    return df
'''
        source_file = str(haute_scratch / "main.py")
        parsed = parse_pipeline_source(
            source,
            source_file=source_file,
            _base_dir=haute_scratch,
        )
        input_node = parsed.nodes[0]

        assert not (parsed.preamble or "").strip()
        assert not str(input_node.data.config.get("code") or "").strip()
        result = execute_graph(parsed, target_node_id=input_node.id)[input_node.id]
        assert result.status == "ok", result.error
        assert result.row_count == 1

        regenerated = graph_to_code(parsed)
        assert regenerated.count("_HAUTE_CONFIG_BASE =") == 1
        assert regenerated.index("from pathlib import Path as _HautePath") < regenerated.index(
            "import haute"
        )
        assert regenerated.index("pipeline = haute.Pipeline") < regenerated.index(
            "_HAUTE_CONFIG_BASE ="
        )
        reparsed = parse_pipeline_source(
            regenerated,
            source_file=source_file,
            _base_dir=haute_scratch,
        )
        assert graph_to_code(reparsed) == regenerated

    def test_indented_preserve_marker_stays_in_decorated_node_code(self):
        from haute.parser import parse_pipeline_source

        source = """\
import polars as pl
import haute

pipeline = haute.Pipeline("main")

@pipeline.polars
def transform(df: pl.LazyFrame) -> pl.LazyFrame:
    # haute:preserve-start
    marker = 1
    # haute:preserve-end
    return df
"""
        graph = parse_pipeline_source(source)
        assert graph.preserved_blocks == []
        code = graph_to_code(graph)
        assert "    marker = 1" in code
        compile(code, "<test>", "exec")

    def test_identical_node_labels_raise_parse_error(self):
        graph = _g(
            {
                "nodes": [
                    {
                        "id": "one",
                        "data": {
                            "label": "Duplicate",
                            "nodeType": "polars",
                            "config": {"code": "return df"},
                        },
                    },
                    {
                        "id": "two",
                        "data": {
                            "label": "Duplicate",
                            "nodeType": "polars",
                            "config": {"code": "return df"},
                        },
                    },
                ],
                "edges": [],
            }
        )
        with pytest.raises(ParseError):
            graph_to_code(graph)


# ---------------------------------------------------------------------------
# _make_passthrough_builder factory + all four passthrough node types
# ---------------------------------------------------------------------------


class TestPassthroughAndBehaviouralCodegen:
    """Integration tests for the scenario_expander / optimiser / optimiser_apply
    / modelling codegen builders.

    optimiser and modelling are genuine passthroughs (their bodies return the
    first frame); scenario_expander and optimiser_apply are behavioural — their
    bodies route through a shared ``*_from_config`` helper so a standalone
    ``pipeline.run()`` applies the real transform instead of silently no-oping.
    """

    #: node types whose codegen body is a genuine first-frame passthrough
    _PASSTHROUGH_TYPES = {"optimiser", "modelling"}

    # -- integration tests for the four registered builders ------------------

    @pytest.mark.parametrize(
        "node_type, decorator_name, config_key_sample, config_folder",
        [
            pytest.param(
                "scenarioExpander",
                "scenario_expander",
                {"quote_id": "qid", "column_name": "col1"},
                "expander",
                id="scenario_expander",
            ),
            pytest.param(
                "optimiser",
                "optimiser",
                {"mode": "minimize", "tolerance": 0.01},
                "optimisation",
                id="optimiser",
            ),
            pytest.param(
                "optimiserApply",
                "optimiser_apply",
                {"sourceType": "file", "artifact_path": "models/opt", "version": "3"},
                "apply_optimisation",
                id="optimiser_apply",
            ),
            pytest.param(
                "modelling",
                "modelling",
                {"target": "loss_ratio", "algorithm": "catboost"},
                "model_training",
                id="modelling",
            ),
        ],
    )
    def test_passthrough_node_basic(
        self,
        node_type,
        decorator_name,
        config_key_sample,
        config_folder,
    ):
        """Each passthrough builder generates code with the correct type-specific
        decorator, config kwargs, and a passthrough return statement."""
        node = _n(
            {
                "id": "n1",
                "data": {
                    "label": "My Step",
                    "nodeType": node_type,
                    "config": config_key_sample,
                },
            }
        )
        # _generate_node_code preserves the inline decorator (pre-config rewrite)
        raw_code = _generate_node_code(node, source_names=["upstream"])
        assert f"@pipeline.{decorator_name}(" in raw_code
        for key, val in config_key_sample.items():
            assert f"{key}={val!r}" in raw_code
        assert "def My_Step(upstream: pl.LazyFrame)" in raw_code
        if node_type in self._PASSTHROUGH_TYPES:
            assert "return upstream" in raw_code
        else:
            # Behavioural nodes must NOT emit a bare passthrough body.
            assert "return upstream\n" not in raw_code
            assert "_from_config(" in raw_code

        # _node_to_code replaces decorator with config= path
        final_code = _node_to_code(node, source_names=["upstream"])
        assert f'config="config/{config_folder}/My_Step.json"' in final_code
        _compile_node_code(final_code)

    @pytest.mark.parametrize(
        "node_type, decorator_name",
        [
            ("scenarioExpander", "scenario_expander"),
            ("optimiser", "optimiser"),
            ("optimiserApply", "optimiser_apply"),
            ("modelling", "modelling"),
        ],
    )
    def test_passthrough_node_empty_config(self, node_type, decorator_name):
        """Passthrough builders work correctly with an empty config dict."""
        node = _n(
            {
                "id": "n1",
                "data": {
                    "label": "Empty",
                    "nodeType": node_type,
                    "config": {},
                },
            }
        )
        raw_code = _generate_node_code(node, source_names=[])
        assert f"@pipeline.{decorator_name}(" in raw_code
        assert "def Empty(df: pl.LazyFrame)" in raw_code
        if node_type in self._PASSTHROUGH_TYPES:
            assert "return df" in raw_code
        else:
            assert "return df\n" not in raw_code
            assert "_from_config(" in raw_code

        final_code = _node_to_code(node, source_names=[])
        _compile_node_code(final_code)

    @pytest.mark.parametrize(
        "node_type",
        ["scenarioExpander", "optimiser", "optimiserApply", "modelling"],
    )
    def test_passthrough_node_multi_source(self, node_type):
        """Passthrough builders handle multiple upstream sources correctly."""
        node = _n(
            {
                "id": "n1",
                "data": {
                    "label": "Merge",
                    "nodeType": node_type,
                    "config": {"data_input": "left"} if node_type == "optimiser" else {},
                },
            }
        )
        code = _node_to_code(node, source_names=["left", "right"])
        assert "def Merge(left: pl.LazyFrame, right: pl.LazyFrame)" in code
        if node_type in self._PASSTHROUGH_TYPES:
            assert "return left" in code
        else:
            assert "_from_config(" in code
        _compile_node_code(code)


# ---------------------------------------------------------------------------
# E10: Missing codegen builder fails loudly (no silent fallback)
# ---------------------------------------------------------------------------


class TestUnknownNodeTypeFallback:
    """Post-Package-4B: codegen dispatch reads a unified registry that must
    have an entry for every ``NodeType``.  A missing entry is a registration
    bug and raises ``KeyError`` — the old silent fallback to ``_gen_transform``
    was removed because it hid misregistered types (CLAUDE.md: fail loudly)."""

    def test_missing_codegen_builder_raises_keyerror(self) -> None:
        """Temporarily evict a NodeType from the registry to simulate a
        missing registration; dispatch must raise KeyError identifying the
        offending NodeType, not silently emit transform code."""
        from haute._registry import NODE_REGISTRY
        from haute._types import NodeType

        node = _n(
            {
                "id": "n_unknown",
                "data": {
                    "label": "Mystery",
                    "nodeType": "banding",
                    "config": {"code": ""},
                },
            }
        )

        entry = NODE_REGISTRY[NodeType.BANDING]
        saved = entry.codegen
        entry.codegen = None
        try:
            with pytest.raises(KeyError, match="banding"):
                _node_to_code(node, source_names=["src"])
        finally:
            entry.codegen = saved


# ---------------------------------------------------------------------------
# Gap 1: Missing codegen builder — dispatch raises instead of silently
#       generating a transform.  Deployed pipelines with future node types
#       must fail at codegen time, not at import time of the generated file.
# ---------------------------------------------------------------------------


class TestUnknownNodeTypeFallbackCode:
    """The old fallback-to-transform was a silent workaround for the two-table
    drift era.  With a unified registry and one entry per NodeType, a missing
    codegen builder is a registration bug; raise immediately."""

    def test_missing_builder_raises_with_code(self):
        """Missing registration raises even when user code is provided."""
        from haute._registry import NODE_REGISTRY
        from haute._types import NodeType

        node = _n(
            {
                "id": "u1",
                "data": {
                    "label": "FutureNode",
                    "nodeType": "banding",
                    "config": {"code": "df = upstream.filter(pl.col('x') > 0)"},
                },
            }
        )
        entry = NODE_REGISTRY[NodeType.BANDING]
        saved = entry.codegen
        entry.codegen = None
        try:
            with pytest.raises(KeyError, match="banding"):
                _generate_node_code(node, source_names=["upstream"])
        finally:
            entry.codegen = saved

    def test_missing_builder_raises_without_code(self):
        """Missing registration raises regardless of user code presence."""
        from haute._registry import NODE_REGISTRY
        from haute._types import NodeType

        node = _n(
            {
                "id": "u2",
                "data": {
                    "label": "Empty",
                    "nodeType": "banding",
                    "config": {},
                },
            }
        )
        entry = NODE_REGISTRY[NodeType.BANDING]
        saved = entry.codegen
        entry.codegen = None
        try:
            with pytest.raises(KeyError, match="banding"):
                _generate_node_code(node, source_names=["src"])
        finally:
            entry.codegen = saved


# ---------------------------------------------------------------------------
# Gap 2: _wrap_user_code simple indent + return df behavior
# ---------------------------------------------------------------------------


class TestWrapUserCodeIndentBehavior:
    """Verify _wrap_user_code indents code, prepends df alias, and appends return df."""

    def test_no_alias_prepended_when_source_not_df(self):
        """No preamble is added — user code is responsible for defining df."""
        result = _wrap_user_code("df = src.filter(pl.col('x') > 0)", ["src"])
        assert result == "    df = src.filter(pl.col('x') > 0)\n    return df"

    def test_no_alias_when_source_is_df(self):
        """When first source is 'df', no preamble is added."""
        result = _wrap_user_code("df = df.filter(pl.col('x') > 0)", ["df"])
        assert result == "    df = df.filter(pl.col('x') > 0)\n    return df"

    def test_multiline_all_lines_indented(self):
        """Each line of multiline code gets 4-space indent."""
        code = "a = 1\nb = 2\ndf = a + b"
        result = _wrap_user_code(code, ["df"])
        assert result == "    a = 1\n    b = 2\n    df = a + b\n    return df"

    def test_strips_leading_trailing_whitespace(self):
        """Leading/trailing whitespace in the code is stripped before indenting."""
        result = _wrap_user_code("  df = src.filter(pl.col('x') > 0)  \n", ["src"])
        assert result == "    df = src.filter(pl.col('x') > 0)\n    return df"


# ---------------------------------------------------------------------------
# Gap 3: _sanitize_description triple-quote edge cases
# ---------------------------------------------------------------------------


class TestSanitizeDescription:
    """Descriptions containing triple quotes, trailing backslashes, or
    trailing double-quotes must not break the generated docstring.

    Post Wave 9D #122: the sanitiser backslash-escapes every ``\\`` and
    every ``"`` and prepends a leading ``\\n`` when needed to neutralise
    ``inspect.cleandoc``'s indent-stripping.  The result is that
    ``\"\"\"{sanitize(desc)}\"\"\"`` is always valid Python AND
    round-trips via ``ast.get_docstring`` for arbitrary user input.
    """

    @staticmethod
    def _assert_roundtrip(description: str) -> None:
        result = _sanitize_description(description)
        code = f'def f():\n    """{result}"""\n    pass'
        compile(code, "<test>", "exec")
        tree = ast.parse(code)
        assert (ast.get_docstring(tree.body[0]) or "") == description, (
            f"round-trip failed for {description!r}: docstring={ast.get_docstring(tree.body[0])!r}"
        )

    def test_triple_quotes_escaped(self):
        """Triple quotes inside description never appear as a run in output."""
        result = _sanitize_description('hello """world"""')
        assert '"""' not in result
        self._assert_roundtrip('hello """world"""')

    def test_trailing_double_quote(self):
        self._assert_roundtrip('ends with quote"')

    def test_trailing_multiple_double_quotes(self):
        self._assert_roundtrip('danger""')

    def test_trailing_backslash(self):
        self._assert_roundtrip("ends with backslash\\")

    def test_trailing_backslash_before_quotes(self):
        self._assert_roundtrip('backslash then quote\\"')

    def test_only_triple_quotes(self):
        result = _sanitize_description('"""')
        assert '"""' not in result
        self._assert_roundtrip('"""')

    def test_empty_string(self):
        result = _sanitize_description("")
        assert result == ""

    def test_normal_text_unchanged(self):
        result = _sanitize_description("Simple description")
        assert result == "Simple description"


# ---------------------------------------------------------------------------
# Gap 4: Instance code generation with missing instanceOf target
# ---------------------------------------------------------------------------


class TestInstanceMissingTarget:
    """Catch: if the ``instanceOf`` target node is not in the graph, the
    generated instance code would reference an undefined function, causing
    a NameError at pipeline execution time."""

    def test_instance_with_missing_original_still_compiles(self):
        """Instance node whose instanceOf target is absent from graph."""
        instance_node = _n(
            {
                "id": "inst1",
                "data": {
                    "label": "ClonedStep",
                    "nodeType": "polars",
                    "config": {"instanceOf": "ghost_node_id"},
                },
            }
        )
        # _instance_to_code takes the original func name directly - if lookup
        # falls back to the raw ID, the code should still compile syntactically.
        code = _instance_to_code(
            instance_node,
            original_func_name="ghost_node_id",
            source_names=["upstream"],
        )
        assert "def ClonedStep(" in code
        assert 'of="ghost_node_id"' in code
        assert "return ghost_node_id(" in code
        _compile_node_code(code)

    def test_instance_in_graph_with_missing_target_node(self):
        """Full graph where instanceOf references a node ID not in the graph."""
        graph = _g(
            {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "Source",
                            "nodeType": "dataInput",
                            "config": {"path": "d.parquet"},
                        },
                    },
                    {
                        "id": "inst",
                        "data": {
                            "label": "Clone",
                            "nodeType": "polars",
                            "config": {"instanceOf": "deleted_node"},
                        },
                    },
                ],
                "edges": [{"id": "e1", "source": "src", "target": "inst"}],
            }
        )
        # Should not crash; the instance references a missing node
        code = graph_to_code(graph)
        compile(code, "<test>", "exec")


# ---------------------------------------------------------------------------
# Instance input mapping: ambiguous substring pairing fails the save loudly
# ---------------------------------------------------------------------------


class TestInstanceAmbiguousMapping:
    """An instance whose upstream names pair ambiguously with the original's
    parameters must fail codegen (and therefore save) with ``ConfigError``,
    not silently bind frames to the wrong parameters. ``Rate`` is a
    substring of both ``X_Base_Rate`` and ``X_Rate``, so name matching
    cannot decide which frame feeds which parameter."""

    @staticmethod
    def _ambiguous_graph(input_mapping: dict[str, str] | None = None):
        def src(node_id: str, label: str) -> dict:
            return {
                "id": node_id,
                "data": {
                    "label": label,
                    "nodeType": "dataInput",
                    "config": {"path": f"{node_id}.parquet"},
                },
            }

        inst_config: dict = {"instanceOf": "orig"}
        if input_mapping is not None:
            inst_config["inputMapping"] = input_mapping
        return _g(
            {
                "nodes": [
                    src("r", "Rate"),
                    src("br", "Base Rate"),
                    src("xr", "X Rate"),
                    src("xbr", "X Base Rate"),
                    {
                        "id": "orig",
                        "data": {
                            "label": "Blend",
                            "nodeType": "polars",
                            "config": {"code": "df = Rate"},
                        },
                    },
                    {
                        "id": "inst",
                        "data": {
                            "label": "Blend Clone",
                            "nodeType": "polars",
                            "config": inst_config,
                        },
                    },
                ],
                "edges": [
                    {"id": "e1", "source": "r", "target": "orig"},
                    {"id": "e2", "source": "br", "target": "orig"},
                    {"id": "e3", "source": "xbr", "target": "inst"},
                    {"id": "e4", "source": "xr", "target": "inst"},
                ],
            }
        )

    def test_ambiguous_instance_mapping_fails_codegen(self):
        from haute.errors import ConfigError

        with pytest.raises(ConfigError) as exc_info:
            graph_to_code(self._ambiguous_graph())
        assert "ambiguous" in str(exc_info.value)

    def test_explicit_mapping_unblocks_codegen(self):
        code = graph_to_code(self._ambiguous_graph({"Rate": "X_Rate", "Base_Rate": "X_Base_Rate"}))
        compile(code, "<test>", "exec")
        assert (
            '@pipeline.instance(of="Blend", '
            "inputMapping={'Rate': 'X_Rate', 'Base_Rate': 'X_Base_Rate'})"
        ) in code
        assert "Rate=X_Rate" in code
        assert "Base_Rate=X_Base_Rate" in code


# ---------------------------------------------------------------------------
# Gap 5: _submodel_node_to_code replaces only first @pipeline. occurrence
# ---------------------------------------------------------------------------


class TestSubmodelPipelineReplacement:
    """Catch: ``.replace("@pipeline.", "@submodel.", 1)`` only replaces the
    first occurrence. If user code in a comment or string literal contains
    ``@pipeline.``, it stays as ``@pipeline.`` which is misleading but not
    a syntax error. The decorator prefix is correctly replaced."""

    def test_decorator_replaced_but_comment_preserved(self):
        """A node whose generated code contains @pipeline. in a comment."""
        node = _n(
            {
                "id": "s1",
                "data": {
                    "label": "Step",
                    "nodeType": "polars",
                    "config": {"code": "# see @pipeline.polars docs\ndf = src.drop_nulls()"},
                },
            }
        )
        code = _submodel_node_to_code(node, source_names=["src"])
        # Decorator line must use @submodel.
        assert "@submodel.polars" in code
        # The comment still has @pipeline. because replace(..., 1) only hits first
        assert "@pipeline.polars docs" in code

    def test_decorator_is_always_first_replacement(self):
        """Even with code that mentions @pipeline, the decorator is what gets replaced."""
        node = _n(
            {
                "id": "s2",
                "data": {
                    "label": "Clean",
                    "nodeType": "polars",
                    "config": {"code": ""},
                },
            }
        )
        code = _submodel_node_to_code(node, source_names=["src"])
        lines = code.strip().split("\n")
        assert lines[0].startswith("@submodel.polars")


# ---------------------------------------------------------------------------
# Gap 6: _build_extra_kwargs edge cases — falsy but valid values
# ---------------------------------------------------------------------------


class TestBuildExtraKwargsEdgeCases:
    """Catch: ``_build_extra_kwargs`` skips None, "", and []. But 0, False,
    and {} are falsy values that should NOT be skipped. If they were skipped,
    config like ``tolerance=0`` or ``enabled=False`` would silently vanish
    from generated decorators."""

    def test_zero_is_included(self):
        """0 is falsy but is a valid config value — must not be skipped."""
        parts = _build_extra_kwargs({"tolerance": 0}, ("tolerance",))
        assert parts == ["tolerance=0"]

    def test_false_is_included(self):
        """False is falsy but is a valid config value — must not be skipped."""
        parts = _build_extra_kwargs({"enabled": False}, ("enabled",))
        assert parts == ["enabled=False"]

    def test_empty_dict_is_included(self):
        """{} is falsy but is a valid config value — must not be skipped."""
        parts = _build_extra_kwargs({"mapping": {}}, ("mapping",))
        assert parts == ["mapping={}"]

    def test_none_is_skipped(self):
        parts = _build_extra_kwargs({"x": None}, ("x",))
        assert parts == []

    def test_empty_string_is_skipped(self):
        parts = _build_extra_kwargs({"x": ""}, ("x",))
        assert parts == []

    def test_empty_list_is_skipped(self):
        parts = _build_extra_kwargs({"x": []}, ("x",))
        assert parts == []

    def test_missing_key_is_skipped(self):
        parts = _build_extra_kwargs({}, ("x",))
        assert parts == []

    def test_mixed_values(self):
        """Only None, '', and [] are skipped — everything else passes through."""
        config = {
            "a": None,
            "b": "",
            "c": [],
            "d": 0,
            "e": False,
            "f": "real",
        }
        parts = _build_extra_kwargs(config, ("a", "b", "c", "d", "e", "f"))
        assert len(parts) == 3
        assert "d=0" in parts
        assert "e=False" in parts
        assert "f='real'" in parts


# ---------------------------------------------------------------------------
# Gap 7: Connect deduplication in multi-submodel mode
# ---------------------------------------------------------------------------


class TestCanonicalBindingRejection:
    """Codegen refuses malformed or over-bound public-port bindings at save."""

    @staticmethod
    def _instance_graph(edges: list[dict]):
        return _g(
            {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "Source",
                            "nodeType": "dataInput",
                            "config": {"path": "d.parquet"},
                        },
                    },
                    {
                        "id": "instance_sm1",
                        "type": "submodel",
                        "data": {
                            "label": "sm1",
                            "nodeType": "submodel",
                            "config": {"definitionId": "sm1", "alias": "sm1"},
                        },
                    },
                ],
                "edges": edges,
                "submodels": {
                    "sm1": {
                        "definitionId": "sm1",
                        "file": "modules/sm1.py",
                        "graph": {
                            "nodes": [
                                {
                                    "id": "child_a",
                                    "data": {"label": "ChildA", "nodeType": "polars", "config": {}},
                                },
                            ],
                            "edges": [],
                        },
                        "inputPorts": [
                            {
                                "portId": "records",
                                "label": "Records",
                                "targets": [{"nodeId": "child_a", "handleId": None}],
                            }
                        ],
                        "outputPorts": [
                            {
                                "portId": "scored",
                                "label": "Scored",
                                "source": {"nodeId": "child_a", "handleId": None},
                            }
                        ],
                    },
                },
            }
        )

    def test_double_binding_one_public_input_port_cannot_be_saved(self):
        graph = self._instance_graph(
            [
                {
                    "id": "e1",
                    "source": "src",
                    "target": "instance_sm1",
                    "targetHandle": "in__records",
                },
                {
                    "id": "e2",
                    "source": "src",
                    "target": "instance_sm1",
                    "targetHandle": "in__records",
                    "sourceHandle": "frame_b",
                },
            ]
        )

        with pytest.raises(ParseError, match="bound more than once") as exc_info:
            graph_to_code_multi(graph, pipeline_name="main")

        assert "records" in str(exc_info.value)

    def test_unassigned_input_draft_cannot_be_saved(self):
        graph = self._instance_graph(
            [
                {
                    "id": "unassigned",
                    "source": "src",
                    "target": "instance_sm1",
                    "targetHandle": None,
                }
            ]
        )

        with pytest.raises(ParseError, match="public-port handle"):
            graph_to_code_multi(graph, pipeline_name="main")

    def test_definition_child_ids_are_never_parent_endpoints(self):
        graph = self._instance_graph(
            [
                {
                    "id": "direct-child-edge",
                    "source": "src",
                    "target": "child_a",
                }
            ]
        )

        with pytest.raises(ParseError):
            graph_to_code_multi(graph, pipeline_name="main")


class TestDeclaredSubmodelOutputs:
    @staticmethod
    def _graph(output_ports: list[dict[str, object]]):
        return _g(
            {
                "nodes": [
                    {
                        "id": "instance_sm1",
                        "type": "submodel",
                        "data": {
                            "label": "sm1",
                            "nodeType": "submodel",
                            "config": {"definitionId": "sm1", "alias": "sm1"},
                        },
                    }
                ],
                "edges": [],
                "submodels": {
                    "sm1": {
                        "definitionId": "sm1",
                        "file": "modules/sm1.py",
                        "inputPorts": [],
                        "outputPorts": output_ports,
                        "graph": {
                            "nodes": [
                                {
                                    "id": "child_export",
                                    "data": {
                                        "label": "child_export",
                                        "nodeType": "dataInput",
                                        "config": {"path": "d.parquet"},
                                    },
                                },
                            ],
                            "edges": [],
                        },
                    },
                },
            }
        )

    def test_unused_output_is_emitted_on_submodel_constructor(self):
        files = graph_to_code_multi(
            self._graph(
                [
                    {
                        "portId": "export",
                        "label": "Export",
                        "source": {"nodeId": "child_export"},
                    }
                ]
            ),
            pipeline_name="main",
        )

        assert (
            "output_ports=[{'portId': 'export', 'label': 'Export', "
            "'source': {'nodeId': 'child_export', 'handleId': None}}]" in files["modules/sm1.py"]
        )
        assert "pipeline.connect" not in files["main.py"]

    @pytest.mark.parametrize(
        ("ports", "error"),
        [
            (
                [{"portId": "export", "label": "Export", "source": {"nodeId": "missing"}}],
                "missing child",
            ),
            (
                [
                    {"portId": "export", "label": "Export", "source": {"nodeId": "child_export"}},
                    {
                        "portId": "export",
                        "label": "Duplicate",
                        "source": {"nodeId": "child_export"},
                    },
                ],
                "duplicate public port",
            ),
        ],
    )
    def test_invalid_declared_outputs_fail_loudly(self, ports, error):
        with pytest.raises(ValueError, match=error):
            self._graph(ports)


# ---------------------------------------------------------------------------
# Gap 8: Special characters in labels — quotes, newlines, unicode
# ---------------------------------------------------------------------------


class TestSpecialCharacterLabels:
    """Catch: labels with quotes or unusual chars could produce invalid
    Python identifiers or break string literals in decorators/connect calls."""

    def test_label_with_single_quotes(self):
        node = _n(
            {
                "id": "q",
                "data": {"label": "it's a node", "nodeType": "polars", "config": {}},
            }
        )
        code = _node_to_code(node, source_names=["upstream"])
        _compile_node_code(code)
        assert "def " in code

    def test_label_with_double_quotes(self):
        node = _n(
            {
                "id": "q",
                "data": {"label": 'say "hello"', "nodeType": "polars", "config": {}},
            }
        )
        code = _node_to_code(node, source_names=["upstream"])
        _compile_node_code(code)

    def test_label_with_newline(self):
        """Newlines in labels would break function def syntax."""
        node = _n(
            {
                "id": "nl",
                "data": {"label": "line1\nline2", "nodeType": "polars", "config": {}},
            }
        )
        code = _node_to_code(node, source_names=["upstream"])
        _compile_node_code(code)

    def test_label_with_unicode_emoji(self):
        node = _n(
            {
                "id": "em",
                "data": {"label": "price_update_\u2705", "nodeType": "polars", "config": {}},
            }
        )
        code = _node_to_code(node, source_names=["upstream"])
        _compile_node_code(code)

    def test_label_all_special_chars(self):
        """Label made entirely of special chars should still produce a valid identifier."""
        node = _n(
            {
                "id": "sp",
                "data": {"label": "!@#$%", "nodeType": "polars", "config": {}},
            }
        )
        code = _node_to_code(node, source_names=["upstream"])
        # Must have a def with some valid identifier
        assert "def " in code
        _compile_node_code(code)

    def test_connect_with_sanitized_labels(self):
        """Graph connect calls must use sanitized names matching function defs."""
        graph = _g(
            {
                "nodes": [
                    {
                        "id": "a",
                        "data": {
                            "label": "My Source (v2)",
                            "nodeType": "dataInput",
                            "config": {"path": "d.parquet"},
                        },
                    },
                    {
                        "id": "b",
                        "data": {"label": "Clean & Filter!", "nodeType": "polars", "config": {}},
                    },
                ],
                "edges": [{"id": "e1", "source": "a", "target": "b"}],
            }
        )
        code = graph_to_code(graph)
        compile(code, "<test>", "exec")
        # The connect call and the def must use the same sanitized name
        assert "pipeline.connect(" in code


# ---------------------------------------------------------------------------
# Gap 9: Very long user code — performance / correctness test
# ---------------------------------------------------------------------------


class TestVeryLongUserCode:
    """Catch: extremely large code blocks could trigger performance issues
    in string operations (splitlines, join, indent) or exceed Python's
    compile limits."""

    def test_large_assignment_code_block_in_node(self):
        """1000 lines of assignment-style code should still produce compilable code."""
        lines = [f"df = df.with_columns(pl.lit(1).alias('col_{i}'))" for i in range(1000)]
        code_block = "\n".join(lines)
        node = _n(
            {
                "id": "big",
                "data": {
                    "label": "BigTransform",
                    "nodeType": "polars",
                    "config": {"code": code_block},
                },
            }
        )
        code = _node_to_code(node, source_names=["src"])
        assert "def BigTransform(src: pl.LazyFrame)" in code
        assert "return df" in code
        _compile_node_code(code)

    def test_large_assignment_code_block(self):
        """500 lines of assignment-style code should compile."""
        lines = [f"df = df.with_columns(pl.lit({i}).alias('c{i}'))" for i in range(500)]
        code_block = "\n".join(lines)
        result = _wrap_user_code(code_block, ["src"])
        assert "return df" in result
        # Verify it compiles in a function context
        func_code = f"import polars as pl\ndef test_func(src):\n{result}\n"
        compile(func_code, "<test>", "exec")


# ---------------------------------------------------------------------------
# Edge-case tests: _build_params
# ---------------------------------------------------------------------------


class TestBuildParamsEdgeCases:
    def test_no_sources_returns_default(self):
        assert _build_params([]) == "df: pl.LazyFrame"

    def test_single_source_returns_typed_param(self):
        assert _build_params(["source_name"]) == "source_name: pl.LazyFrame"

    def test_multiple_sources_returns_comma_separated(self):
        result = _build_params(["name1", "name2"])
        assert result == "name1: pl.LazyFrame, name2: pl.LazyFrame"

    def test_source_names_that_are_python_keywords(self):
        result = _build_params(["node_class", "node_return"])
        assert "node_class: pl.LazyFrame" in result
        assert "node_return: pl.LazyFrame" in result


# ---------------------------------------------------------------------------
# Edge-case tests: _wrap_user_code
# ---------------------------------------------------------------------------


class TestWrapUserCodeEdgeCases:
    def test_empty_code_returns_first_input_name(self):
        result = _wrap_user_code("", ["my_source"])
        assert "return my_source" in result

    def test_empty_code_no_sources_returns_df(self):
        result = _wrap_user_code("", [])
        assert "return df" in result

    def test_assignment_indented_with_return(self):
        result = _wrap_user_code("df = src.filter(pl.col('x') > 0)", ["src"])
        assert "    df = src.filter" in result
        assert "return df" in result

    def test_multiline_code_indented(self):
        code = "tmp = df.filter(pl.col('x') > 0)\ndf = tmp.select('x', 'y')"
        result = _wrap_user_code(code, ["df"])
        assert "    tmp = df.filter" in result
        assert "    df = tmp.select" in result
        assert "return df" in result

    def test_whitespace_only_code_returns_first_input(self):
        result = _wrap_user_code("   \n  \n  ", ["abc"])
        assert "return abc" in result


# ---------------------------------------------------------------------------
# Edge-case tests: node type generators
# ---------------------------------------------------------------------------


class TestGenConstantEdgeCases:
    def test_empty_values_list(self):
        node = _n(
            {
                "id": "c",
                "data": {
                    "label": "EmptyConst",
                    "nodeType": "constant",
                    "config": {"values": []},
                },
            }
        )
        code = _node_to_code(node)
        assert "def EmptyConst()" in code
        assert '"constant": [0]' in code
        _compile_node_code(code)

    def test_none_values_coerced(self):
        node = _n(
            {
                "id": "c",
                "data": {
                    "label": "NoneConst",
                    "nodeType": "constant",
                    "config": {"values": None},
                },
            }
        )
        code = _node_to_code(node)
        assert '"constant": [0]' in code
        _compile_node_code(code)

    def test_nan_handling(self):
        node = _n(
            {
                "id": "c",
                "data": {
                    "label": "NanConst",
                    "nodeType": "constant",
                    "config": {"values": [{"name": "x", "value": "nan"}]},
                },
            }
        )
        code = _node_to_code(node)
        assert "float('nan')" in code
        _compile_node_code(code)

    def test_numeric_values(self):
        node = _n(
            {
                "id": "c",
                "data": {
                    "label": "NumConst",
                    "nodeType": "constant",
                    "config": {
                        "values": [
                            {"name": "rate", "value": "3.14"},
                            {"name": "count", "value": "42"},
                        ]
                    },
                },
            }
        )
        code = _node_to_code(node)
        assert "3.14" in code
        assert "42" in code
        _compile_node_code(code)

    def test_string_values(self):
        node = _n(
            {
                "id": "c",
                "data": {
                    "label": "StrConst",
                    "nodeType": "constant",
                    "config": {"values": [{"name": "label", "value": "hello"}]},
                },
            }
        )
        code = _node_to_code(node)
        assert '"hello"' in code
        _compile_node_code(code)

    def test_none_value_emits_null_literal_not_crash(self):
        """F156: a ``value=None`` entry emits ``"x": [None]`` and does not crash.

        Pre-fix the ``except`` branch called ``_safe_str(val)`` with
        ``val is None`` — ``None.replace`` raises ``AttributeError``. The fix
        adds a ``val is None`` branch that emits a ``None`` literal.
        """
        from haute._codegen_builders import _gen_constant

        node = _n(
            {
                "id": "c",
                "data": {
                    "label": "NullConst",
                    "nodeType": "constant",
                    "config": {"values": [{"name": "x", "value": None}]},
                },
            }
        )
        code = _gen_constant(node, [])
        assert '"x": [None]' in code
        _compile_node_code(code)

    def test_empty_or_missing_name_entries_skipped(self):
        """F134: entries with an empty or missing name are dropped from the body.

        Pre-fix an empty name became ``""`` and a missing name defaulted to
        ``"col"`` — both emitted phantom columns. The fix skips them, matching
        the executor's ``_build_constant`` (``if not name: continue``).
        """
        from haute._codegen_builders import _gen_constant

        node = _n(
            {
                "id": "c",
                "data": {
                    "label": "SkipConst",
                    "nodeType": "constant",
                    "config": {
                        "values": [
                            {"name": "", "value": "5"},  # empty name -> skipped
                            {"value": "6"},  # missing name -> skipped
                            {"name": "keep", "value": "seven"},  # valid -> emitted
                        ]
                    },
                },
            }
        )
        code = _gen_constant(node, [])
        assert '"keep": ["seven"]' in code
        # The pre-fix phantom columns must NOT appear.
        assert '"": [' not in code  # empty-name column
        assert '"col": [' not in code  # missing-name default column
        _compile_node_code(code)


class TestGenDataSourceEdgeCases:
    def test_unknown_file_extension_defaults_to_parquet(self):
        node = _n(
            {
                "id": "src",
                "data": {
                    "label": "WeirdSrc",
                    "nodeType": "dataInput",
                    "config": _file_input_config(
                        "data/file.xyz",
                        format_name="parquet",
                    ),
                },
            }
        )
        code = _node_to_code(node)
        assert "resolve_data_input_from_config" in code
        assert "scan_parquet" not in code
        _compile_node_code(code)

    def test_no_extension_defaults_to_parquet(self):
        node = _n(
            {
                "id": "src",
                "data": {
                    "label": "NoExtSrc",
                    "nodeType": "dataInput",
                    "config": _file_input_config(
                        "data/noext",
                        format_name="parquet",
                    ),
                },
            }
        )
        code = _node_to_code(node)
        assert "resolve_data_input_from_config" in code
        assert "scan_parquet" not in code
        _compile_node_code(code)

    def test_databricks_config(self):
        node = _n(
            {
                "id": "src",
                "data": {
                    "label": "DBSrc",
                    "nodeType": "dataInput",
                    "config": _databricks_input_config(),
                },
            }
        )
        code = _node_to_code(node)
        assert "resolve_data_input_from_config" in code
        assert "read_cached_table" not in code
        assert 'config="config/data_input/DBSrc.json"' in code
        _compile_node_code(code)


class TestGenOutputEdgeCases:
    def test_empty_fields_list(self):
        node = _n(
            {
                "id": "out",
                "data": {
                    "label": "EmptyOut",
                    "nodeType": "output",
                    "config": make_output_config([]),
                },
            }
        )
        code = _node_to_code(node, source_names=["src"])
        assert "assemble_output_from_config(" in code
        assert "return src" not in code
        assert ".select" not in code
        _compile_node_code(code)

    def test_none_fields(self):
        node = _n(
            {
                "id": "out",
                "data": {
                    "label": "NoneOut",
                    "nodeType": "output",
                    "config": make_output_config([]),
                },
            }
        )
        code = _node_to_code(node, source_names=["src"])
        assert "assemble_output_from_config(" in code
        assert "return src" not in code
        assert ".select" not in code
        _compile_node_code(code)


class TestGenTransformEdgeCases:
    def test_empty_code_emits_raising_placeholder(self):
        node = _n(
            {
                "id": "t",
                "data": {
                    "label": "NoOp",
                    "nodeType": "polars",
                    "config": {"code": ""},
                },
            }
        )
        code = _node_to_code(node, source_names=["upstream"])
        assert "raise NotImplementedError" in code
        assert "return upstream" not in code
        _compile_node_code(code)

    def test_selected_columns_decorator_kwarg(self):
        node = _n(
            {
                "id": "t",
                "data": {
                    "label": "SelCol",
                    "nodeType": "polars",
                    "config": {
                        "code": "df = src.with_columns(y=pl.col('x') * 2)",
                        "selected_columns": ["x", "y"],
                    },
                },
            }
        )
        code = _node_to_code(node, source_names=["src"])
        assert "selected_columns=" in code
        assert "@pipeline.polars(selected_columns=" in code
        _compile_node_code(code)

    def test_no_selected_columns_uses_bare_decorator(self):
        node = _n(
            {
                "id": "t",
                "data": {
                    "label": "Bare",
                    "nodeType": "polars",
                    "config": {"code": ""},
                },
            }
        )
        # Post Item #22: empty code + no sources raises, so supply one.
        code = _node_to_code(node, source_names=["upstream"])
        # ``@pipeline.polars`` decorator with no ``selected_columns=``.
        # Contract kwargs may appear (see test_column_contracts_adoption),
        # but the ``selected_columns=`` attribute must be absent when the
        # node config lacks it.
        first_line = code.splitlines()[0]
        assert first_line.startswith("@pipeline.polars"), first_line
        assert "selected_columns" not in first_line
        _compile_node_code(code)

    def test_stable_input_mapping_round_trips_and_runs_standalone(self):
        from haute.parser import parse_pipeline_source

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "raw_rows",
                        "data": {
                            "label": "raw_rows",
                            "nodeType": "polars",
                            "config": {
                                "code": "df = pl.LazyFrame({'value': [99]})",
                            },
                        },
                    },
                    {
                        "id": "replacement",
                        "data": {
                            "label": "Replacement Parent",
                            "nodeType": "polars",
                            "config": {
                                "code": "df = pl.LazyFrame({'value': [2]})",
                            },
                        },
                    },
                    {
                        "id": "enriched",
                        "data": {
                            "label": "enriched",
                            "nodeType": "polars",
                            "config": {
                                "code": (
                                    "df = raw_rows.with_columns(value_doubled=pl.col('value') * 2)"
                                ),
                                "inputMapping": {
                                    "raw_rows": "Replacement_Parent",
                                },
                            },
                        },
                    },
                ],
                "edges": [
                    {
                        "id": "e-raw-replacement",
                        "source": "raw_rows",
                        "target": "replacement",
                    },
                    {
                        "id": "e-replacement-enriched",
                        "source": "replacement",
                        "target": "enriched",
                    },
                ],
            }
        )

        code = graph_to_code(graph, pipeline_name="stable_mapping")
        assert "inputMapping={'raw_rows': 'Replacement_Parent'}" in code
        assert "def enriched(raw_rows: pl.LazyFrame)" in code

        parsed = parse_pipeline_source(code)
        parsed_enriched = next(node for node in parsed.nodes if node.id == "enriched")
        assert parsed_enriched.data.config["inputMapping"] == {"raw_rows": "Replacement_Parent"}
        assert [
            (edge.source, edge.target) for edge in parsed.edges if edge.target == "enriched"
        ] == [
            ("Replacement_Parent", "enriched"),
        ]
        assert graph_to_code(parsed, pipeline_name="stable_mapping") == code

        namespace: dict[str, object] = {}
        exec(compile(code, "<stable_mapping>", "exec"), namespace)
        runtime_pipeline = namespace["pipeline"]
        runtime_graph = runtime_pipeline.to_graph()  # type: ignore[union-attr]
        assert [
            (edge["source"], edge["target"])
            for edge in runtime_graph["edges"]
            if edge["target"] == "enriched"
        ] == [("Replacement_Parent", "enriched")]

        result = runtime_pipeline.run()  # type: ignore[union-attr]
        if isinstance(result, pl.LazyFrame):
            result = result.collect()
        assert result["value_doubled"].to_list() == [4]  # type: ignore[index]


class TestGenLiveSwitchRoundTrip:
    def test_round_trip_preserves_scenario_map(self, tmp_path):
        import json

        from haute.parser import parse_pipeline_file

        scenario_map = {"src_a": "live", "src_b": "test_batch"}
        node = _n(
            {
                "id": "sw",
                "data": {
                    "label": "MySwitch",
                    "nodeType": "liveSwitch",
                    "config": {
                        "input_scenario_map": scenario_map,
                        "inputs": ["src_a", "src_b"],
                    },
                },
            }
        )
        code = _node_to_code(node, source_names=["src_a", "src_b"])
        full_code = (
            "import polars as pl\nimport haute\n"
            'pipeline = haute.Pipeline("test")\n\n'
            '@pipeline.data_input(config="config/data_input/src_a.json")\n'
            "def src_a() -> pl.LazyFrame:\n"
            '    return pl.scan_parquet("a.parquet")\n\n'
            '@pipeline.data_input(config="config/data_input/src_b.json")\n'
            "def src_b() -> pl.LazyFrame:\n"
            '    return pl.scan_parquet("b.parquet")\n\n'
            f"{code}\n"
            'pipeline.connect("src_a", "MySwitch")\n'
            'pipeline.connect("src_b", "MySwitch")\n'
        )
        cfg_dir = tmp_path / "config" / "source_switch"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "MySwitch.json").write_text(
            json.dumps({"input_scenario_map": scenario_map, "inputs": ["src_a", "src_b"]})
        )
        for name in ("src_a", "src_b"):
            ds_dir = tmp_path / "config" / "data_input"
            ds_dir.mkdir(parents=True, exist_ok=True)
            (ds_dir / f"{name}.json").write_text(json.dumps(_file_input_config("a.parquet")))
        py_file = tmp_path / "test.py"
        py_file.write_text(full_code)
        graph = parse_pipeline_file(py_file)
        switch_nodes = [n for n in graph.nodes if n.data.nodeType == "liveSwitch"]
        assert len(switch_nodes) == 1
        assert switch_nodes[0].data.config["input_scenario_map"] == scenario_map


class TestGenExternalFileEdgeCases:
    def test_empty_code_passthrough(self):
        node = _n(
            {
                "id": "ext",
                "data": {
                    "label": "ExtModel",
                    "nodeType": "externalFile",
                    "config": {"path": "model.pkl", "fileType": "pickle", "code": ""},
                },
            }
        )
        code = _node_to_code(node, source_names=["features"])
        assert "return df" in code
        assert "load_external_object" in code
        _compile_node_code(code)

    def test_none_code_passthrough(self):
        node = _n(
            {
                "id": "ext",
                "data": {
                    "label": "ExtNone",
                    "nodeType": "externalFile",
                    "config": {"path": "model.pkl", "fileType": "pickle", "code": None},
                },
            }
        )
        code = _node_to_code(node, source_names=["features"])
        assert "return df" in code
        _compile_node_code(code)


# ---------------------------------------------------------------------------
# Edge-case tests: graph_to_code
# ---------------------------------------------------------------------------


class TestGraphToCodeEdgeCases:
    def test_empty_graph_produces_valid_python(self):
        code = graph_to_code(_g({"nodes": [], "edges": []}))
        assert "import polars as pl" in code
        assert "import haute" in code
        assert "Pipeline" in code
        assert "pipeline.connect" not in code
        compile(code, "<test>", "exec")

    def test_single_node_pipeline_compiles(self):
        graph = _g(
            {
                "nodes": [
                    {
                        "id": "s",
                        "data": {
                            "label": "OnlySource",
                            "nodeType": "dataInput",
                            "config": {"path": "data.parquet"},
                        },
                    }
                ],
                "edges": [],
            }
        )
        code = graph_to_code(graph, pipeline_name="single")
        assert "def OnlySource()" in code
        assert "pipeline.connect" not in code
        compile(code, "<test>", "exec")

    def test_pipeline_with_description_included(self):
        graph = _g({"nodes": [], "edges": []})
        code = graph_to_code(graph, pipeline_name="rated", description="Motor pricing model")
        assert "description='Motor pricing model'" in code
        compile(code, "<test>", "exec")

    def test_preamble_positioned_before_pipeline_def(self):
        graph = _g(
            {
                "nodes": [
                    {
                        "id": "s",
                        "data": {
                            "label": "S",
                            "nodeType": "dataInput",
                            "config": {"path": "d.parquet"},
                        },
                    }
                ],
                "edges": [],
            }
        )
        code = graph_to_code(graph, preamble="MY_CONST = 42")
        lines = code.splitlines()
        preamble_idx = next(i for i, line in enumerate(lines) if "MY_CONST" in line)
        pipeline_idx = next(i for i, line in enumerate(lines) if "haute.Pipeline(" in line)
        assert preamble_idx < pipeline_idx

    def test_unknown_node_type_raises_rather_than_falling_back(self):
        """Post-Package-4B: missing codegen builder is a registration bug.
        The old silent fallback to ``_gen_transform`` was removed because it
        masked misregistered NodeTypes — see TestUnknownNodeTypeFallback."""
        from haute._registry import NODE_REGISTRY
        from haute._types import NodeType

        node = _n(
            {
                "id": "u",
                "data": {
                    "label": "FutureType",
                    "nodeType": "banding",
                    "config": {"code": "df = src.drop_nulls()"},
                },
            }
        )
        entry = NODE_REGISTRY[NodeType.BANDING]
        saved = entry.codegen
        entry.codegen = None
        try:
            with pytest.raises(KeyError, match="banding"):
                _generate_node_code(node, source_names=["src"])
        finally:
            entry.codegen = saved


# ---------------------------------------------------------------------------
# Round-trip tests
# ---------------------------------------------------------------------------


class TestRoundTripEdgeCases:
    def test_banding_with_multiple_factors(self):
        node = _n(
            {
                "id": "b",
                "data": {
                    "label": "MultiBand",
                    "nodeType": "banding",
                    "config": {
                        "factors": [
                            {
                                "banding": "continuous",
                                "column": "age",
                                "outputColumn": "age_factor",
                                "rules": [
                                    {
                                        "op1": ">=",
                                        "val1": 0,
                                        "op2": "<",
                                        "val2": 100,
                                        "assignment": "1.0",
                                    }
                                ],
                            },
                            {
                                "banding": "discrete",
                                "column": "region",
                                "outputColumn": "region_factor",
                                "rules": [{"match": "North", "assignment": "1.2"}],
                                "default": "1.0",
                            },
                        ],
                    },
                },
            }
        )
        raw_code = _generate_node_code(node, source_names=["data"])
        assert "factors=" in raw_code
        assert "def MultiBand(data: pl.LazyFrame)" in raw_code
        assert 'apply_banding_from_config(data, "config/banding/MultiBand.json"' in raw_code
        final_code = _node_to_code(node, source_names=["data"])
        assert 'config="config/banding/MultiBand.json"' in final_code
        assert "def MultiBand(data: pl.LazyFrame)" in final_code
        _compile_node_code(final_code)

    def test_rating_step_with_multiple_tables(self):
        node = _n(
            {
                "id": "rs",
                "data": {
                    "label": "MultiRate",
                    "nodeType": "ratingStep",
                    "config": {
                        "tables": [
                            {
                                "factors": ["region"],
                                "outputColumn": "region_factor",
                                "defaultValue": 1.0,
                                "entries": [{"region": "North", "value": 1.1}],
                            },
                            {
                                "factors": ["age_band"],
                                "outputColumn": "age_factor",
                                "entries": [{"age_band": "18-25", "value": 1.5}],
                            },
                        ],
                        "combinedOutputs": [
                            {
                                "outputColumn": "total_factor",
                                "operation": "add",
                                "baseValue": 0,
                            }
                        ],
                    },
                },
            }
        )
        raw_code = _generate_node_code(node, source_names=["base"])
        assert "tables=" in raw_code
        assert (
            "combined_outputs=[{'output_column': 'total_factor', "
            "'operation': 'add', 'base_value': 0.0}]"
        ) in raw_code
        assert "apply_rating_step_from_config(base" in raw_code
        assert "return df" in raw_code
        final_code = _node_to_code(node, source_names=["base"])
        assert 'config="config/rating_step/MultiRate.json"' in final_code
        assert "apply_rating_step_from_config(base" in final_code
        assert "return df" in final_code
        _compile_node_code(final_code)

    def test_data_source_with_databricks_config(self):
        node = _n(
            {
                "id": "db",
                "data": {
                    "label": "DBRead",
                    "nodeType": "dataInput",
                    "config": {
                        **_databricks_input_config(),
                        "table": "catalog.schema.my_table",
                        "query": "SELECT col1, col2",
                    },
                },
            }
        )
        code = _node_to_code(node)
        assert "resolve_data_input_from_config" in code
        assert "read_cached_table" not in code
        assert 'config="config/data_input/DBRead.json"' in code
        assert "def DBRead()" in code
        _compile_node_code(code)


# ---------------------------------------------------------------------------
# W2 regression tests (reviewer-flagged TDD gaps) — pin the codegen fixes
# landed in f48764b8 that shipped without a dedicated failing test.
# ---------------------------------------------------------------------------


class TestFormatContractKwargFailLoud:
    """F002/F637: ``_format_contract_kwarg`` narrows the opaque-contract rescue
    to infrastructure errors only. A genuine contract-computation bug
    (TypeError/KeyError/ContractMismatchError) must PROPAGATE, while an
    ``OSError`` or ``mlflow.*`` failure still rescues to ``contract="opaque"``.
    """

    @staticmethod
    def _node():
        return _n(
            {
                "id": "n",
                "data": {"label": "N", "nodeType": "polars", "config": {}},
            }
        )

    @pytest.mark.parametrize(
        "exc_factory",
        [
            lambda: TypeError("boom"),
            lambda: KeyError("boom"),
            pytest.param(
                lambda: __import__(
                    "haute.errors", fromlist=["ContractMismatchError"]
                ).ContractMismatchError("boom"),
                id="contract-mismatch",
            ),
        ],
    )
    def test_non_infra_error_propagates(self, monkeypatch, exc_factory):
        """A non-infra exception must NOT be swallowed to ``contract="opaque"``."""
        import haute.codegen as codegen_mod

        def _raise(node_type, config):
            raise exc_factory()

        monkeypatch.setattr(codegen_mod, "get_column_contract", _raise)
        with pytest.raises(type(exc_factory())):
            codegen_mod._format_contract_kwarg(self._node())

    def test_oserror_rescues_to_opaque(self, monkeypatch):
        """An ``OSError`` (missing artifact / refused connection) stays opaque."""
        import haute.codegen as codegen_mod

        def _raise(node_type, config):
            raise OSError("artifact file missing")

        monkeypatch.setattr(codegen_mod, "get_column_contract", _raise)
        assert codegen_mod._format_contract_kwarg(self._node()) == 'contract="opaque"'

    def test_mlflow_error_rescues_to_opaque(self, monkeypatch):
        """An exception raised from the ``mlflow`` package stays opaque."""
        import haute.codegen as codegen_mod

        class _FakeMlflowError(Exception):
            pass

        # ``_is_codegen_infra_error`` classifies by the exception type's
        # top-level module — simulate an mlflow.* exception.
        _FakeMlflowError.__module__ = "mlflow.exceptions"

        def _raise(node_type, config):
            raise _FakeMlflowError("mlflow unreachable")

        monkeypatch.setattr(codegen_mod, "get_column_contract", _raise)
        assert codegen_mod._format_contract_kwarg(self._node()) == 'contract="opaque"'


class TestGraphToCodeSingleFileGuard:
    """F266: ``graph_to_code`` is single-file only. A submodel graph produces
    more than one file, so silently returning the sole/first value would hand
    back a submodel instead of the main pipeline — it must raise
    ``ConfigError`` and direct the caller to ``graph_to_code_multi``."""

    def test_plain_single_file_graph_returns_code(self):
        graph = _g(
            {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "Source",
                            "nodeType": "dataInput",
                            "config": {"path": "d.parquet"},
                        },
                    }
                ],
                "edges": [],
            }
        )
        code = graph_to_code(graph, pipeline_name="main")
        assert "def Source()" in code
        compile(code, "<test>", "exec")

    def test_submodel_graph_raises_config_error(self):
        from haute.errors import ConfigError

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "Source",
                            "nodeType": "dataInput",
                            "config": {"path": "d.parquet"},
                        },
                    },
                    {
                        "id": "instance_sm1",
                        "type": "submodel",
                        "data": {
                            "label": "sm1",
                            "nodeType": "submodel",
                            "config": {"definitionId": "sm1", "alias": "sm1"},
                        },
                    },
                ],
                "edges": [
                    {
                        "id": "e1",
                        "source": "src",
                        "target": "instance_sm1",
                        "targetHandle": "in__child_a",
                    }
                ],
                "submodels": {
                    "sm1": {
                        "definitionId": "sm1",
                        "file": "modules/sm1.py",
                        "inputPorts": [
                            {
                                "portId": "child_a",
                                "label": "ChildA",
                                "targets": [{"nodeId": "child_a"}],
                            }
                        ],
                        "outputPorts": [],
                        "graph": {
                            "nodes": [
                                {
                                    "id": "child_a",
                                    "data": {"label": "ChildA", "nodeType": "polars", "config": {}},
                                }
                            ],
                            "edges": [],
                        },
                    }
                },
            }
        )
        # Sanity: the multi-file entrypoint genuinely produces >1 file here.
        assert len(graph_to_code_multi(graph, pipeline_name="main")) > 1
        with pytest.raises(ConfigError):
            graph_to_code(graph, pipeline_name="main")


class TestFormatContractSourceCollision:
    """F264: two declared ``inputs_by_parent`` source keys (a parent id AND
    that parent's emitted func-name) that collapse to the SAME emitted parent
    with conflicting columns is a genuine ambiguity — it must raise
    ``ParseError`` instead of silently keeping the last writer."""

    def test_colliding_keys_conflicting_columns_raise(self):
        from haute._contracts import Contract
        from haute.codegen import _format_contract_source
        from haute.errors import ParseError

        # parent id "p1" emits as func name "Foo"; the declared contract also
        # carries a key "Foo" (the emitted name) with a DIFFERENT column set.
        contract = Contract(
            inputs=frozenset({"a", "b"}),
            outputs=frozenset(),
            inputs_by_parent={
                "p1": frozenset({"a"}),
                "Foo": frozenset({"b"}),
            },
        )
        with pytest.raises(ParseError):
            _format_contract_source(contract, parent_name_by_id={"p1": "Foo"})

    def test_colliding_keys_matching_columns_do_not_raise(self):
        """The same collision with IDENTICAL columns is unambiguous — no raise."""
        from haute._contracts import Contract
        from haute.codegen import _format_contract_source

        contract = Contract(
            inputs=frozenset({"a"}),
            outputs=frozenset(),
            inputs_by_parent={
                "p1": frozenset({"a"}),
                "Foo": frozenset({"a"}),
            },
        )
        src = _format_contract_source(contract, parent_name_by_id={"p1": "Foo"})
        assert "Foo" in src


class TestSubmodelImportSafePath:
    """F743: the submodel import line must go through ``_safe_path`` so a file
    path containing a double-quote or backslash is emitted as a properly
    escaped string literal and the main module still compiles. Pre-fix the
    path was interpolated raw (``pipeline.submodel("{path}")``), so a quote
    broke the literal."""

    def test_quote_and_backslash_in_submodel_path_compiles(self):
        graph = _g(
            {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "Source",
                            "nodeType": "dataInput",
                            "config": {"path": "d.parquet"},
                        },
                    },
                    {
                        "id": "instance_sm1",
                        "type": "submodel",
                        "data": {
                            "label": "sm1",
                            "nodeType": "submodel",
                            "config": {"definitionId": "sm1", "alias": "sm1"},
                        },
                    },
                ],
                "edges": [
                    {
                        "id": "e1",
                        "source": "src",
                        "target": "instance_sm1",
                        "targetHandle": "in__child_a",
                    }
                ],
                "submodels": {
                    "sm1": {
                        "definitionId": "sm1",
                        # Path with a double-quote AND a Windows backslash —
                        # raw interpolation would emit invalid Python.
                        "file": 'modules/a"b\\c.py',
                        "inputPorts": [
                            {
                                "portId": "child_a",
                                "label": "ChildA",
                                "targets": [{"nodeId": "child_a"}],
                            }
                        ],
                        "outputPorts": [],
                        "graph": {
                            "nodes": [
                                {
                                    "id": "child_a",
                                    "data": {"label": "ChildA", "nodeType": "polars", "config": {}},
                                }
                            ],
                            "edges": [],
                        },
                    }
                },
            }
        )
        files = graph_to_code_multi(graph, pipeline_name="main")
        main_code = files["main.py"]
        assert "pipeline.submodel(" in main_code
        # The emitted module must be valid Python despite the hostile path.
        ast.parse(main_code)
        compile(main_code, "<gen>", "exec")
