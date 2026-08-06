"""Source-linked, disposable project knowledge contracts (ASSIST-A07)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest


def _project(root: Path) -> None:
    (root / "haute.toml").write_text(
        '[project]\npipeline = "."\n'
        '[assistant]\nprovider = "openai"\nmodel = "m"\n'
        'base_url = "https://api.example/v1"\n'
        '[assistant.egress]\ntrust = "organization"\nmax_sensitivity = "internal"\n'
        "allow_project_knowledge = true\nallow_executable_source = false\n"
        "allow_row_samples = false\n",
        encoding="utf-8",
    )
    (root / "main.py").write_text(
        "import haute\npipeline = haute.Pipeline('main')\n",
        encoding="utf-8",
    )
    docs = root / "docs"
    docs.mkdir()
    (docs / "terms.md").write_text(
        "Sensitivity: internal\n\nRegion means the declared rating territory.\n",
        encoding="utf-8",
    )
    (docs / "unknown.md").write_text(
        "Unlabelled commercial notes must be restricted.\n",
        encoding="utf-8",
    )


def test_items_are_source_linked_label_unknown_restricted_and_filter_by_policy(tmp_path: Path):
    from haute.assistant._config import EgressPolicy
    from haute.assistant._project_knowledge import build_project_knowledge

    _project(tmp_path)
    policy = EgressPolicy(
        trust="organization",
        max_sensitivity="internal",
        allow_project_knowledge=True,
        allow_executable_source=False,
        allow_row_samples=False,
    )
    view = build_project_knowledge(tmp_path, "main.py", policy=policy)

    assert view.items
    assert any(item.source == "docs/terms.md" for item in view.items)
    assert all(item.source != "docs/unknown.md" for item in view.items)
    for item in view.items:
        assert len(item.source_digest) == 64
        assert item.extraction_version
        assert item.evidence_class in {"project_fact", "untrusted_document"}
    assert view.excluded_by_policy == ("docs/unknown.md",)


def test_undecodable_document_fails_with_the_project_relative_source(tmp_path: Path):
    from haute.assistant._config import EgressPolicy
    from haute.assistant._project_knowledge import ProjectKnowledgeError, build_project_knowledge

    _project(tmp_path)
    (tmp_path / "docs" / "broken.md").write_bytes(b"\xff\xfe\x00broken")
    policy = EgressPolicy(
        trust="organization",
        max_sensitivity="internal",
        allow_project_knowledge=True,
        allow_executable_source=False,
        allow_row_samples=False,
    )
    with pytest.raises(ProjectKnowledgeError, match=r"docs/broken\.md"):
        build_project_knowledge(tmp_path, "main.py", policy=policy)


def test_changed_and_removed_sources_invalidate_metadata_cache(tmp_path: Path):
    from haute.assistant._config import EgressPolicy
    from haute.assistant._project_knowledge import build_project_knowledge

    _project(tmp_path)
    policy = EgressPolicy(
        trust="organization",
        max_sensitivity="restricted",
        allow_project_knowledge=True,
        allow_executable_source=False,
        allow_row_samples=False,
    )
    first = build_project_knowledge(tmp_path, "main.py", policy=policy)
    cache_path = tmp_path / ".haute" / "assistant" / "knowledge" / "index-v1.json"
    first_cache = json.loads(cache_path.read_text(encoding="utf-8"))

    (tmp_path / "docs" / "terms.md").write_text(
        "Sensitivity: public\n\nUpdated terminology.\n",
        encoding="utf-8",
    )
    (tmp_path / "docs" / "unknown.md").unlink()
    second = build_project_knowledge(tmp_path, "main.py", policy=policy)
    second_cache = json.loads(cache_path.read_text(encoding="utf-8"))

    assert first.source_manifest != second.source_manifest
    assert first_cache != second_cache
    assert "docs/unknown.md" not in second.source_manifest


def test_deleting_cache_only_changes_warmup_and_rebuild_is_source_equivalent(tmp_path: Path):
    from haute.assistant._config import EgressPolicy
    from haute.assistant._project_knowledge import build_project_knowledge

    _project(tmp_path)
    policy = EgressPolicy(
        trust="organization",
        max_sensitivity="restricted",
        allow_project_knowledge=True,
        allow_executable_source=False,
        allow_row_samples=False,
    )
    first = build_project_knowledge(tmp_path, "main.py", policy=policy)
    cache_path = tmp_path / ".haute" / "assistant" / "knowledge" / "index-v1.json"
    cache_path.unlink()
    rebuilt = build_project_knowledge(tmp_path, "main.py", policy=policy)

    assert rebuilt.items == first.items
    assert rebuilt.source_manifest == first.source_manifest
    assert cache_path.exists()
    cache_text = cache_path.read_text(encoding="utf-8")
    assert "Region means" not in cache_text


def test_query_is_bounded_attributed_and_never_names_excluded_sources(tmp_path: Path):
    from haute.assistant._config import EgressPolicy
    from haute.assistant._project_knowledge import (
        build_project_knowledge,
        query_project_knowledge,
    )

    _project(tmp_path)
    policy = EgressPolicy(
        trust="organization",
        max_sensitivity="internal",
        allow_project_knowledge=True,
        allow_executable_source=False,
        allow_row_samples=False,
    )
    view = build_project_knowledge(tmp_path, "main.py", policy=policy)
    results = query_project_knowledge(view, "rating territory", limit=2)

    assert len(results) == 1
    assert results[0]["source"] == "docs/terms.md"
    assert results[0]["evidence_class"] == "untrusted_document"
    assert len(results[0]["source_digest"]) == 64
    assert "unknown.md" not in repr(results)


def test_tool_applies_policy_before_returning_project_content(tmp_path: Path, monkeypatch):
    from haute.assistant._tools import get_project_knowledge

    _project(tmp_path)
    monkeypatch.chdir(tmp_path)
    result = get_project_knowledge("main.py", "rating territory")

    assert result["items"][0]["source"] == "docs/terms.md"
    assert result["excluded_by_policy_count"] == 1
    assert "unknown.md" not in repr(result)


def test_document_symlink_cannot_read_outside_project(tmp_path: Path):
    from haute.assistant._config import EgressPolicy
    from haute.assistant._project_knowledge import build_project_knowledge

    _project(tmp_path)
    outside = tmp_path.parent / f"{tmp_path.name}-outside-docs"
    outside.mkdir()
    (outside / "secret.md").write_text(
        "Sensitivity: public\n\nOUTSIDE_CANARY",
        encoding="utf-8",
    )
    docs = tmp_path / "docs"
    for child in docs.iterdir():
        child.unlink()
    docs.rmdir()
    try:
        os.symlink(outside, docs, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable on this platform")
    policy = EgressPolicy(
        trust="organization",
        max_sensitivity="restricted",
        allow_project_knowledge=True,
        allow_executable_source=False,
        allow_row_samples=False,
    )

    with pytest.raises(ValueError, match="inside the project root"):
        build_project_knowledge(tmp_path, "main.py", policy=policy)


def test_cache_symlink_cannot_write_outside_project(tmp_path: Path):
    from haute.assistant._config import EgressPolicy
    from haute.assistant._project_knowledge import build_project_knowledge

    _project(tmp_path)
    outside = tmp_path.parent / f"{tmp_path.name}-outside-cache"
    outside.mkdir()
    state = tmp_path / ".haute"
    try:
        os.symlink(outside, state, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable on this platform")
    policy = EgressPolicy(
        trust="organization",
        max_sensitivity="restricted",
        allow_project_knowledge=True,
        allow_executable_source=False,
        allow_row_samples=False,
    )

    with pytest.raises(ValueError, match="cache must stay inside"):
        build_project_knowledge(tmp_path, "main.py", policy=policy)
    assert not (outside / "assistant").exists()
