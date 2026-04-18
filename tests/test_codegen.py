"""Tests for haute.codegen - graph JSON → Python code generation."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from haute.codegen import (
    _build_extra_kwargs,
    _build_params,
    _generate_node_code,
    _instance_to_code,
    _is_absolute_path,
    _make_passthrough_builder,
    _node_to_code,
    _portable_path_expr,
    _sanitize_description,
    _submodel_node_to_code,
    _wrap_user_code,
    graph_to_code,
    graph_to_code_multi,
)
from haute.parser import parse_pipeline_source
from tests.conftest import (
    compile_node_code as _compile_node_code,
    make_graph as _g,
    make_node as _n,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent


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
# _is_absolute_path / _portable_path_expr
# ---------------------------------------------------------------------------


class TestPortablePath:
    def test_unix_absolute(self):
        assert _is_absolute_path("/home/user/data.parquet") is True

    def test_windows_absolute(self):
        assert _is_absolute_path("C:/Users/data.parquet") is True

    def test_windows_backslash(self):
        assert _is_absolute_path("C:\\Users\\data.parquet") is True

    def test_relative(self):
        assert _is_absolute_path("data/input.csv") is False

    def test_empty(self):
        assert _is_absolute_path("") is False

    def test_dot_relative(self):
        assert _is_absolute_path("../data/file.json") is False

    def test_portable_relative_uses_file_parent(self):
        result = _portable_path_expr("data/input.parquet")
        assert "Path(__file__).parent" in result
        assert '"data/input.parquet"' in result

    def test_portable_absolute_stays_raw(self):
        result = _portable_path_expr("/absolute/path.csv")
        assert "Path(__file__)" not in result
        assert '"/absolute/path.csv"' in result

    def test_portable_empty_treated_as_relative(self):
        result = _portable_path_expr("")
        assert "Path(__file__).parent" in result


# ---------------------------------------------------------------------------
# _node_to_code
# ---------------------------------------------------------------------------


class TestNodeToCode:
    @pytest.mark.parametrize(
        "label, config, expected_strings",
        [
            pytest.param(
                "Load Data",
                {"path": "data/input.parquet"},
                [
                    "def Load_Data()",
                    'scan_parquet(Path(__file__).parent / "data/input.parquet")',
                    'config="config/data_source/Load_Data.json"',
                ],
                id="parquet",
            ),
            pytest.param(
                "CSV Source",
                {"path": "data/input.csv"},
                [
                    'scan_csv(Path(__file__).parent / "data/input.csv")',
                    "def CSV_Source()",
                    'config="config/data_source/CSV_Source.json"',
                ],
                id="csv",
            ),
            pytest.param(
                "JSON Source",
                {"path": "data/input.json"},
                [
                    'read_json(Path(__file__).parent / "data/input.json")',
                    ".lazy()",
                    "def JSON_Source()",
                    'config="config/data_source/JSON_Source.json"',
                ],
                id="json",
            ),
            pytest.param(
                "JSONL Source",
                {"path": "data/input.jsonl"},
                [
                    'scan_ndjson(Path(__file__).parent / "data/input.jsonl")',
                    "def JSONL_Source()",
                    'config="config/data_source/JSONL_Source.json"',
                ],
                id="jsonl",
            ),
            pytest.param(
                "DB Source",
                {"sourceType": "databricks", "table": "catalog.schema.tbl"},
                [
                    "read_cached_table",
                    "catalog.schema.tbl",
                    'config="config/data_source/DB_Source.json"',
                ],
                id="databricks",
            ),
        ],
    )
    def test_data_source(self, label, config, expected_strings):
        node = _n(
            {
                "id": "src",
                "data": {"label": label, "nodeType": "dataSource", "config": config},
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
                {"path": "data/input.parquet", "code": "df = df.filter(pl.col('x') > 0)"},
                ["df = pl.scan_parquet", "filter", "return df"],
                id="parquet_with_code",
            ),
            pytest.param(
                "CSV Source",
                {"path": "data/input.csv", "code": "df = df.select('a', 'b')"},
                ["df = pl.scan_csv", "select", "return df"],
                id="csv_with_code",
            ),
            pytest.param(
                "DB Source",
                {"sourceType": "databricks", "table": "cat.sch.tbl", "code": "df = df.limit(100)"},
                ["read_cached_table", "limit", "return df"],
                id="databricks_with_code",
            ),
        ],
    )
    def test_data_source_with_code(self, label, config, expected_strings):
        """DataSource with user code emits boilerplate + user code."""
        node = _n(
            {
                "id": "src",
                "data": {"label": label, "nodeType": "dataSource", "config": config},
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
                    "nodeType": "dataSource",
                    "config": {"path": "data/input.parquet"},
                },
            }
        )
        code = _node_to_code(node)
        assert "df = pl.scan_parquet" in code
        assert "return df" in code
        assert "# -- user code --" not in code
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
        assert "filter" in code
        assert "return df" in code
        _compile_node_code(code)

    def test_transform_without_code_uses_first_source(self):
        node = _n(
            {
                "id": "t",
                "data": {"label": "Pass", "nodeType": "polars", "config": {}},
            }
        )
        code = _node_to_code(node, source_names=["upstream"])
        assert "def Pass(upstream: pl.LazyFrame)" in code
        assert "return upstream" in code
        _compile_node_code(code)

    def test_transform_without_code_no_sources_raises(self):
        """Post Item #22: an orphan polars transform (no code, no inputs)
        is incoherent — must raise ``ConfigError`` rather than emit
        ``return df`` where ``df`` is unbound."""
        from haute.errors import ConfigError

        node = _n(
            {
                "id": "t",
                "data": {"label": "Pass", "nodeType": "polars", "config": {}},
            }
        )
        with pytest.raises(ConfigError):
            _node_to_code(node, source_names=[])

    def test_output_with_fields(self):
        node = _n(
            {
                "id": "out",
                "data": {
                    "label": "Output",
                    "nodeType": "output",
                    "config": {"fields": ["a", "b"]},
                },
            }
        )
        code = _node_to_code(node, source_names=["transform"])
        assert 'config="config/quote_response/Output.json"' in code
        assert "transform.select(" in code
        assert "def Output(transform: pl.LazyFrame)" in code
        _compile_node_code(code)

    def test_output_without_fields(self):
        node = _n(
            {
                "id": "out",
                "data": {
                    "label": "Final",
                    "nodeType": "output",
                    "config": {"fields": []},
                },
            }
        )
        code = _node_to_code(node, source_names=["src"])
        assert "return src" in code
        assert ".select" not in code
        _compile_node_code(code)

    def test_sink_parquet(self):
        node = _n(
            {
                "id": "s",
                "data": {
                    "label": "Write",
                    "nodeType": "dataSink",
                    "config": {"path": "out.parquet", "format": "parquet"},
                },
            }
        )
        code = _node_to_code(node, source_names=["transform"])
        assert 'safe_sink(transform, Path(__file__).parent / "outputs/out.parquet")' in code
        assert "def Write(transform: pl.LazyFrame)" in code
        _compile_node_code(code)

    def test_sink_csv(self):
        node = _n(
            {
                "id": "s",
                "data": {
                    "label": "Write CSV",
                    "nodeType": "dataSink",
                    "config": {"path": "out.csv", "format": "csv"},
                },
            }
        )
        code = _node_to_code(node)
        assert 'safe_sink(df, Path(__file__).parent / "outputs/out.csv", fmt="csv")' in code
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
        assert "base = str(Path(__file__).parent)" in code
        assert "base_dir=base" in code
        assert "from pathlib import Path" in code
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
                        "code": "result = result * 2",
                    },
                },
            }
        )
        code = _node_to_code(node)
        assert "score_from_config" in code
        assert "base = str(Path(__file__).parent)" in code
        assert "base_dir=base" in code
        assert "from pathlib import Path" in code
        assert "result = result * 2" in code
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
                                "name": "Region",
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
                    '"catboost", "regressor"',
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
                            "nodeType": "dataSource",
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
                            "nodeType": "dataSource",
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
                            "nodeType": "dataSource",
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
                        "data": {"label": "Out", "nodeType": "output", "config": {"fields": ["x"]}},
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
        assert "return live_src" in code
        _compile_node_code(code)

    def test_emits_config_ref_with_no_live_mapping(self):
        code = _node_to_code(
            self._switch_node({"live_src": "test_batch", "batch_src": "prod"}),
            source_names=["live_src", "batch_src"],
        )
        assert 'config="config/source_switch/Switch.json"' in code
        # Falls back to first param when no input mapped to "live"
        assert "return live_src" in code
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
            '@pipeline.data_source(path="a.parquet")\n'
            "def live_src() -> pl.LazyFrame:\n"
            '    return pl.scan_parquet("a.parquet")\n\n'
            '@pipeline.data_source(path="b.parquet")\n'
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
            ds_dir = tmp_path / "config" / "data_source"
            ds_dir.mkdir(parents=True, exist_ok=True)
            (ds_dir / f"{name}.json").write_text(json.dumps({"path": "a.parquet"}))

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
                    "nodeType": "dataSource",
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
                    "nodeType": "dataSource",
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
        body_lines = [l for l in lines if not l.startswith("@")]
        assert not any(".select(" in l for l in body_lines)

    def test_transform_no_decorator_kwarg_when_empty(self):
        """Transform without selected_columns uses bare @pipeline.polars."""
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
        assert code.startswith("@pipeline.polars\n")


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
        """Transform with empty string code should generate passthrough."""
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
        assert "return upstream" in code
        _compile_node_code(code)

    def test_graph_with_edge_referencing_nonexistent_node(self):
        """Edges to non-existent nodes should not crash graph_to_code."""
        graph = _g(
            {
                "nodes": [
                    {
                        "id": "a",
                        "data": {
                            "label": "A",
                            "nodeType": "dataSource",
                            "config": {"path": "d.parquet"},
                        },
                    },
                ],
                "edges": [
                    {"id": "e1", "source": "a", "target": "ghost_node"},
                ],
            }
        )
        # Should not raise — ghost edges are tolerated
        code = graph_to_code(graph)
        assert "import polars as pl" in code
        compile(code, "<test>", "exec")

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
        """Output node with None fields list should generate passthrough."""
        node = _n(
            {
                "id": "out",
                "data": {
                    "label": "Out",
                    "nodeType": "output",
                    "config": {"fields": None},
                },
            }
        )
        code = _node_to_code(node, source_names=["src"])
        assert "return src" in code
        assert ".select" not in code
        _compile_node_code(code)

    def test_sink_with_empty_path(self):
        """Sink node with empty path should still generate compilable code."""
        node = _n(
            {
                "id": "s",
                "data": {
                    "label": "Sink",
                    "nodeType": "dataSink",
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
                    "nodeType": "dataSource",
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
        assert "return df" in code
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
                            "nodeType": "dataSource",
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

    def test_banding_single_returns_first_param(self):
        """Banding single-factor should return the first upstream name, not 'df'."""
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
        assert "return upstream_data" in code
        assert "return df" not in code
        _compile_node_code(code)

    def test_banding_multi_returns_first_param(self):
        """Banding multi-factor should return the first upstream name, not 'df'."""
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
        assert "return my_source" in code
        assert "return df" not in code
        _compile_node_code(code)

    def test_rating_step_returns_first_param(self):
        """Rating step should return the first upstream name, not 'df'."""
        node = _n(
            {
                "id": "rs",
                "data": {
                    "label": "Rate",
                    "nodeType": "ratingStep",
                    "config": {
                        "tables": [
                            {
                                "name": "T",
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
        assert "return input_df" in code
        assert "return df" not in code
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


class TestDataSourceJsonCodegen:
    """Verify that DATA_SOURCE codegen produces correct templates for all
    supported file extensions, including JSON and JSONL which were previously
    missing (B4 bug: fell through to parquet template).
    """

    def _make_ds_node(self, path: str, label: str = "Source", **extra_config):
        config = {"path": path, **extra_config}
        return _n(
            {
                "id": "src",
                "data": {"label": label, "nodeType": "dataSource", "config": config},
            }
        )

    # -- CSV (regression: existing behaviour must not break) ----------------

    def test_csv_uses_scan_csv(self):
        code = _node_to_code(self._make_ds_node("data/file.csv", "CSVSrc"))
        assert 'scan_csv(Path(__file__).parent / "data/file.csv")' in code
        assert "scan_parquet" not in code
        assert "read_json" not in code
        _compile_node_code(code)

    # -- Parquet (regression: existing behaviour must not break) -------------

    def test_parquet_uses_scan_parquet(self):
        code = _node_to_code(self._make_ds_node("data/file.parquet", "ParqSrc"))
        assert 'scan_parquet(Path(__file__).parent / "data/file.parquet")' in code
        assert "scan_csv" not in code
        assert "read_json" not in code
        _compile_node_code(code)

    # -- JSON (new behaviour) -----------------------------------------------

    def test_json_uses_read_json_lazy(self):
        """JSON data source should use pl.read_json(...).lazy(), matching _io.read_source."""
        code = _node_to_code(self._make_ds_node("data/quotes.json", "JSONSrc"))
        assert 'read_json(Path(__file__).parent / "data/quotes.json")' in code
        assert ".lazy()" in code
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
        assert 'config="config/data_source/JsonCfg.json"' in code

    # -- JSONL (new behaviour) ----------------------------------------------

    def test_jsonl_uses_scan_ndjson(self):
        """JSONL data source should use pl.scan_ndjson(...), matching _io.read_source."""
        code = _node_to_code(self._make_ds_node("data/events.jsonl", "JsonlSrc"))
        assert 'scan_ndjson(Path(__file__).parent / "data/events.jsonl")' in code
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
        assert 'config="config/data_source/JsonlCfg.json"' in code

    # -- Case-insensitive extension matching --------------------------------

    def test_uppercase_json_extension(self):
        """Path with .JSON (uppercase) should still use the JSON template."""
        code = _node_to_code(self._make_ds_node("data/INPUT.JSON", "UpperJson"))
        assert 'read_json(Path(__file__).parent / "data/INPUT.JSON")' in code
        assert ".lazy()" in code
        assert "scan_parquet" not in code
        _compile_node_code(code)

    def test_uppercase_jsonl_extension(self):
        """Path with .JSONL (uppercase) should still use the JSONL template."""
        code = _node_to_code(self._make_ds_node("data/EVENTS.JSONL", "UpperJsonl"))
        assert 'scan_ndjson(Path(__file__).parent / "data/EVENTS.JSONL")' in code
        assert "scan_parquet" not in code
        _compile_node_code(code)

    def test_uppercase_csv_extension(self):
        """Path with .CSV (uppercase) should still use the CSV template."""
        code = _node_to_code(self._make_ds_node("data/FILE.CSV", "UpperCsv"))
        assert 'scan_csv(Path(__file__).parent / "data/FILE.CSV")' in code
        assert "scan_parquet" not in code
        _compile_node_code(code)

    # -- Paths with dots in directory names ---------------------------------

    def test_json_with_dots_in_directory(self):
        """Dots in parent directory names must not confuse extension detection."""
        code = _node_to_code(self._make_ds_node("data/v2.1/quotes.json", "DotDir"))
        assert 'read_json(Path(__file__).parent / "data/v2.1/quotes.json")' in code
        assert ".lazy()" in code
        assert "scan_parquet" not in code
        _compile_node_code(code)

    def test_jsonl_with_dots_in_directory(self):
        """Dots in parent directory names must not confuse extension detection."""
        code = _node_to_code(self._make_ds_node("data/v3.0.beta/events.jsonl", "DotDirL"))
        assert 'scan_ndjson(Path(__file__).parent / "data/v3.0.beta/events.jsonl")' in code
        assert "scan_parquet" not in code
        _compile_node_code(code)

    def test_parquet_with_dots_in_directory(self):
        """Parquet path with dots in directory should still use scan_parquet."""
        code = _node_to_code(self._make_ds_node("data/v1.2/file.parquet", "DotDirP"))
        assert 'scan_parquet(Path(__file__).parent / "data/v1.2/file.parquet")' in code
        _compile_node_code(code)

    # -- Consistency with _io.read_source -----------------------------------

    @pytest.mark.parametrize(
        "ext, expected_fn",
        [
            (".csv", "scan_csv"),
            (".json", "read_json"),
            (".jsonl", "scan_ndjson"),
            (".parquet", "scan_parquet"),
        ],
        ids=["csv", "json", "jsonl", "parquet"],
    )
    def test_codegen_matches_read_source_dispatch(self, ext, expected_fn):
        """Codegen must use the same Polars function as _io.read_source for each extension."""
        path = f"data/file{ext}"
        code = _node_to_code(self._make_ds_node(path, f"Src{ext.strip('.')}"))
        assert expected_fn in code, f"Expected {expected_fn!r} in codegen for {ext!r}, got:\n{code}"
        _compile_node_code(code)

    # -- Unknown extension falls through to parquet -------------------------

    def test_unknown_extension_falls_through_to_parquet(self):
        """An unrecognised extension should still fall through to scan_parquet."""
        code = _node_to_code(self._make_ds_node("data/file.feather", "FeatherSrc"))
        assert "scan_parquet" in code
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
                            "nodeType": "dataSource",
                            "config": {"path": "data.json"},
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
        assert 'read_json(Path(__file__).parent / "data.json")' in code
        assert ".lazy()" in code
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
                            "nodeType": "dataSource",
                            "config": {"path": "events.jsonl"},
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
        assert 'scan_ndjson(Path(__file__).parent / "events.jsonl")' in code
        assert "def Filter(EventLog: pl.LazyFrame)" in code
        compile(code, "<test>", "exec")

    # -- Additional edge cases ----------------------------------------------

    def test_ndjson_extension_falls_through_to_parquet(self):
        """.ndjson is NOT a supported user-facing extension — falls through to parquet."""
        code = _node_to_code(self._make_ds_node("data/events.ndjson", "NdjsonSrc"))
        assert "scan_parquet" in code
        assert "scan_ndjson" not in code
        _compile_node_code(code)

    def test_empty_path_falls_through_to_parquet(self):
        """Empty path string should fall through to parquet template."""
        code = _node_to_code(self._make_ds_node("", "EmptyPath"))
        assert "scan_parquet" in code
        _compile_node_code(code)

    def test_no_extension_falls_through_to_parquet(self):
        """Path with no extension should fall through to parquet template."""
        code = _node_to_code(self._make_ds_node("data/noext", "NoExt"))
        assert "scan_parquet" in code
        _compile_node_code(code)

    def test_mixed_case_json_extension(self):
        """Path with .Json (mixed case) should use the JSON template."""
        code = _node_to_code(self._make_ds_node("data/file.Json", "MixedJson"))
        assert 'read_json(Path(__file__).parent / "data/file.Json")' in code
        assert ".lazy()" in code
        _compile_node_code(code)

    def test_mixed_case_parquet_extension(self):
        """Path with .Parquet (mixed case) should use the parquet template."""
        code = _node_to_code(self._make_ds_node("data/file.Parquet", "MixedPq"))
        assert 'scan_parquet(Path(__file__).parent / "data/file.Parquet")' in code
        _compile_node_code(code)


# ---------------------------------------------------------------------------
# API input codegen: case-insensitive extension matching
# ---------------------------------------------------------------------------


class TestApiInputCodegen:
    """Verify that API input codegen handles case-insensitive extensions."""

    def _make_api_node(self, path: str, label: str = "Input"):
        return _n(
            {
                "id": "inp",
                "data": {"label": label, "nodeType": "apiInput", "config": {"path": path}},
            }
        )

    def test_json_api_input(self):
        code = _node_to_code(self._make_api_node("input.json", "JsonIn"))
        assert "read_json_flat" in code
        assert "api_input=True" not in code  # replaced by config= ref
        _compile_node_code(code)

    def test_jsonl_api_input(self):
        code = _node_to_code(self._make_api_node("input.jsonl", "JsonlIn"))
        assert "read_json_flat" in code
        _compile_node_code(code)

    def test_csv_api_input(self):
        code = _node_to_code(self._make_api_node("input.csv", "CsvIn"))
        assert "scan_csv" in code
        assert "read_json_flat" not in code
        _compile_node_code(code)

    def test_uppercase_json_api_input(self):
        """Case-insensitive: .JSON should use read_json_flat, not scan_parquet."""
        code = _node_to_code(self._make_api_node("input.JSON", "UpperIn"))
        assert "read_json_flat" in code
        assert "scan_parquet" not in code
        _compile_node_code(code)

    def test_uppercase_csv_api_input(self):
        """Case-insensitive: .CSV should use scan_csv, not scan_parquet."""
        code = _node_to_code(self._make_api_node("input.CSV", "UpperCsv"))
        assert "scan_csv" in code
        assert "scan_parquet" not in code
        _compile_node_code(code)

    def test_parquet_api_input(self):
        code = _node_to_code(self._make_api_node("input.parquet", "PqIn"))
        assert "scan_parquet" in code
        assert "read_json_flat" not in code
        _compile_node_code(code)


# ---------------------------------------------------------------------------
# _make_passthrough_builder factory + all four passthrough node types
# ---------------------------------------------------------------------------


class TestMakePassthroughBuilder:
    """Tests for the ``_make_passthrough_builder`` factory and the four
    passthrough codegen builders it creates (scenario_expander, optimiser,
    optimiser_apply, modelling).
    """

    # -- factory unit tests --------------------------------------------------

    def test_factory_returns_callable(self):
        template = """\
