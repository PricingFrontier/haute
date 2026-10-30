"""Gap-coverage tests for haute.deploy._validators.

Targets the specific untested branches in ``_validators.py``:

  * ``load_test_quote_file`` — non-array JSON raises ValueError, and the
    metadata-stripping behaviour.
  * ``validate_deploy`` — the unparseable-test-quote branch (a ``.json`` file
    in the test-quotes dir that fails to parse is collected as a test-quote
    error rather than crashing the run).
  * ``score_test_quotes`` — the early-return guard when the test-quotes
    directory is ``None`` or not a directory.

Mirrors the helpers/style of tests.test_deploy_contract_integrity.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from haute.deploy._config import DeployConfig, ResolvedDeploy
from haute.deploy._project_modules import ProjectModules
from haute.deploy._request_rows import RequestRows
from haute.errors import DeployError
from haute.graph_utils import GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph
from tests._deploy_helpers import artifact_digests

FIXTURE_DIR = Path("tests/fixtures")
PIPELINE_FILE = FIXTURE_DIR / "pipeline.py"


# ---------------------------------------------------------------------------
# Shared helpers (mirror tests.test_deploy_contract_integrity).
# ---------------------------------------------------------------------------


def _make_node(
    node_id: str,
    node_type: NodeType = NodeType.POLARS,
    config: dict | None = None,
    label: str | None = None,
) -> GraphNode:
    return GraphNode(
        id=node_id,
        data=NodeData(label=label or node_id, nodeType=node_type, config=config or {}),
    )


def _make_resolved(
    *,
    nodes: list[GraphNode] | None = None,
    edges: list[GraphEdge] | None = None,
    input_node_ids: list[str] | None = None,
    output_node_id: str = "output",
    artifacts: dict[str, Path] | None = None,
    input_schema: dict[str, str] | None = None,
    output_schema: dict[str, str] | None = None,
    config: DeployConfig | None = None,
) -> ResolvedDeploy:
    graph = PipelineGraph(nodes=nodes or [], edges=edges or [])
    return ResolvedDeploy(
        config=config
        or DeployConfig(
            pipeline_file=PIPELINE_FILE,
            model_name="test-model",
        ),
        full_graph=graph,
        pruned_graph=graph,
        input_node_ids=input_node_ids or ["api_in"],
        output_node_id=output_node_id,
        artifacts=artifacts or {},
        input_schema=input_schema or {"col": "Int64"},
        output_schema=output_schema or {"result": "Float64"},
        project_modules=ProjectModules(utility=None, unbundled_imports=()),
        request_rows=RequestRows(multi_quote="in_request_order"),
        artifact_digests=artifact_digests(artifacts or {}),
    )


# ===========================================================================
# load_test_quote_file
# ===========================================================================


class TestLoadTestQuoteFile:
    def test_non_array_json_raises(self, tmp_path: Path) -> None:
        """A JSON object (not an array) must raise ValueError."""
        from haute.deploy._validators import load_test_quote_file

        jf = tmp_path / "obj.json"
        jf.write_text(json.dumps({"a": 1}))

        with pytest.raises(ValueError, match="JSON array"):
            load_test_quote_file(jf)

    def test_strips_underscore_metadata_fields(self, tmp_path: Path) -> None:
        """Keys prefixed with ``_`` are dropped from each quote dict."""
        from haute.deploy._validators import load_test_quote_file

        jf = tmp_path / "q.json"
        jf.write_text(
            json.dumps(
                [
                    {"input": {"age": 30, "premium": 100}, "_note": "ignore me"},
                    {"input": {"age": 40}, "_meta": {"x": 1}},
                ]
            )
        )

        cleaned = load_test_quote_file(jf)

        assert cleaned == [{"age": 30, "premium": 100}, {"age": 40}]


# ===========================================================================
# validate_deploy — unparseable test-quote branch (lines 102-104)
# ===========================================================================


class TestValidateDeployUnparseableQuote:
    def test_unparseable_quote_file_raises_deploy_error(self, tmp_path: Path) -> None:
        """A ``.json`` file that fails to parse is collected as a test-quote
        error and surfaced via ``DeployError`` rather than crashing the run.
        """
        from haute.deploy._validators import validate_deploy

        tq_dir = tmp_path / "quotes"
        tq_dir.mkdir()
        # Not valid JSON at all -> json.loads raises inside load_test_quote_file.
        (tq_dir / "broken.json").write_text("{ this is not valid json ]")

        config = DeployConfig(
            pipeline_file=PIPELINE_FILE,
            model_name="broken-parse-model",
            test_quotes_dir=tq_dir,
        )
        inp = _make_node("api_in", node_type=NodeType.API_INPUT)
        out = _make_node("output", node_type=NodeType.OUTPUT)
        edge = GraphEdge(id="e1", source="api_in", target="output")
        resolved = _make_resolved(
            nodes=[inp, out],
            edges=[edge],
            input_node_ids=["api_in"],
            output_node_id="output",
            input_schema={"required_col": "Int64"},
            output_schema={"prediction": "Float64"},
            config=config,
        )

        with pytest.raises(DeployError) as exc_info:
            validate_deploy(resolved)

        msg = str(exc_info.value)
        assert "broken.json" in msg
        assert "could not parse" in msg


class TestDataInputValidationLogging:
    def test_provider_cause_is_logged_but_redacted_from_user_error(self) -> None:
        from haute.deploy._validators import validate_deploy

        inp = _make_node(
            "drivers",
            node_type=NodeType.DATA_INPUT,
            config={
                "inputType": "database",
                "format": "database",
                "query": "select * from drivers",
                "arguments": {},
            },
        )
        out = _make_node("output", node_type=NodeType.OUTPUT)
        resolved = _make_resolved(
            nodes=[inp, out],
            edges=[GraphEdge(id="edge", source="drivers", target="output")],
            input_node_ids=["drivers"],
        )

        with (
            patch("haute.deploy._validators.logger.exception") as logged,
            pytest.raises(DeployError) as exc_info,
        ):
            validate_deploy(resolved)

        assert "ready, valid matching snapshot" in str(exc_info.value)
        logged.assert_called_once()
        assert logged.call_args.args == ("deploy_data_input_validation_failed",)
        assert logged.call_args.kwargs["node_id"] == "drivers"
        assert logged.call_args.kwargs["error_type"]


# ===========================================================================
# score_test_quotes — directory guard (line 163)
# ===========================================================================


class TestScoreTestQuotesGuard:
    def test_returns_empty_when_dir_is_none(self) -> None:
        """No test-quotes dir configured and none passed -> empty list."""
        from haute.deploy._validators import score_test_quotes

        config = DeployConfig(
            pipeline_file=PIPELINE_FILE,
            model_name="no-quotes-model",
            test_quotes_dir=None,
        )
        resolved = _make_resolved(config=config)

        assert score_test_quotes(resolved) == []

    def test_returns_empty_when_dir_does_not_exist(self, tmp_path: Path) -> None:
        """A path that is not an existing directory -> empty list."""
        from haute.deploy._validators import score_test_quotes

        resolved = _make_resolved()
        missing = tmp_path / "does_not_exist"

        assert score_test_quotes(resolved, test_quotes_dir=missing) == []

    def test_returns_empty_when_dir_has_no_json(self, tmp_path: Path) -> None:
        """An existing directory with no ``.json`` files -> empty list."""
        from haute.deploy._validators import score_test_quotes

        resolved = _make_resolved()
        empty_dir = tmp_path / "quotes"
        empty_dir.mkdir()
        (empty_dir / "readme.txt").write_text("not a quote")

        assert score_test_quotes(resolved, test_quotes_dir=empty_dir) == []


class TestConfiguredQuoteDirectoryGate:
    @pytest.mark.parametrize("kind", ["missing", "file", "empty"])
    def test_configured_quote_directory_must_be_usable(self, tmp_path: Path, kind: str) -> None:
        from haute.deploy._validators import validate_deploy

        quote_dir = tmp_path / "quotes"
        if kind == "file":
            quote_dir.write_text("not a directory")
        elif kind == "empty":
            quote_dir.mkdir()
        config = DeployConfig(
            pipeline_file=PIPELINE_FILE, model_name="m", test_quotes_dir=quote_dir
        )
        inp = _make_node("api_in", node_type=NodeType.API_INPUT)
        out = _make_node("output", node_type=NodeType.OUTPUT)
        resolved = _make_resolved(
            nodes=[inp, out],
            edges=[GraphEdge(id="e", source="api_in", target="output")],
            config=config,
        )

        with pytest.raises(DeployError, match="test_quotes.dir"):
            validate_deploy(resolved)

    def test_score_test_quotes_forwards_output_projection(self, tmp_path: Path) -> None:
        from haute.deploy._validators import score_test_quotes

        quote = tmp_path / "q.json"
        quote.write_text('[{"input": {"col": 1}}]')
        config = DeployConfig(
            pipeline_file=PIPELINE_FILE, model_name="m", output_fields=["premium"]
        )
        resolved = _make_resolved(config=config)
        with patch(
            "haute.deploy._validators.score_graph",
            return_value=__import__("polars").DataFrame({"premium": [1]}),
        ) as score:
            score_test_quotes(resolved, tmp_path)
        assert score.call_args.kwargs["output_fields"] == ["premium"]


# ===========================================================================
# validate_deploy — global constants
# ===========================================================================


class TestValidateDeployGlobalConstants:
    def test_a_read_constant_without_a_live_value_is_refused(self) -> None:
        from haute._types import GlobalConstant
        from haute.deploy._validators import validate_deploy

        reader = _make_node(
            "reader", config={"code": "df = df.with_columns(r=pl.lit(global_constants.rate))"}
        )
        resolved = _make_resolved(
            nodes=[
                _make_node("api_in", node_type=NodeType.API_INPUT),
                reader,
                _make_node("output", node_type=NodeType.OUTPUT),
            ],
            edges=[
                GraphEdge(id="e1", source="api_in", target="reader"),
                GraphEdge(id="e2", source="reader", target="output"),
            ],
        )
        resolved.pruned_graph.global_constants = [
            GlobalConstant(name="rate", type="float", by_source={"nb_batch": 2.0})
        ]

        with pytest.raises(DeployError, match="'rate' has no live value"):
            validate_deploy(resolved)


# ===========================================================================
# validate_deploy — the form the Workbench nodes took their tables from, held to the pipeline
# ===========================================================================


_FORM_TABLES: list[dict[str, Any]] = [
    {
        "id": "t_policy",
        "name": "policy",
        "role": "input",
        "rows": "one",
        "columns": [
            {"id": "c_limit", "name": "limit", "type": "float"},
            {"id": "c_region", "name": "region", "type": "str"},
        ],
    },
    {
        "id": "t_items",
        "name": "items",
        "role": "input",
        "rows": "many",
        "columns": [{"id": "c_value", "name": "value", "type": "float"}],
    },
    {
        "id": "t_pricing",
        "name": "pricing",
        "role": "output",
        "rows": "one",
        "columns": [{"id": "c_premium", "name": "premium", "type": "float"}],
    },
]


def _workbench_project(root: Path) -> None:
    from tests._workbench_helpers import enable, write_form

    enable(root)
    write_form(
        root,
        {
            "name": "motor",
            "schema": {"tables": _FORM_TABLES},
            "pages": [
                {
                    "id": "p",
                    "title": "Sheet 1",
                    "width": 1280,
                    "sections": [{"id": "s", "title": "s", "height": 120, "widgets": []}],
                }
            ],
        },
    )


def _form_check_errors(root: Path, edges: list[GraphEdge] | None = None) -> list[str]:
    """The structural errors ``validate_deploy`` reports for a workbench pipeline in *root*,
    its nodes given their tables from the form read as resolution reads it, *edges* joining
    them; the pipeline is otherwise sound, so they are the form check's alone."""
    from haute._workbench_nodes import read_project_form, with_tables
    from haute._workbench_tables import workbench_tables
    from haute.deploy._validators import validate_deploy

    form = read_project_form(root, owner="Workbench Input")
    graph = with_tables(
        PipelineGraph(
            nodes=[
                _make_node("quote", NodeType.WORKBENCH_INPUT),
                _make_node("priced", NodeType.WORKBENCH_OUTPUT),
            ],
            edges=edges
            or [
                GraphEdge(
                    id="e1",
                    source="quote",
                    target="priced",
                    sourceHandle="policy",
                    targetHandle="pricing",
                )
            ],
        ),
        workbench_tables(form.spec),
    )
    resolved = _make_resolved(
        nodes=graph.nodes,
        edges=graph.edges,
        input_node_ids=["quote"],
        output_node_id="priced",
        config=DeployConfig(pipeline_file=root / "main.py", model_name="m", project_dir=root),
    )
    resolved.workbench_form = form
    try:
        validate_deploy(resolved)
    except DeployError as exc:
        return list(exc.context["structural_errors"])
    return []


