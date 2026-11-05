"""An Explore node's settings live in its sidecar, and its steps in its code (READ-43).

Spec: specs/pipeline-config/high-level.md (Stepped transforms; JSON files Haute writes) and
specs/explore-eda/high-level.md. The decorator carries only ``config=``; the sidecar holds
the overview, pivot formulas, pivots and charts, each pivot placement, formula and chart
series on one line; the steps are rebuilt from the body.
"""

from __future__ import annotations

import json
import runpy
from pathlib import Path
from typing import Any

import pytest

from haute._config_io import collect_node_configs
from haute.codegen import graph_to_code
from haute.errors import ConfigError
from haute.graph_utils import GraphNode, NodeData, PipelineGraph
from haute.parser import parse_pipeline_source
from tests.conftest import make_edge, make_file_input_config
from tests.test_explore_pivots import _formula, _pivot

pytestmark = pytest.mark.usefixtures("_widen_sandbox_root")

SETTINGS: dict[str, Any] = {
    "overview": {"dataset_snapshot": True, "schema": True},
    "pivot_formulas": [_formula()],
    "pivots": [_pivot(formulas=["formula_1"])],
}
STEPS = [{"id": "limit-1", "kind": "limit", "n": 5}]


def _graph(config: dict[str, Any]) -> PipelineGraph:
    quotes = GraphNode(
        id="quotes",
        data=NodeData(
            label="quotes", nodeType="dataInput", config=make_file_input_config("quotes.parquet")
        ),
    )
    look = GraphNode(id="look", data=NodeData(label="look", nodeType="explore", config=config))
    return PipelineGraph(nodes=[quotes, look], edges=[make_edge("quotes", "look")])


def _saved(tmp_path: Path, graph: PipelineGraph) -> str:
    for rel, content in collect_node_configs(graph).items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(content.encode("utf-8"))
    code = graph_to_code(graph, pipeline_name="main")
    (tmp_path / "main.py").write_bytes(code.encode("utf-8"))
    return code


def _loaded(tmp_path: Path, code: str) -> GraphNode:
    graph = parse_pipeline_source(code, _base_dir=tmp_path)
    return next(node for node in graph.nodes if node.id == "look")


def test_the_decorator_names_the_sidecar_and_the_body_holds_the_steps(tmp_path: Path) -> None:
    code = _saved(tmp_path, _graph({**SETTINGS, "steps": STEPS}))

    assert '@pipeline.explore(config="config/explore/look.json")\n' in code
    assert "def look(df: pl.LazyFrame) -> pl.LazyFrame:\n    df = df.head(5)\n" in code
    sidecar = json.loads((tmp_path / "config/explore/look.json").read_text(encoding="utf-8"))
    assert sidecar == SETTINGS
    node = _loaded(tmp_path, code)
    assert {key: node.data.config[key] for key in SETTINGS} == SETTINGS
    assert node.data.config["steps"] == STEPS


def test_each_placement_formula_and_series_is_one_line(tmp_path: Path) -> None:
    _saved(tmp_path, _graph(SETTINGS))
    text = (tmp_path / "config/explore/look.json").read_text(encoding="utf-8")

    lines = text.splitlines()
    for key in ("filter_1", "column_1", "row_1", "value_1", "formula_1"):
        (line,) = [line for line in lines if f'"id": "{key}"' in line]
        assert line.strip().startswith("{") and line.rstrip().rstrip(",").endswith("}"), line
    # A second save writes the same bytes.
    _saved(tmp_path, _graph(SETTINGS))
    assert (tmp_path / "config/explore/look.json").read_text(encoding="utf-8") == text


def test_a_new_node_with_no_settings_saves_an_empty_sidecar_and_a_declaration(
    tmp_path: Path,
) -> None:
    code = _saved(tmp_path, _graph({"steps": []}))

    assert '@pipeline.explore(config="config/explore/look.json")\ndef look(quotes): ...\n' in code
    assert json.loads((tmp_path / "config/explore/look.json").read_text(encoding="utf-8")) == {}
    assert _loaded(tmp_path, code).data.config["steps"] == []


def test_settings_on_the_decorator_are_the_old_form_and_refused(tmp_path: Path) -> None:
    code = _saved(tmp_path, _graph(SETTINGS)).replace(
        '@pipeline.explore(config="config/explore/look.json")',
        '@pipeline.explore(overview={"schema": True})',
    )

    with pytest.raises(ConfigError, match="stores its config in a JSON sidecar") as refused:
        _loaded(tmp_path, code)
    assert refused.value.context["config_folder"] == "config/explore"


def test_an_invalid_overview_in_the_sidecar_fails_the_load(tmp_path: Path) -> None:
    code = _saved(tmp_path, _graph(SETTINGS))
    (tmp_path / "config/explore/look.json").write_text(
        json.dumps({"overview": {"no_such_card": True}}), encoding="utf-8"
    )

    with pytest.raises(ConfigError, match="explore sidecar"):
        _loaded(tmp_path, code)


