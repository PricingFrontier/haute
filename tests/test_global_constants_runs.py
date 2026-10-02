"""Every run reads each global constant's value for the source it runs under (GCONST-03)."""

from __future__ import annotations

import importlib.util
import json
import pickle
import threading
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock, patch

import numpy as np
import polars as pl
import pytest

from haute._global_constants import GlobalConstantsNamespace, bind_function_view, restricted_view
from haute._types import GlobalConstant, PipelineGraph
from haute.deploy._validators import _global_constant_errors
from haute.errors import GlobalConstantError
from haute.executor import _build_node_fn, execute_graph, write_data_output
from tests.conftest import (
    make_edge,
    make_file_output_config,
    make_graph,
    make_node,
    make_ready_file_input_config,
    make_transform_node,
)

pytestmark = pytest.mark.usefixtures("_widen_sandbox_root")

RATE = GlobalConstant(name="rate", type="float", by_source={"live": 1.5, "nb_batch": 2.5})
LIVE_ONLY = GlobalConstant(name="rate", type="float", by_source={"live": 1.5})


def _source(nid: str, path: Path, **extra: object) -> dict[str, object]:
    return {
        "id": nid,
        "data": {
            "label": nid,
            "nodeType": "dataInput",
            "config": make_ready_file_input_config(str(path), **extra),
        },
    }


def _input(tmp_path: Path, name: str = "in.parquet", **columns: list[object]) -> Path:
    path = tmp_path / name
    pl.DataFrame(columns or {"x": [1.0, 2.0]}).write_parquet(path)
    return path


def _graph(nodes: list[object], edges: list[object], *constants: GlobalConstant) -> PipelineGraph:
    graph = make_graph({"nodes": nodes, "edges": edges})
    return graph.model_copy(update={"global_constants": list(constants)})


def _column(result: object, column: str) -> list[object]:
    return [row[column] for row in result.preview]  # type: ignore[attr-defined]


