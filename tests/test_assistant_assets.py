"""Tests for the assistant knowledge assets (``haute.assistant._assets``).

Spec: specs/assistant/low-level.md — `_assets.py` / `assets/` rows and
§ Testing: the authoring guide must load non-empty from the installed
package; every packaged exemplar must parse cleanly through the real
parser (the drift guard — a stale exemplar fails CI exactly like a stale
catalog entry); summaries derive from module docstrings; the rendering
matches the live-graph shape ``get_pipeline`` serves.
"""

from __future__ import annotations

import json

import pytest

from haute.assistant import _assets
from haute.assistant._assets import authoring_guide, example_index, load_example

EXPECTED_GRAPH_KEYS = {"name", "description", "nodes", "edges", "preamble", "singletons"}


class TestAuthoringGuide:
    def test_loads_non_empty_from_package_resources(self):
        guide = authoring_guide()
        assert len(guide.strip()) > 500
        assert "node" in guide.lower()

    def test_missing_guide_raises_loudly(self, monkeypatch: pytest.MonkeyPatch):
        authoring_guide.cache_clear()
        try:
            monkeypatch.setattr(
                _assets,
                "_read_resource",
                lambda _resource: (_ for _ in ()).throw(FileNotFoundError("gone")),
            )
            with pytest.raises(RuntimeError, match="missing"):
                authoring_guide()
        finally:
            authoring_guide.cache_clear()

    def test_empty_guide_raises_loudly(self, monkeypatch: pytest.MonkeyPatch):
        authoring_guide.cache_clear()
        try:
            monkeypatch.setattr(_assets, "_read_resource", lambda _resource: "   \n")
            with pytest.raises(RuntimeError, match="empty"):
                authoring_guide()
        finally:
            authoring_guide.cache_clear()

    def test_guide_relies_on_the_packaged_catalog_not_repo_only_docs(self):
        guide = authoring_guide()
        assert "specs/README.md" not in guide
        assert "node catalog" in guide.lower()


class TestExemplars:
    def test_index_is_non_empty_with_docstring_summaries(self):
        index = example_index()
        assert index, "at least one packaged exemplar is required"
        for name, summary in index:
            assert name and summary
            assert not summary.startswith('"""')

    def test_every_exemplar_parses_through_the_real_engine(self):
        """The drift guard: an exemplar that stops parsing fails CI."""

        for name, _summary in example_index():
            result = load_example(name)
            assert "error" not in result, result
            graph = result["graph"]
            assert graph["nodes"], f"exemplar {name!r} must contain nodes"
            assert isinstance(result["narrative"], str) and result["narrative"].strip()

    def test_exemplars_use_specialised_sources_instead_of_direct_file_scans(self):
        for name, resource in _assets._example_resources():
            source = resource.read_text(encoding="utf-8")
            assert "scan_parquet" not in source, name

            graph = load_example(name)["graph"]
            node_types = {node["type"] for node in graph["nodes"]}
            assert node_types & {"apiInput", "dataInput"}, name
            assert "output" in node_types, name
            output_nodes = [node for node in graph["nodes"] if node["type"] == "output"]
            assert all("outputMapping" in node["config"]["keys"] for node in output_nodes), name

        output_sidecars = _assets._examples_root().joinpath("config", "quote_response")
        for resource in output_sidecars.iterdir():
            config = json.loads(resource.read_text(encoding="utf-8"))
            assert config["outputMapping"], resource.name

    def test_rendering_matches_the_live_graph_shape(self):
        """Few-shot format == the format the model reads the live graph in."""

        result = load_example(example_index()[0][0])
        assert set(result["graph"].keys()) == EXPECTED_GRAPH_KEYS
        node = result["graph"]["nodes"][0]
        assert set(node.keys()) == {"id", "type", "label", "config"}

    def test_summary_is_first_docstring_line(self):
        for name, summary in example_index():
            narrative = load_example(name)["narrative"]
            assert narrative.splitlines()[0].strip() == summary

    def test_unknown_name_is_structured_error_listing_valid_names(self):
        result = load_example("not_a_real_example")
        error = result["error"]
        assert error["code"] == "unknown_example"
        for name, _summary in example_index():
            assert name in error["message"]