def test_a_saved_module_runs_on_its_own_and_explore_passes_the_frame_through(
    tmp_path: Path,
) -> None:
    import polars as pl

    pl.DataFrame({"premium": [float(n) for n in range(10)]}).write_parquet(
        tmp_path / "quotes.parquet"
    )
    graph = _graph({**SETTINGS, "steps": STEPS})
    _saved(tmp_path, graph)
    (tmp_path / "haute.toml").write_bytes(b'[project]\nname = "t"\npipeline = "main.py"\n')
    (tmp_path / ".git").mkdir()

    namespace = runpy.run_path(str(tmp_path / "main.py"), run_name="pipeline_under_test")
    frame = namespace["pipeline"].run(source="live")

    # The Explore node is the pipeline's last: its hook ran on the frame it passed on.
    assert frame.height == 5


def test_a_standalone_run_holds_explore_to_its_sidecar_as_the_parse_does(
    tmp_path: Path,
) -> None:
    """Run on its own, the module refuses a settings keyword on the decorator and a malformed
    sidecar, as the parse refuses them (READ-43)."""
    import polars as pl

    pl.DataFrame({"premium": [1.0, 2.0]}).write_parquet(tmp_path / "quotes.parquet")
    code = _saved(tmp_path, _graph(SETTINGS))
    (tmp_path / "haute.toml").write_bytes(b'[project]\nname = "t"\npipeline = "main.py"\n')
    (tmp_path / ".git").mkdir()

    old_form = code.replace(
        '@pipeline.explore(config="config/explore/look.json")',
        '@pipeline.explore(config="config/explore/look.json", overview={"schema": True})',
    )
    (tmp_path / "old_form.py").write_bytes(old_form.encode("utf-8"))
    with pytest.raises(ConfigError, match="decorator keywords 'overview' would be ignored"):
        runpy.run_path(str(tmp_path / "old_form.py"), run_name="pipeline_under_test")

    (tmp_path / "config/explore/look.json").write_text(
        json.dumps({"overview": {"no_such_card": True}}), encoding="utf-8"
    )
    namespace = runpy.run_path(str(tmp_path / "main.py"), run_name="pipeline_under_test")
    with pytest.raises(ConfigError, match="explore sidecar"):
        namespace["pipeline"].run(source="live")


@pytest.mark.parametrize(
    "decorator",
    [
        pytest.param("@pipeline.explore", id="no-config"),
        pytest.param('@pipeline.explore(config="")', id="empty-config"),
    ],
)
def test_a_standalone_run_refuses_an_explore_without_its_sidecar(
    tmp_path: Path, decorator: str
) -> None:
    """An Explore node registered without its ``config=`` sidecar is refused when it runs,
    never called as a plain function whose body hands on nothing (READ-43)."""
    import polars as pl

    pl.DataFrame({"premium": [1.0, 2.0]}).write_parquet(tmp_path / "quotes.parquet")
    code = _saved(tmp_path, _graph(SETTINGS)).replace(
        '@pipeline.explore(config="config/explore/look.json")', decorator
    )
    (tmp_path / "haute.toml").write_bytes(b'[project]\nname = "t"\npipeline = "main.py"\n')
    (tmp_path / ".git").mkdir()
    (tmp_path / "bare.py").write_bytes(code.encode("utf-8"))

    namespace = runpy.run_path(str(tmp_path / "bare.py"), run_name="pipeline_under_test")
    with pytest.raises(ConfigError, match="needs its config= sidecar path to run"):
        namespace["pipeline"].run(source="live")


def test_a_saved_module_with_a_declared_explore_contract_runs_on_its_own(tmp_path: Path) -> None:
    """A declared column contract is the decorator's, beside ``config=``: the generated module
    carries it, parses, and registers in a standalone run (READ-43)."""
    import polars as pl

    pl.DataFrame({"premium": [1.0, 2.0]}).write_parquet(tmp_path / "quotes.parquet")
    # Fan-in metadata is what an Explore node's contract tells the parser beyond its settings.
    contract = {
        "inputs": ["premium"],
        "outputs": [],
        "inputs_by_parent": {"quotes": ["premium"]},
    }
    code = _saved(tmp_path, _graph({**SETTINGS, "contract": contract}))
    (tmp_path / "haute.toml").write_bytes(b'[project]\nname = "t"\npipeline = "main.py"\n')
    (tmp_path / ".git").mkdir()

    assert "contract=" in code
    assert _loaded(tmp_path, code).data.config["contract"]["inputs_by_parent"] == {
        "quotes": ["premium"]
    }
    namespace = runpy.run_path(str(tmp_path / "main.py"), run_name="pipeline_under_test")
    assert namespace["pipeline"].run(source="live").height == 2