class TestExecutorRuns:
    def test_a_transform_reads_each_sources_value(self, tmp_path: Path) -> None:
        graph = _graph(
            [
                make_node(_source("src", _input(tmp_path))),
                make_transform_node(
                    "t", "df = src.with_columns(rate=pl.lit(global_constants.rate))"
                ),
            ],
            [make_edge("src", "t")],
            RATE,
        )

        assert _column(execute_graph(graph, "t", source="live")["t"], "rate") == [1.5, 1.5]
        assert _column(execute_graph(graph, "t", source="nb_batch")["t"], "rate") == [2.5, 2.5]

    def test_data_input_and_external_file_code_read_the_sources_value(self, tmp_path: Path) -> None:
        lookup = tmp_path / "lookup.json"
        lookup.write_text(json.dumps({"base": 10.0}))
        graph = _graph(
            [
                make_node(
                    _source(
                        "src",
                        _input(tmp_path),
                        code="df = df.with_columns(read=pl.lit(global_constants.rate))",
                    )
                ),
                make_node(
                    {
                        "id": "ext",
                        "data": {
                            "label": "ext",
                            "nodeType": "externalFile",
                            "config": {
                                "path": str(lookup),
                                "fileType": "json",
                                "code": (
                                    "df = df.with_columns("
                                    "scaled=pl.lit(obj['base'] * global_constants.rate))"
                                ),
                            },
                        },
                    }
                ),
            ],
            [make_edge("src", "ext")],
            RATE,
        )

        result = execute_graph(graph, "ext", source="nb_batch")["ext"]

        assert result.status == "ok", result.error
        assert _column(result, "read") == [2.5, 2.5]
        assert _column(result, "scaled") == [25.0, 25.0]

    def test_a_missing_source_value_fails_only_the_nodes_that_read_it(self, tmp_path: Path) -> None:
        graph = _graph(
            [
                make_node(_source("src", _input(tmp_path))),
                make_transform_node(
                    "reader", "df = src.with_columns(rate=pl.lit(global_constants.rate))"
                ),
                make_transform_node("plain", "df = src.with_columns(y=pl.col('x') + 1)"),
            ],
            [make_edge("src", "reader"), make_edge("src", "plain")],
            LIVE_ONLY,
        )

        reader = execute_graph(graph, "reader", source="nb_batch")["reader"]
        plain = execute_graph(graph, "plain", source="nb_batch")["plain"]

        assert reader.status == "error"
        assert "'rate' has no value for source 'nb_batch'" in (reader.error or "")
        assert plain.status == "ok"

    def test_getattr_and_f_string_reads_resolve(self, tmp_path: Path) -> None:
        graph = _graph(
            [
                make_node(_source("src", _input(tmp_path))),
                make_transform_node(
                    "t",
                    "name = 'ra' + 'te'\n"
                    "df = src.with_columns(\n"
                    "    a=pl.lit(getattr(global_constants, name)),\n"
                    "    b=pl.lit(f'rate={global_constants.rate}'),\n"
                    ")",
                ),
            ],
            [make_edge("src", "t")],
            RATE,
        )

        result = execute_graph(graph, "t", source="nb_batch")["t"]

        assert result.status == "ok", result.error
        assert _column(result, "a") == [2.5, 2.5]
        assert _column(result, "b") == ["rate=2.5", "rate=2.5"]

    @pytest.mark.parametrize(
        ("constants", "error", "message"),
        [
            ([], None, "'rate' is not defined"),
            ([RATE], "bad JSON", "could not be loaded from config/global_constants.json: bad JSON"),
        ],
        ids=["undefined", "load-error"],
    )
    def test_an_undefined_name_or_a_load_error_fails_only_the_reading_node(
        self,
        tmp_path: Path,
        constants: list[GlobalConstant],
        error: str | None,
        message: str,
    ) -> None:
        graph = _graph(
            [
                make_node(_source("src", _input(tmp_path))),
                make_transform_node(
                    "reader", "df = src.with_columns(rate=pl.lit(global_constants.rate))"
                ),
                make_transform_node("plain", "df = src.with_columns(y=pl.col('x') + 1)"),
            ],
            [make_edge("src", "reader"), make_edge("src", "plain")],
            *constants,
        ).model_copy(update={"global_constants_error": error})

        reader = execute_graph(graph, "reader", source="live")["reader"]

        assert reader.status == "error"
        assert message in (reader.error or "")
        assert execute_graph(graph, "plain", source="live")["plain"].status == "ok"

    def test_the_cached_preamble_namespace_is_not_mutated(self, tmp_path: Path) -> None:
        from haute.executor import _compile_preamble

        preamble = "BASE = 2.0"
        graph = _graph(
            [
                make_node(_source("src", _input(tmp_path))),
                make_transform_node(
                    "t", "df = src.with_columns(r=pl.lit(BASE * global_constants.rate))"
                ),
            ],
            [make_edge("src", "t")],
            RATE,
        ).model_copy(update={"preamble": preamble})

        result = execute_graph(graph, "t", source="live")["t"]

        assert _column(result, "r") == [3.0, 3.0]
        assert "global_constants" not in _compile_preamble(preamble)

    def test_a_read_the_code_does_not_name_fails_in_its_node(self, tmp_path: Path) -> None:
        graph = _graph(
            [
                make_node(_source("src", _input(tmp_path))),
                make_transform_node(
                    "hidden",
                    'df = src.with_columns(rate=pl.lit(eval("global_constants.rate")))',
                ),
            ],
            [make_edge("src", "hidden")],
            RATE,
        )

        result = execute_graph(graph, "hidden", source="live")["hidden"]

        assert result.status == "error"
        assert "without naming it" in (result.error or "")

    def test_a_data_output_started_under_live_reads_the_batch_scenarios_value(
        self, tmp_path: Path
    ) -> None:
        out = tmp_path / "out.parquet"
        graph = _graph(
            [
                make_node(_source("live_src", _input(tmp_path, "live.parquet"))),
                make_node(_source("batch_src", _input(tmp_path, "batch.parquet"))),
                make_node(
                    {
                        "id": "sw",
                        "data": {
                            "label": "sw",
                            "nodeType": "liveSwitch",
                            "config": {
                                "input_scenario_map": {
                                    "live_src": "live",
                                    "batch_src": "nb_batch",
                                }
                            },
                        },
                    }
                ),
                make_transform_node(
                    "priced", "df = sw.with_columns(rate=pl.lit(global_constants.rate))"
                ),
                make_node(
                    {
                        "id": "sink",
                        "data": {
                            "label": "sink",
                            "nodeType": "dataOutput",
                            "config": make_file_output_config(str(out), format_name="parquet"),
                        },
                    }
                ),
            ],
            [
                make_edge("live_src", "sw"),
                make_edge("batch_src", "sw"),
                make_edge("sw", "priced"),
                make_edge("priced", "sink"),
            ],
            RATE,
        )

        result = write_data_output(graph, "sink", source="live")

        assert result.status == "ok", result.message
        assert pl.read_parquet(out)["rate"].to_list() == [2.5, 2.5]

    def test_model_score_receives_the_runs_constants(self, monkeypatch) -> None:
        import haute._model_scorer as scorer_module

        captured: dict[str, object] = {}

        class _Scorer:
            def __init__(self, **kwargs: object) -> None:
                captured.update(kwargs)

            def score(self, *frames: object) -> object:
                return frames[0]

        monkeypatch.setattr(scorer_module, "ModelScorer", _Scorer)
        constants = GlobalConstantsNamespace([RATE], source="nb_batch")
        node = make_node(
            {
                "id": "ms",
                "data": {
                    "label": "ms",
                    "nodeType": "modelScore",
                    "config": {"sourceType": "run", "run_id": "r1", "task": "regression"},
                },
            }
        )

        _build_node_fn(node, source_names=["src"], preamble_ns={"global_constants": constants})

        assert captured["global_constants"] is constants


