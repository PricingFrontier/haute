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
from haute.errors import DeployError
from haute.graph_utils import GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph

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
# validate_deploy — the form against each Workbench node's copy of its tables
# ===========================================================================


def _column(name: str, type_: str) -> dict[str, str]:
    return {"name": name, "type": type_}


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


def _input_copy() -> list[dict[str, Any]]:
    """The Workbench Input's tables as the editor copies them from ``_FORM_TABLES``."""
    return [
        {
            "name": "policy",
            "rows": "one",
            "columns": [_column("limit", "float"), _column("region", "str")],
        },
        {"name": "items", "rows": "many", "columns": [_column("value", "float")]},
    ]


def _output_copy() -> list[dict[str, Any]]:
    return [{"name": "pricing", "rows": "one", "columns": [_column("premium", "float")]}]


def _workbench_project(
    root: Path,
    *,
    enabled: bool = True,
    form: bool = True,
    quote_list: dict[str, Any] | None = None,
) -> None:
    from haute._workbench_form import FormSpec, render_form

    (root / "haute.toml").write_text(
        f"[workbench]\nenabled = {str(enabled).lower()}\n", encoding="utf-8"
    )
    if form:
        spec = FormSpec.model_validate(
            {
                "name": "motor",
                "schema": {"tables": _FORM_TABLES},
                "pages": [
                    {
                        "id": "p",
                        "title": "Sheet 1",
                        "sections": [{"id": "s", "title": "s", "height": 120, "widgets": []}],
                    }
                ],
                "quote_list": quote_list or {},
            }
        )
        (root / "forms").mkdir()
        (root / "forms" / "form.json").write_text(render_form(spec), encoding="utf-8")


def _form_check_errors(
    root: Path,
    input_copy: list[dict[str, Any]],
    output_copy: list[dict[str, Any]],
) -> list[str]:
    """The structural errors ``validate_deploy`` reports for a workbench pipeline in *root*;
    the pipeline is otherwise sound, so they are the form check's alone."""
    from haute.deploy._validators import validate_deploy

    resolved = _make_resolved(
        nodes=[
            _make_node("quote", NodeType.WORKBENCH_INPUT, {"tables": input_copy}),
            _make_node("priced", NodeType.WORKBENCH_OUTPUT, {"tables": output_copy}),
        ],
        edges=[GraphEdge(id="e1", source="quote", target="priced")],
        input_node_ids=["quote"],
        output_node_id="priced",
        config=DeployConfig(pipeline_file=root / "main.py", model_name="m", project_dir=root),
    )
    try:
        validate_deploy(resolved)
    except DeployError as exc:
        return list(exc.context["structural_errors"])
    return []


class TestValidateDeployWorkbenchForm:
    """Each Workbench node must hold the tables the form defines for it (deploy spec)."""

    def test_copies_that_match_the_form_pass(self, tmp_path: Path) -> None:
        _workbench_project(tmp_path)
        assert _form_check_errors(tmp_path, _input_copy(), _output_copy()) == []

    @pytest.mark.parametrize(
        ("node", "change", "named"),
        [
            (
                "input",
                lambda tables: tables[0]["columns"].pop(),
                "column 'policy.region' is in the form but not in the copy",
            ),
            (
                "input",
                lambda tables: tables[0]["columns"].append(_column("extra", "int")),
                "column 'policy.extra' is in the copy but not in the form",
            ),
            (
                "input",
                lambda tables: tables[0]["columns"][0].update(type="int"),
                "column 'policy.limit' is float in the form and int in the copy",
            ),
            (
                "input",
                lambda tables: tables[1].update(rows="one"),
                "table 'items' is many rows per quote in the form and one in the copy",
            ),
            (
                "input",
                lambda tables: tables.pop(),
                "table 'items' is in the form but not in the copy",
            ),
            (
                "input",
                lambda tables: tables.append({"name": "old", "rows": "one", "columns": []}),
                "table 'old' is in the copy but not in the form",
            ),
            (
                "input",
                lambda tables: tables.reverse(),
                "the tables are in a different order in the copy",
            ),
            (
                "input",
                lambda tables: tables[0]["columns"].reverse(),
                "the columns of table 'policy' are in a different order in the copy",
            ),
            (
                "output",
                lambda tables: tables[0]["columns"][0].update(type="int"),
                "column 'pricing.premium' is float in the form and int in the copy",
            ),
        ],
        ids=[
            "column-missing",
            "column-extra",
            "column-type",
            "rows",
            "table-missing",
            "table-extra",
            "table-order",
            "column-order",
            "output-column-type",
        ],
    )
    def test_each_disagreement_is_named_with_the_way_to_fix_it(
        self,
        tmp_path: Path,
        node: str,
        change: Any,
        named: str,
    ) -> None:
        _workbench_project(tmp_path)
        input_copy, output_copy = _input_copy(), _output_copy()
        change(input_copy if node == "input" else output_copy)

        [error] = _form_check_errors(tmp_path, input_copy, output_copy)

        owner = "Workbench Input 'quote'" if node == "input" else "Workbench Output 'priced'"
        assert error.startswith(f"{owner} does not match forms/form.json: ")
        assert named in error
        assert "Save the project in Haute's editor" in error

    def test_a_quote_list_naming_a_column_the_schema_lacks_is_refused(self, tmp_path: Path) -> None:
        _workbench_project(tmp_path, quote_list={"title": {"table": "nope", "column": "x"}})

        [error] = _form_check_errors(tmp_path, _input_copy(), _output_copy())

        assert error.startswith("forms/form.json: The quote list's title names column 'x'")

    def test_a_form_file_that_does_not_exist_is_refused(self, tmp_path: Path) -> None:
        _workbench_project(tmp_path, form=False)

        [error] = _form_check_errors(tmp_path, _input_copy(), _output_copy())

        assert "forms/form.json does not exist" in error

    def test_a_project_whose_workbench_is_off_is_not_checked_against_a_form(
        self, tmp_path: Path
    ) -> None:
        _workbench_project(tmp_path, enabled=False)
        input_copy = _input_copy()
        input_copy.pop()

        assert _form_check_errors(tmp_path, input_copy, _output_copy()) == []

    def test_a_form_that_cannot_be_read_is_reported(self, tmp_path: Path) -> None:
        _workbench_project(tmp_path)
        (tmp_path / "forms" / "form.json").write_text("not json", encoding="utf-8")

        [error] = _form_check_errors(tmp_path, _input_copy(), _output_copy())

        assert "forms/form.json is not JSON" in error

    def test_a_copy_not_as_the_workbench_writes_it_is_reported(self, tmp_path: Path) -> None:
        _workbench_project(tmp_path)

        [error] = _form_check_errors(tmp_path, [{"name": "policy"}], _output_copy())

        assert "The Workbench Input's tables are not as the workbench writes them" in error