@pipeline.test({dec_kwargs})
def {func_name}({params}) -> pl.LazyFrame:
    \"\"\"{description}\"\"\"
    return {first}
"""
        builder = _make_passthrough_builder(template, ("bar",))
        assert callable(builder)

    def test_factory_produces_valid_code(self):
        """A builder from the factory should produce compilable code."""
        template = """\
@pipeline.test({dec_kwargs})
def {func_name}({params}) -> pl.LazyFrame:
    \"\"\"{description}\"\"\"
    return {first}
"""
        builder = _make_passthrough_builder(template, ("alpha", "beta"))
        node = _n(
            {
                "id": "x",
                "data": {
                    "label": "My Node",
                    "nodeType": "polars",
                    "config": {"alpha": 42, "beta": "hello"},
                },
            }
        )
        code = builder(node, ["upstream"])
        assert "def My_Node(upstream: pl.LazyFrame)" in code
        assert "alpha=42" in code
        assert "beta='hello'" in code
        assert "return upstream" in code
        _compile_node_code(code)

    def test_factory_skips_empty_config_values(self):
        """None, empty string, and empty list config values are omitted."""
        template = """\
@pipeline.test({dec_kwargs})
def {func_name}({params}) -> pl.LazyFrame:
    \"\"\"{description}\"\"\"
    return {first}