class TestDeployedScoring:
    def test_model_score_code_reads_the_live_value(self, tmp_path: Path) -> None:
        from haute.deploy._scorer import score_graph
        from haute.modelling._feature_contract import (
            CONTRACT_FILENAME,
            build_contract,
            save_contract,
        )

        model_path = tmp_path / "model.cbm"
        model_path.write_bytes(b"fake")
        contract_path = tmp_path / CONTRACT_FILENAME
        save_contract(
            build_contract(
                features=["x"],
                feature_types={"x": "Float64"},
                categorical_features=[],
                target_name="y",
                target_type="Float64",
                task="regression",
            ),
            contract_path,
        )
        model = MagicMock()
        model.feature_names_ = ["x"]
        model.predict.return_value = np.array([4.0])
        graph = _graph(
            [
                make_node(
                    {
                        "id": "src",
                        "data": {"label": "src", "nodeType": "apiInput", "config": {"path": ""}},
                    }
                ),
                make_node(
                    {
                        "id": "ms",
                        "data": {
                            "label": "ms",
                            "nodeType": "modelScore",
                            "config": {
                                "sourceType": "run",
                                "run_id": "r1",
                                "artifact_path": "model.cbm",
                                "task": "regression",
                                "code": (
                                    "df = df.with_columns("
                                    "priced=pl.col('prediction') * global_constants.rate)"
                                ),
                            },
                        },
                    }
                ),
                make_node(
                    {
                        "id": "out",
                        "data": {
                            "label": "out",
                            "nodeType": "output",
                            "config": {
                                "outputMapping": [
                                    {
                                        "source_port": "ms",
                                        "source_column": "priced",
                                        "output_path": "$[:].priced",
                                        "enabled": True,
                                    }
                                ],
                                "outputFormat": "json",
                            },
                        },
                    }
                ),
            ],
            [
                make_edge("src", "ms", source_handle="src"),
                make_edge("ms", "out"),
            ],
            RATE,
        )

        with patch("haute._mlflow_io._load_catboost_model", return_value=model):
            result = score_graph(
                graph=graph,
                input_df=pl.DataFrame({"x": [1.0]}),
                input_node_ids=["src"],
                output_node_id="out",
                artifact_paths={
                    "ms__model.cbm": str(model_path),
                    f"ms__{CONTRACT_FILENAME}": str(contract_path),
                },
            )

        assert result["priced"].to_list() == [6.0]


