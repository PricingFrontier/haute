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