"""
        builder = _make_passthrough_builder(template, ("a", "b", "c", "d"))
        node = _n(
            {
                "id": "x",
                "data": {
                    "label": "Skip",
                    "nodeType": "polars",
                    "config": {"a": None, "b": "", "c": [], "d": "keep"},
                },
            }
        )
        code = builder(node, [])
        assert "a=" not in code
        assert "b=" not in code
        assert "c=" not in code
        assert "d='keep'" in code

    def test_factory_no_extra_kwargs(self):
        """When all config keys are absent, no trailing comma appears."""
        template = """\
@pipeline.test({dec_kwargs})
def {func_name}({params}) -> pl.LazyFrame:
    \"\"\"{description}\"\"\"
    return {first}
"""
        builder = _make_passthrough_builder(template, ("missing_key",))
        node = _n(
            {
                "id": "x",
                "data": {
                    "label": "Bare",
                    "nodeType": "polars",
                    "config": {},
                },
            }
        )
        code = builder(node, [])
        assert "@pipeline.test()" in code
        assert "missing_key" not in code

    def test_factory_multiple_sources(self):
        """Builder should list all upstream params and return the first."""
        template = """\
@pipeline.test({dec_kwargs})
def {func_name}({params}) -> pl.LazyFrame:
    \"\"\"{description}\"\"\"
    return {first}