class TestDeployValidation:
    @staticmethod
    def _reader(*constants: GlobalConstant, error: str | None = None) -> PipelineGraph:
        graph = _graph(
            [make_transform_node("t", "df = df.with_columns(r=pl.lit(global_constants.rate))")],
            [],
            *constants,
        )
        return graph.model_copy(update={"global_constants_error": error})

    def test_a_constant_with_no_live_value_is_refused(self) -> None:
        batch_only = GlobalConstant(name="rate", type="float", by_source={"nb_batch": 2.0})

        [error] = _global_constant_errors(self._reader(batch_only))

        assert "'rate' has no live value" in error

    def test_an_undefined_constant_is_refused(self) -> None:
        [error] = _global_constant_errors(self._reader())

        assert "'rate' is read by the deployed pipeline but is not defined" in error

    def test_a_load_error_is_refused_when_constants_are_read(self) -> None:
        [error] = _global_constant_errors(self._reader(error="bad JSON"))

        assert "could not be loaded: bad JSON" in error

    def test_live_and_uniform_values_pass(self) -> None:
        uniform = GlobalConstant(name="rate", type="float", value=1.0)

        assert _global_constant_errors(self._reader(RATE)) == []
        assert _global_constant_errors(self._reader(uniform)) == []

    def test_a_graph_that_reads_no_constant_passes_whatever_the_file(self) -> None:
        graph = _graph([make_transform_node("t", "df = df")], []).model_copy(
            update={"global_constants_error": "bad JSON"}
        )

        assert _global_constant_errors(graph) == []


class TestNamespace:
    def test_a_view_survives_pickling(self) -> None:
        view = restricted_view(
            GlobalConstantsNamespace([RATE], source="nb_batch"), frozenset({"rate"})
        )

        assert pickle.loads(pickle.dumps(view)).rate == 2.5

    def test_a_bound_function_reads_its_view_and_leaves_the_original_alone(self) -> None:
        def reader() -> float:
            return global_constants.rate  # type: ignore[name-defined]  # noqa: F821

        bound = bind_function_view(reader, GlobalConstantsNamespace([RATE], source="live"))

        assert bound() == 1.5
        assert "global_constants" not in reader.__globals__ or (
            reader.__globals__["global_constants"] is not bound.__globals__["global_constants"]
        )


PIPELINE_FILE = """\
import polars as pl
import haute

SYNC = []


def _sync():
    if SYNC:
        SYNC[0].wait(timeout=10)


pipeline = haute.Pipeline("main", global_constants="config/global_constants.json")
global_constants = pipeline.global_constants


@pipeline.data_input(config="config/data_input/base.json")
def base(): ...


@pipeline.polars
def priced(base: pl.LazyFrame) -> pl.LazyFrame:
    _sync()
    return base.with_columns(
        scaled=pl.col("x")
        .map_batches(lambda s: s * global_constants.factor, return_dtype=pl.Float64)
        .over("g"),
        rate=pl.lit(global_constants.rate),
    )


pipeline.connect("base", "priced")
"""


def _write_pipeline(
    tmp_path: Path, constants: list[dict[str, object]], *, sync: bool = False
) -> Path:
    (tmp_path / ".git").mkdir()
    (tmp_path / "haute.toml").write_text('[project]\nname = "constants"\n')
    (tmp_path / "config" / "data_input").mkdir(parents=True)
    data = tmp_path / "base.parquet"
    pl.DataFrame({"g": ["a", "a", "b"], "x": [1.0, 2.0, 3.0]}).write_parquet(data)
    (tmp_path / "config" / "data_input" / "base.json").write_text(
        json.dumps(make_ready_file_input_config(str(data)))
    )
    (tmp_path / "config" / "global_constants.json").write_text(json.dumps({"constants": constants}))
    path = tmp_path / "main.py"
    # The executor's estimator refuses the wait and a grouped callback, so only
    # the standalone threaded runs have them.
    source = PIPELINE_FILE
    if not sync:
        source = source.replace("    _sync()\n", "").replace('\n        .over("g")', "")
    path.write_text(source)
    return path


def _import(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"gconst_{abs(hash(path))}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SPLIT = [
    {"name": "rate", "type": "float", "by_source": {"live": 1.5, "nb_batch": 2.5}},
    {"name": "factor", "type": "integer", "by_source": {"live": 10, "nb_batch": 100}},
]


