"""Node and input names cannot collide with support code, nor support code with itself (NAME-03).

Support code is the root and submodel preambles, the preserved blocks and the
``utility.<module>`` files they star-import, inventoried statically.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from haute._pipeline_recovery import load_pipeline_editor_document
from haute._support_code_names import support_code_violations
from haute._types import GraphEdge, GraphNode, NodeData, PipelineGraph, SubmodelDefinition
from haute.errors import ParseError
from haute.parser import parse_pipeline_file
from haute.routes._save_pipeline import SavePipelineService


def _node(node_id: str, node_type: str = "polars", **config: object) -> GraphNode:
    return GraphNode(
        id=node_id, data=NodeData(label=node_id, nodeType=node_type, config=dict(config))
    )


def _quote_input(frame: str) -> GraphNode:
    table = {
        "path": "$[:]",
        "label": frame,
        "emit": True,
        "row_id_column": None,
        "columns": [
            {
                "name": "quote_id",
                "path": "$[:].quote_id",
                "type": "str",
                "status": "Confirmed",
                "selected": True,
                "levels": None,
            }
        ],
    }
    return _node("quotes", "apiInput", path="quote.json", tables=[table])


def _utilities(**modules: str):
    def read(module: str) -> str | None:
        source = modules.get(module)
        return None if source is None else textwrap.dedent(source)

    return read


def _violations(graph: PipelineGraph, **modules: str) -> list[tuple[str, str]]:
    return [(v.kind, v.name) for v in support_code_violations(graph, _utilities(**modules))]


def _with_submodel_preamble(root_preamble: str, child_preamble: str) -> PipelineGraph:
    definition = SubmodelDefinition(
        definitionId="rates",
        file="modules/rates.py",
        graph=PipelineGraph(nodes=[_node("child")], edges=[], preamble=child_preamble),
        inputPorts=[],
        outputPorts=[],
    )
    occurrence = _node("rates_1", "submodel", definitionId="rates", alias="rates_1")
    return PipelineGraph(
        nodes=[occurrence], edges=[], submodels={"rates": definition}, preamble=root_preamble
    )


_RATE_LOOKUP = "def rate_lookup(x):\n    return x\n"
_BAND_A = "def band(x):\n    return x\n"
_BAND_B = "def band(x):\n    return x * 2\n"


@pytest.mark.parametrize(
    ("graph", "modules", "expected"),
    [
        pytest.param(
            PipelineGraph(nodes=[_node("rate_lookup")], edges=[], preamble=_RATE_LOOKUP),
            {},
            [("support_collision", "rate_lookup")],
            id="node-named-like-a-preamble-helper",
        ),
        pytest.param(
            PipelineGraph(
                nodes=[_quote_input("rate_lookup"), _node("rated")],
                edges=[
                    GraphEdge(id="e", source="quotes", target="rated", sourceHandle="rate_lookup")
                ],
                preamble=_RATE_LOOKUP,
            ),
            {},
            [("support_input", "rate_lookup")],
            id="quote-input-table-named-like-a-helper",
        ),
        pytest.param(
            PipelineGraph(
                nodes=[_node("rate_lookup")],
                edges=[],
                preamble="from utility.rates import *\n",
            ),
            {"rates": _RATE_LOOKUP},
            [("support_collision", "rate_lookup")],
            id="node-named-like-a-utility-helper",
        ),
        pytest.param(
            PipelineGraph(
                nodes=[],
                edges=[],
                preamble="from utility.rates import *\nfrom utility.bands import *\n",
            ),
            {"rates": _BAND_A, "bands": _BAND_B},
            [("support_conflict", "band")],
            id="two-utilities-defining-band-differently",
        ),
        pytest.param(
            _with_submodel_preamble(_BAND_A, _BAND_B),
            {},
            [("support_conflict", "band")],
            id="root-and-submodel-preambles-defining-band-differently",
        ),
        pytest.param(
            _with_submodel_preamble(_BAND_A, _BAND_A),
            {},
            [],
            id="one-definition-written-twice-is-one-provenance",
        ),
        pytest.param(
            PipelineGraph(
                nodes=[],
                edges=[],
                preamble="import polars as pl\nfrom utility.rates import *\n",
            ),
            {"rates": "import polars as pl\n"},
            [],
            id="utility-and-preamble-both-import-polars-as-pl",
        ),
        pytest.param(
            PipelineGraph(nodes=[], edges=[], preamble="pipeline = 1\n"),
            {},
            [("support_reserved", "pipeline")],
            id="preamble-binds-a-reserved-name",
        ),
        pytest.param(
            PipelineGraph(nodes=[], edges=[], preamble="from math import *\n"),
            {},
            [("support_unsupported", "")],
            id="star-import-outside-utility",
        ),
        pytest.param(
            PipelineGraph(nodes=[], edges=[], preamble="from utility.rates import *\n"),
            {"rates": "names = ['band']\n__all__ = names\n"},
            [("support_unsupported", "")],
            id="computed-all",
        ),
        pytest.param(
            PipelineGraph(nodes=[], edges=[], preamble="from utility.rates import *\n"),
            {"rates": "try:\n    import numpy as np\nexcept ImportError:\n    np = None\n"},
            [("support_unsupported", "")],
            id="binding-inside-a-block-in-a-utility",
        ),
        pytest.param(
            PipelineGraph(
                nodes=[_node("helper")], edges=[], preamble="from utility.rates import *\n"
            ),
            {
                "rates": "__all__ = ['band']\n\n"
                "def band(x):\n    return x\n\n"
                "def helper():\n    pass\n"
            },
            [],
            id="a-name-left-out-of-all-is-not-exported",
        ),
        pytest.param(
            PipelineGraph(
                nodes=[_node("_private")], edges=[], preamble="from utility.rates import *\n"
            ),
            {"rates": "def _private():\n    pass\n"},
            [],
            id="an-underscore-name-is-not-exported",
        ),
        pytest.param(
            PipelineGraph(
                nodes=[_node("band")], edges=[], preamble="from utility.rates import *\n"
            ),
            {"rates": "from utility.bands import *\n", "bands": _BAND_A},
            [("support_collision", "band")],
            id="transitive-utility-star-import",
        ),
        pytest.param(
            PipelineGraph(nodes=[], edges=[], preamble="from utility.rates import *\n"),
            {"rates": "from utility.bands import *\n", "bands": "from utility.rates import *\n"},
            [("support_unsupported", "")],
            id="star-import-cycle",
        ),
    ],
)
def test_the_support_code_rule(
    graph: PipelineGraph, modules: dict[str, str], expected: list[tuple[str, str]]
) -> None:
    assert _violations(graph, **modules) == expected


def test_messages_name_the_statement_or_the_node() -> None:
    star = support_code_violations(
        PipelineGraph(nodes=[], edges=[], preamble="from math import *\n"), _utilities()
    )
    collision = support_code_violations(
        PipelineGraph(nodes=[_node("rate_lookup")], edges=[], preamble=_RATE_LOOKUP), _utilities()
    )

    assert star[0].message() == (
        "the preamble line 1 star-imports `math`; only `from utility.<module> import *` can be "
        "checked. Import the names you use."
    )
    assert (
        collision[0]
        .message()
        .startswith(
            "Node 'rate_lookup' (the pipeline) takes the name `rate_lookup`, which the preamble "
            "(line 1) binds"
        )
    )


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

_PIPELINE = """\
import haute
import polars as pl
from utility.rates import *