"""
        builder = _make_passthrough_builder(template, ())
        node = _n(
            {
                "id": "x",
                "data": {"label": "Join", "nodeType": "polars", "config": {}},
            }
        )
        code = builder(node, ["left", "right"])
        assert "def Join(left: pl.LazyFrame, right: pl.LazyFrame)" in code
        assert "return left" in code

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
                {"artifact_path": "models/opt", "version": "3"},
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
        assert "return upstream" in raw_code

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
        assert "return df" in raw_code

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
                    "config": {},
                },
            }
        )
        code = _node_to_code(node, source_names=["left", "right"])
        assert "def Merge(left: pl.LazyFrame, right: pl.LazyFrame)" in code
        assert "return left" in code
        _compile_node_code(code)


# ---------------------------------------------------------------------------
# E10: Unknown node type logs warning and falls back to transform
# ---------------------------------------------------------------------------


class TestUnknownNodeTypeFallback:
    def test_unknown_type_logs_warning(self) -> None:
        """When a node type has no registered builder, log a warning and fall back to transform."""
        from unittest.mock import patch

        from haute.codegen import _CODEGEN_BUILDERS

        # Use a valid nodeType but temporarily remove its builder to trigger fallback
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

        original_builder = _CODEGEN_BUILDERS.pop("banding", None)
        try:
            with patch("haute.codegen.logger") as mock_logger:
                code = _node_to_code(node, source_names=["src"])

            mock_logger.warning.assert_any_call(
                "unknown_node_type_fallback",
                node_type="banding",
                node_id="n_unknown",
                label="Mystery",
            )
            # Falls back to transform — should still produce valid code
            assert "def Mystery" in code
            _compile_node_code(code)
        finally:
            if original_builder is not None:
                _CODEGEN_BUILDERS["banding"] = original_builder


# ---------------------------------------------------------------------------
# Gap 1: Unknown node type fallback produces compilable transform code
# ---------------------------------------------------------------------------


class TestUnknownNodeTypeFallbackCode:
    """Catch: if fallback silently generates broken code for unknown types,
    deployed pipelines with future node types would fail at import time."""

    def test_fallback_generates_compilable_transform_with_code(self):
        """Unknown type with user code still wraps it like a polars transform."""
        from unittest.mock import patch
        from haute.codegen import _CODEGEN_BUILDERS

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
        saved = _CODEGEN_BUILDERS.pop("banding", None)
        try:
            with patch("haute.codegen.logger"):
                code = _generate_node_code(node, source_names=["upstream"])
            assert "def FutureNode(upstream: pl.LazyFrame)" in code
            assert "filter" in code
            assert "return df" in code
            _compile_node_code(code)
        finally:
            if saved is not None:
                _CODEGEN_BUILDERS["banding"] = saved

    def test_fallback_without_code_returns_first_source(self):
        """Unknown type with no user code produces passthrough returning first source."""
        from unittest.mock import patch
        from haute.codegen import _CODEGEN_BUILDERS

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
        saved = _CODEGEN_BUILDERS.pop("banding", None)
        try:
            with patch("haute.codegen.logger"):
                code = _generate_node_code(node, source_names=["src"])
            assert "return src" in code
            _compile_node_code(code)
        finally:
            if saved is not None:
                _CODEGEN_BUILDERS["banding"] = saved


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
    """Catch: descriptions containing triple quotes, trailing backslashes,
    or trailing double-quotes would break the generated docstring, producing
    a SyntaxError at pipeline import time."""

    def test_triple_quotes_replaced(self):
        """Triple quotes inside description would close the docstring early."""
        result = _sanitize_description('hello """world"""')
        assert '"""' not in result
        assert "'''" in result
        # Must produce valid docstring
        code = f'def f():\n    """{result}"""\n    pass'
        compile(code, "<test>", "exec")

    def test_trailing_double_quote(self):
        """A trailing " merges with closing triple-quote to form invalid syntax."""
        result = _sanitize_description('ends with quote"')
        code = f'def f():\n    """{result}"""\n    pass'
        compile(code, "<test>", "exec")

    def test_trailing_multiple_double_quotes(self):
        """Multiple trailing " chars each need escaping."""
        result = _sanitize_description('danger""')
        code = f'def f():\n    """{result}"""\n    pass'
        compile(code, "<test>", "exec")

    def test_trailing_backslash(self):
        """A trailing backslash would escape the closing quote."""
        result = _sanitize_description("ends with backslash\\")
        code = f'def f():\n    """{result}"""\n    pass'
        compile(code, "<test>", "exec")

    def test_trailing_backslash_before_quotes(self):
        r"""Odd backslashes before trailing quotes: ``foo\"`` would absorb escape."""
        result = _sanitize_description('backslash then quote\\"')
        code = f'def f():\n    """{result}"""\n    pass'
        compile(code, "<test>", "exec")

    def test_only_triple_quotes(self):
        """Description that is nothing but triple quotes."""
        result = _sanitize_description('"""')
        assert '"""' not in result
        code = f'def f():\n    """{result}"""\n    pass'
        compile(code, "<test>", "exec")

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
                            "nodeType": "dataSource",
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