class TestStandaloneRuns:
    def test_run_and_score_agree_with_the_executor(self, tmp_path: Path) -> None:
        from haute.parser import parse_pipeline_file

        path = _write_pipeline(tmp_path, SPLIT)
        pipeline = _import(path).pipeline
        graph = parse_pipeline_file(path)

        for source in ("live", "nb_batch"):
            standalone = pipeline.run(source=source)
            executed = execute_graph(graph, "priced", source=source)["priced"]
            assert executed.status == "ok", executed.error
            assert standalone.to_dicts() == executed.preview
        assert pipeline.run(source="nb_batch")["scaled"].to_list() == [100.0, 200.0, 300.0]

        scored = pipeline.score(pl.DataFrame({"g": ["c"], "x": [2.0]}))
        assert scored.to_dicts() == [{"g": "c", "x": 2.0, "scaled": 20.0, "rate": 1.5}]

    def test_two_runs_on_two_threads_each_read_their_own_values(self, tmp_path: Path) -> None:
        module = _import(_write_pipeline(tmp_path, SPLIT, sync=True))
        module.SYNC.append(threading.Barrier(2))
        results: dict[str, pl.DataFrame] = {}
        errors: list[BaseException] = []

        def run(source: str) -> None:
            try:
                results[source] = module.pipeline.run(source=source)
            except BaseException as exc:  # pragma: no cover - reported below
                errors.append(exc)

        threads = [threading.Thread(target=run, args=(s,)) for s in ("live", "nb_batch")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        assert errors == []
        assert results["live"]["rate"].to_list() == [1.5] * 3
        assert results["live"]["scaled"].to_list() == [10.0, 20.0, 30.0]
        assert results["nb_batch"]["rate"].to_list() == [2.5] * 3
        assert results["nb_batch"]["scaled"].to_list() == [100.0, 200.0, 300.0]

    def test_a_missing_source_value_fails_the_reading_node(self, tmp_path: Path) -> None:
        live_only = [
            {"name": "rate", "type": "float", "by_source": {"live": 1.5}},
            {"name": "factor", "type": "integer", "value": 10},
        ]
        pipeline = _import(_write_pipeline(tmp_path, live_only)).pipeline

        with pytest.raises(GlobalConstantError, match="no value for source 'nb_batch'"):
            pipeline.run(source="nb_batch")

    def test_a_file_that_fails_to_load_fails_only_reads(self, tmp_path: Path) -> None:
        path = _write_pipeline(tmp_path, SPLIT)
        (tmp_path / "config" / "global_constants.json").write_text("{not json")
        pipeline = _import(path).pipeline

        with pytest.raises(GlobalConstantError, match="could not be loaded"):
            pipeline.run(source="live")

    def test_module_level_reads_outside_a_run_say_where_constants_are_read(
        self, tmp_path: Path
    ) -> None:
        module = _import(_write_pipeline(tmp_path, SPLIT))

        with pytest.raises(GlobalConstantError, match="pipeline.run"):
            _ = module.global_constants.rate


class TestTraceFormulas:
    def test_a_formula_that_reads_a_constant_shows_and_computes_its_value(self) -> None:
        from haute._expression_parser import evaluate_expression

        code = 'df = df.with_columns((pl.col("x") * global_constants.rate).alias("priced"))'
        namespace = {"global_constants": GlobalConstantsNamespace([RATE], source="nb_batch")}

        result = evaluate_expression(code, "priced", {"x": 2.0}, namespace)

        assert result.substituted_text == "2.0 * 2.5"
        assert result.result_value == pytest.approx(5.0)


class TestCodexFindings:
    def test_a_trace_formula_compares_against_a_date_constant(self) -> None:
        import datetime as dt

        from haute._expression_parser import evaluate_expression

        effective = GlobalConstant(name="effective", type="date", value="2024-06-01")
        code = 'df = df.with_columns((pl.col("start") >= global_constants.effective).alias("on"))'
        namespace = {"global_constants": GlobalConstantsNamespace([effective], source="live")}

        result = evaluate_expression(code, "on", {"start": dt.date(2024, 7, 1)}, namespace)

        assert result.substituted_text == "2024-07-01 >= 2024-06-01"
        assert result.result_value is True

    @pytest.mark.parametrize("name", ["restricted_to", "for_graph", "constants"])
    def test_every_valid_name_reads_its_constant(self, name: str) -> None:
        namespace = GlobalConstantsNamespace(
            [GlobalConstant(name=name, type="integer", value=7)], source="live"
        )

        assert getattr(namespace, name) == 7
        assert getattr(restricted_view(namespace, frozenset({name})), name) == 7

    def test_an_integer_beyond_the_editors_exact_range_is_refused(self) -> None:
        from pydantic import ValidationError

        GlobalConstant(name="big", type="integer", value=2**53 - 1)
        with pytest.raises(ValidationError, match="between -9007199254740991 and 9007199254740991"):
            GlobalConstant(name="big", type="integer", value=2**53 + 1)