class TestExecutableBundles:
    EXPECTED = {
        "minimal_batch",
        "minimal_live_quote",
        "continuous_banding",
        "deployment_safety",
        "discrete_banding",
        "invalid_adversarial",
        "live_batch_parity",
        "model_lifecycle",
        "multi_table_live_mapping",
        "online_scenario_optimisation",
        "ratebook_optimisation_apply",
        "reference_join",
        "reusable_submodel",
        "rating_step",
        "trace_audit",
    }
    PORTFOLIO_NODE_TYPES = {
        "discrete_banding": {"banding"},
        "multi_table_live_mapping": {"apiInput", "edgeJoin", "output"},
        "model_lifecycle": {"modelling", "modelScore"},
        "live_batch_parity": {"apiInput", "dataInput", "liveSwitch"},
        "reusable_submodel": {"submodel"},
        "online_scenario_optimisation": {"scenarioExpander", "optimiser"},
        "ratebook_optimisation_apply": {"optimiser", "optimiserApply"},
    }

    def test_bundle_manifests_are_closed_complete_and_content_addressed(self):
        manifests = _assets.example_bundle_manifests()
        assert self.EXPECTED <= {manifest["id"] for manifest in manifests}
        required_roles = {
            "project_configuration",
            "pipeline_source",
            "expected_graph",
            "expected_schema",
            "golden_request",
            "golden_output",
            "boundary_cases",
            "paired_prompts",
            "semantic_assertions",
        }
        for manifest in manifests:
            assert set(manifest) == {
                "schema_version",
                "id",
                "version",
                "summary",
                "source",
                "assertion_tier",
                "review_class",
                "resources",
            }
            assert manifest["schema_version"] == 1
            assert manifest["assertion_tier"] in {"fast", "ordinary", "negative"}
            assert manifest["review_class"] in {"engineering", "pricing"}
            assert manifest["resources"]
            assert all(
                set(resource) == {"path", "role", "sha256"} for resource in manifest["resources"]
            )
            roles = {resource["role"] for resource in manifest["resources"]}
            assert required_roles <= roles
            assert roles & {"synthetic_data", "synthetic_request"}

    def test_domain_bearing_bundles_require_pricing_review(self):
        review_classes = {
            str(manifest["id"]): manifest["review_class"]
            for manifest in _assets.example_bundle_manifests()
        }
        assert {
            name for name, review_class in review_classes.items() if review_class == "pricing"
        } == {
            "model_lifecycle",
            "online_scenario_optimisation",
            "ratebook_optimisation_apply",
        }

    def test_assertion_contract_is_closed_and_declares_evidence(self):
        allowed_checks = {
            "parse",
            "execute",
            "trace",
            "dry_run",
            "training",
            "model_scoring",
            "optimisation",
            "optimiser_apply",
            "deployment_preflight",
            "adversarial_rejection",
        }
        for manifest in _assets.example_bundle_manifests():
            bundle = _assets._bundle_root(str(manifest["id"]))
            assert bundle is not None
            assertions = _assets._read_bundle_json(bundle, "assertions.json")
            assert set(assertions) in (
                {"target", "required_columns", "checks"},
                {"target", "required_columns", "row_count", "checks"},
            )
            checks = assertions["checks"]
            assert isinstance(checks, list) and checks
            assert len(checks) == len(set(checks))
            assert set(checks) <= allowed_checks
            assert "parse" in checks
            if manifest["assertion_tier"] == "fast":
                assert "execute" in checks
            elif manifest["assertion_tier"] == "ordinary":
                assert set(checks) & {
                    "training",
                    "model_scoring",
                    "optimisation",
                    "optimiser_apply",
                    "deployment_preflight",
                }
            else:
                assert "adversarial_rejection" in checks

    def test_manifest_rejects_an_unknown_resource_role(self, tmp_path):
        bundle = tmp_path / "unknown_role"
        bundle.mkdir()
        (bundle / "manifest.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "id": bundle.name,
                    "version": "1",
                    "summary": "test",
                    "source": "pipeline.py",
                    "assertion_tier": "fast",
                    "review_class": "engineering",
                    "resources": [
                        {"path": "pipeline.py", "role": "pipeline_source", "sha256": "a" * 64},
                        {"path": "other", "role": "invented_role", "sha256": "b" * 64},
                    ],
                }
            ),
            encoding="utf-8",
        )

        with pytest.raises(RuntimeError, match="resource role"):
            _assets._read_bundle_manifest(bundle)

    def test_expected_schema_and_assertions_cannot_drift(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ):
        resources = {
            "expected_graph.json": {"node_types": ["output"], "edge_count": 0},
            "expected_schema.json": {"target": "response", "required_columns": ["expected"]},
            "assertions.json": {
                "target": "response",
                "required_columns": ["actual"],
                "checks": ["parse", "execute"],
            },
        }
        monkeypatch.setattr(
            _assets,
            "_read_bundle_json",
            lambda _bundle, path: resources[path],
        )
        monkeypatch.setattr(_assets, "_validate_teaching_resources", lambda _bundle: None)

        with pytest.raises(RuntimeError, match="schema/assertion columns"):
            _assets._validate_bundle_expectations(
                type("Bundle", (), {"name": "drift"})(),
                {"nodes": [{"type": "output"}], "edges": []},
                {"assertion_tier": "fast", "resources": []},
            )

    def test_assertion_check_requires_its_evidence_resource(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ):
        monkeypatch.setattr(
            _assets,
            "_read_bundle_json",
            lambda _bundle, _path: {
                "target": "response",
                "required_columns": ["value"],
                "checks": ["parse", "execute", "trace"],
            },
        )

        with pytest.raises(RuntimeError, match="trace_expectation"):
            _assets._validate_assertions(
                type("Bundle", (), {"name": "missing_trace"})(),
                {"assertion_tier": "fast", "resources": []},
            )

    def test_every_bundle_validates_and_fast_subset_parses(self):
        report = _assets.validate_example_bundles(execute_fast=False)
        assert {item["id"] for item in report} >= self.EXPECTED
        assert all(item["validated"] is True for item in report)
        assert all(item["parsed"] is True for item in report)

    def test_reference_join_stabilizes_positional_golden_output(self):
        result = load_example("reference_join")
        graph = result["graph"]
        assert {node["type"] for node in graph["nodes"]} >= {
            "dataInput",
            "edgeJoin",
            "polars",
            "output",
        }
        assert len(graph["edges"]) == 4

    @pytest.mark.parametrize(
        ("bundle_id", "required_types"),
        PORTFOLIO_NODE_TYPES.items(),
    )
    def test_remaining_portfolio_uses_real_capability_nodes(
        self,
        bundle_id: str,
        required_types: set[str],
    ):
        graph = load_example(bundle_id)["graph"]
        assert {node["type"] for node in graph["nodes"]} >= required_types

    def test_bundle_loader_returns_bounded_attribution_and_live_graph_shape(self):
        result = load_example("continuous_banding")
        assert set(result) == {"name", "attribution", "narrative", "graph"}
        assert result["name"] == "continuous_banding"
        assert result["attribution"] == {
            "id": "continuous_banding",
            "version": "1",
            "summary": "Synthetic continuous banding teaching fixture.",
            "assertion_tier": "fast",
            "review_class": "engineering",
        }
        assert result["narrative"]
        assert set(result["graph"]) == EXPECTED_GRAPH_KEYS
        assert {node["type"] for node in result["graph"]["nodes"]} >= {
            "dataInput",
            "banding",
            "output",
        }

    def test_every_example_response_is_self_contained_and_hides_bundle_resources(self):
        for name, _summary in example_index():
            result = load_example(name)
            assert set(result) == {"name", "attribution", "narrative", "graph"}
            assert result["name"] == name
            assert set(result["attribution"]) == {
                "id",
                "version",
                "summary",
                "assertion_tier",
                "review_class",
            }
            rendered = json.dumps(result)
            assert "expected_graph.json" not in rendered
            assert '"resources"' not in rendered