class TestConnectDeduplication:
    """Catch: in multi-submodel mode, cross-boundary edges can produce
    duplicate connect() calls. ``dedup_connects=True`` must eliminate them.
    Without dedup, the pipeline would wire the same pair twice, potentially
    causing double-execution or confusing the executor."""

    def test_duplicate_connects_deduplicated(self):
        """Two edges between the same pair in submodel mode should produce one connect."""
        graph = _g(
            {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "Source",
                            "nodeType": "dataSource",
                            "config": {"path": "d.parquet"},
                        },
                    },
                    {
                        "id": "child_a",
                        "data": {"label": "ChildA", "nodeType": "polars", "config": {}},
                    },
                ],
                "edges": [
                    {
                        "id": "e1",
                        "source": "src",
                        "target": "submodel__sm1",
                        "targetHandle": "in__child_a",
                    },
                    {
                        "id": "e2",
                        "source": "src",
                        "target": "submodel__sm1",
                        "targetHandle": "in__child_a",
                    },
                ],
                "submodels": {
                    "sm1": {
                        "file": "modules/sm1.py",
                        "childNodeIds": ["child_a"],
                        "graph": {
                            "nodes": [
                                {
                                    "id": "child_a",
                                    "data": {"label": "ChildA", "nodeType": "polars", "config": {}},
                                },
                            ],
                            "edges": [],
                        },
                    },
                },
            }
        )
        files = graph_to_code_multi(graph, pipeline_name="main")
        main_code = files["main.py"]
        # Count connect calls for the same pair
        connect_count = main_code.count('pipeline.connect("Source", "ChildA")')
        assert connect_count == 1, f"Expected 1 connect call after dedup, got {connect_count}"
        compile(main_code, "<test>", "exec")


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
                            "nodeType": "dataSource",
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


