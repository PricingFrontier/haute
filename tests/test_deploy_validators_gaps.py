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
from unittest.mock import patch

import pytest

from haute.deploy._config import DeployConfig, ResolvedDeploy
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
                "cacheMode": "snapshot",
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