class TestValidateDeployWorkbenchForm:
    """The form the Workbench nodes took their tables from must fit the pipeline (deploy
    spec): resolution read it strictly, so nothing here compares a copy."""

    def test_a_form_that_fits_the_pipeline_passes(self, tmp_path: Path) -> None:
        _workbench_project(tmp_path)
        assert _form_check_errors(tmp_path) == []

    @pytest.mark.parametrize(
        ("edge", "named"),
        [
            (
                GraphEdge(
                    id="e",
                    source="quote",
                    target="priced",
                    sourceHandle="gone",
                    targetHandle="pricing",
                ),
                "'priced' takes table 'gone' of Workbench Input 'quote', which rating/workbench "
                "does not have.",
            ),
            (
                GraphEdge(
                    id="e",
                    source="quote",
                    target="priced",
                    sourceHandle="policy",
                    targetHandle="gone",
                ),
                "'quote' fills table 'gone' of Workbench Output 'priced', which "
                "rating/workbench does not have.",
            ),
        ],
        ids=["input", "output"],
    )
    def test_a_connection_to_a_table_the_form_does_not_have_is_named(
        self, tmp_path: Path, edge: GraphEdge, named: str
    ) -> None:
        # As a pipeline file written by hand, or saved before the form dropped the table,
        # can hold: resolution refuses it before anything runs.
        from haute._workbench_nodes import connection_errors, read_project_form, with_tables
        from haute._workbench_tables import workbench_tables

        _workbench_project(tmp_path)
        form = read_project_form(tmp_path, owner="Workbench Input")
        graph = with_tables(
            PipelineGraph(
                nodes=[
                    _make_node("quote", NodeType.WORKBENCH_INPUT),
                    _make_node("priced", NodeType.WORKBENCH_OUTPUT),
                ],
                edges=[edge],
            ),
            workbench_tables(form.spec),
        )

        assert connection_errors(graph, "rating/workbench") == [named]

    def test_a_table_whose_own_column_takes_an_identity_name_is_refused(
        self, tmp_path: Path
    ) -> None:
        _FORM_TABLES[1]["columns"].append({"id": "c_option", "name": "quote_option", "type": "str"})
        try:
            _workbench_project(tmp_path)
        finally:
            _FORM_TABLES[1]["columns"].pop()

        assert _form_check_errors(tmp_path) == [
            "rating/workbench: items.quote_option has a name Haute keeps for the quote's "
            "identity in every table: rename it, and the table takes quote_reference and "
            "quote_option at the next save."
        ]

    def test_a_sheet_naming_a_column_the_schema_does_not_hold_is_refused(
        self, tmp_path: Path
    ) -> None:
        import json

        _workbench_project(tmp_path)
        # A field on a column one branch dropped, which another's merge kept showing.
        sheet = tmp_path / "rating" / "workbench" / "sheets" / "sheet_1.json"
        layout = tmp_path / "rating" / "workbench" / "sheets" / "sheet_1.layout.json"
        held = json.loads(sheet.read_bytes())
        held["sections"][0]["components"] = [
            {
                "id": "collection_kept",
                "type": "collection",
                "title": "Cover",
                "fields": ["policy.gone"],
            }
        ]
        sheet.write_bytes((json.dumps(held) + "\n").encode())
        placed = json.loads(layout.read_bytes())
        placed["sections"]["s"]["components"] = {
            "collection_kept": {"x": 0, "y": 0, "w": 200, "h": 100}
        }
        layout.write_bytes((json.dumps(placed) + "\n").encode())

        assert _form_check_errors(tmp_path) == [
            'rating/workbench/sheets/sheet_1.json, the Collection "Cover" names policy.gone, '
            "which the schema does not hold."
        ]

    def test_a_pipeline_without_a_workbench_node_reads_no_form(self, tmp_path: Path) -> None:
        from haute.deploy._validators import _workbench_form_errors

        resolved = _make_resolved(nodes=[_make_node("api_in", NodeType.API_INPUT)])
        assert resolved.workbench_form is None
        assert _workbench_form_errors(resolved) == []