class TestGenDataSourceEdgeCases:
    def test_unknown_file_extension_defaults_to_parquet(self):
        node = _n(
            {
                "id": "src",
                "data": {
                    "label": "WeirdSrc",
                    "nodeType": "dataSource",
                    "config": {"path": "data/file.xyz"},
                },
            }
        )
        code = _node_to_code(node)
        assert "scan_parquet" in code
        _compile_node_code(code)

    def test_no_extension_defaults_to_parquet(self):
        node = _n(
            {
                "id": "src",
                "data": {
                    "label": "NoExtSrc",
                    "nodeType": "dataSource",
                    "config": {"path": "data/noext"},
                },
            }
        )
        code = _node_to_code(node)
        assert "scan_parquet" in code
        _compile_node_code(code)

    def test_databricks_config(self):
        node = _n(
            {
                "id": "src",
                "data": {
                    "label": "DBSrc",
                    "nodeType": "dataSource",
                    "config": {
                        "sourceType": "databricks",
                        "table": "catalog.schema.tbl",
                        "http_path": "/sql/1.0/endpoints/abc",
                        "query": "SELECT * FROM t",
                    },
                },
            }
        )
        code = _node_to_code(node)
        assert "read_cached_table" in code
        assert "catalog.schema.tbl" in code
        _compile_node_code(code)