pipeline = haute.Pipeline("main")


@pipeline.polars
def {name}() -> pl.LazyFrame:
    return pl.LazyFrame({{"x": [1]}})
"""


def _project(tmp_path: Path, node: str, rates: str = "import polars as pl\n") -> Path:
    (tmp_path / "utility").mkdir()
    (tmp_path / "utility" / "rates.py").write_text(rates, encoding="utf-8")
    main = tmp_path / "main.py"
    main.write_text(_PIPELINE.format(name=node), encoding="utf-8")
    return main


def test_a_strict_parse_and_the_editor_load_report_a_utility_collision(tmp_path: Path) -> None:
    main = _project(tmp_path, "rate_lookup", rates=_RATE_LOOKUP)

    with pytest.raises(ParseError, match="which utility/rates.py \\(line 1\\) binds"):
        parse_pipeline_file(main)
    document = load_pipeline_editor_document(main, project_root=tmp_path)
    assert [(v.kind, v.name) for v in document.name_violations] == [
        ("support_collision", "rate_lookup")
    ]
    assert document.capabilities.can_save is False


def test_save_refuses_a_node_named_like_a_preamble_helper(tmp_path: Path) -> None:
    graph = PipelineGraph(nodes=[_node("rate_lookup")], edges=[], preamble=_RATE_LOOKUP)

    with pytest.raises(HTTPException) as excinfo:
        SavePipelineService(tmp_path).validate_graph(graph, source_file="main.py")

    assert "takes the name `rate_lookup`, which the preamble (line 1) binds" in excinfo.value.detail


@pytest.fixture()
def utility_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("haute.routes.utility.pipeline_dir", lambda: tmp_path)
    return _project(tmp_path, "rate_lookup")


def test_saving_a_utility_that_adds_a_colliding_helper_is_refused(
    utility_project: Path, client: TestClient
) -> None:
    response = client.put("/api/utility/rates", json={"content": _RATE_LOOKUP})

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "Pipeline 'main.py'" in detail and "Node 'rate_lookup'" in detail
    rates = utility_project.parent / "utility" / "rates.py"
    assert rates.read_text(encoding="utf-8") == "import polars as pl\n"


def test_saving_a_utility_that_adds_an_unrelated_helper_is_accepted(
    utility_project: Path, client: TestClient
) -> None:
    response = client.put("/api/utility/rates", json={"content": "def band(x):\n    return x\n"})

    assert response.status_code == 200, response.text