class TestGenOutputEdgeCases:
    def test_empty_fields_list(self):
        node = _n(
            {
                "id": "out",
                "data": {
                    "label": "EmptyOut",
                    "nodeType": "output",
                    "config": {"fields": []},
                },
            }
        )
        code = _node_to_code(node, source_names=["src"])
        assert "return src" in code
        assert ".select" not in code
        _compile_node_code(code)

    def test_none_fields(self):
        node = _n(
            {
                "id": "out",
                "data": {
                    "label": "NoneOut",
                    "nodeType": "output",
                    "config": {"fields": None},
                },
            }
        )
        code = _node_to_code(node, source_names=["src"])
        assert "return src" in code
        assert ".select" not in code
        _compile_node_code(code)


class TestGenTransformEdgeCases:
    def test_empty_code_passthrough(self):
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
        assert "return upstream" in code
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
        assert code.startswith("@pipeline.polars\n")
        _compile_node_code(code)


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
            '@pipeline.data_source(path="a.parquet")\n'
            "def src_a() -> pl.LazyFrame:\n"
            '    return pl.scan_parquet("a.parquet")\n\n'
            '@pipeline.data_source(path="b.parquet")\n'
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
            ds_dir = tmp_path / "config" / "data_source"
            ds_dir.mkdir(parents=True, exist_ok=True)
            (ds_dir / f"{name}.json").write_text(json.dumps({"path": "a.parquet"}))
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
                            "nodeType": "dataSource",
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
                            "nodeType": "dataSource",
                            "config": {"path": "d.parquet"},
                        },
                    }
                ],
                "edges": [],
            }
        )
        code = graph_to_code(graph, preamble="MY_CONST = 42")
        lines = code.splitlines()
        preamble_idx = next(i for i, l in enumerate(lines) if "MY_CONST" in l)
        pipeline_idx = next(i for i, l in enumerate(lines) if "haute.Pipeline(" in l)
        assert preamble_idx < pipeline_idx

    def test_unknown_node_type_falls_back_gracefully(self):
        from unittest.mock import patch
        from haute.codegen import _CODEGEN_BUILDERS

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
        saved = _CODEGEN_BUILDERS.pop("banding", None)
        try:
            with patch("haute.codegen.logger") as mock_logger:
                code = _generate_node_code(node, source_names=["src"])
            mock_logger.warning.assert_any_call(
                "unknown_node_type_fallback",
                node_type="banding",
                node_id="u",
                label="FutureType",
            )
            assert "def FutureType(src: pl.LazyFrame)" in code
            _compile_node_code(code)
        finally:
            if saved is not None:
                _CODEGEN_BUILDERS["banding"] = saved


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
        assert "return data" in raw_code
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
                                "name": "Region",
                                "factors": ["region"],
                                "outputColumn": "region_factor",
                                "defaultValue": 1.0,
                                "entries": [{"region": "North", "value": 1.1}],
                            },
                            {
                                "name": "Age",
                                "factors": ["age_band"],
                                "outputColumn": "age_factor",
                                "entries": [{"age_band": "18-25", "value": 1.5}],
                            },
                        ],
                        "operation": "add",
                        "combinedColumn": "total_factor",
                    },
                },
            }
        )
        raw_code = _generate_node_code(node, source_names=["base"])
        assert "tables=" in raw_code
        assert "operation='add'" in raw_code
        assert "combined_column='total_factor'" in raw_code
        assert "return base" in raw_code
        final_code = _node_to_code(node, source_names=["base"])
        assert 'config="config/rating_step/MultiRate.json"' in final_code
        assert "return base" in final_code
        _compile_node_code(final_code)

    def test_data_source_with_databricks_config(self):
        node = _n(
            {
                "id": "db",
                "data": {
                    "label": "DBRead",
                    "nodeType": "dataSource",
                    "config": {
                        "sourceType": "databricks",
                        "table": "catalog.schema.my_table",
                        "http_path": "/sql/1.0/endpoints/xyz",
                        "query": "SELECT col1, col2 FROM t WHERE year = 2024",
                    },
                },
            }
        )
        code = _node_to_code(node)
        assert "read_cached_table" in code
        assert "catalog.schema.my_table" in code
        assert "def DBRead()" in code
        _compile_node_code(code)
