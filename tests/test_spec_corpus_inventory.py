from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.spec_corpus_inventory import (
    SpecCorpusError,
    build_inventory,
    discover_spec_files,
    load_coverage,
)


def write(path: Path, text: str = "one\ntwo\nthree\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def corpus(tmp_path: Path) -> None:
    write(tmp_path / "specs/auth/high-level.md")
    write(tmp_path / "specs/auth/low-level.md")
    write(tmp_path / "specs/GOVERNANCE.md")
    write(tmp_path / "specs/config.toml", "x = 1\n")
    write(tmp_path / "specs/roadmap/next.md")
    write(tmp_path / "specs/roadmap/ignored.txt")
    write(tmp_path / "specs/auth/ignored.md")


def test_scope_categories_and_complete_component_pairs(tmp_path: Path) -> None:
    corpus(tmp_path)
    inventory = build_inventory(tmp_path)
    assert [(item["path"], item["category"]) for item in inventory["files"]] == [
        ("specs/GOVERNANCE.md", "governance"),
        ("specs/auth/high-level.md", "component_high"),
        ("specs/auth/low-level.md", "component_low"),
        ("specs/config.toml", "governance"),
        ("specs/roadmap/next.md", "roadmap"),
    ]
    assert inventory["summary"]["components"] == {"pairs": 1, "high": 1, "low": 1}
    assert inventory["summary"]["markdown"]["total"] == {"files": 4, "lines": 12}
    (tmp_path / "specs/auth/low-level.md").unlink()
    with pytest.raises(SpecCorpusError, match="both"):
        discover_spec_files(tmp_path)


def test_digest_reflects_working_tree_add_modify_and_delete(tmp_path: Path) -> None:
    corpus(tmp_path)
    original = build_inventory(tmp_path)["snapshot"]["digest"]
    untracked = tmp_path / "specs/roadmap/untracked.md"
    write(untracked, "untracked\n")
    added = build_inventory(tmp_path)["snapshot"]["digest"]
    write(untracked, "changed\n")
    modified = build_inventory(tmp_path)["snapshot"]["digest"]
    untracked.unlink()
    deleted = build_inventory(tmp_path)["snapshot"]["digest"]
    assert len({original, added, modified}) == 3
    assert deleted == original


def test_coverage_validates_exact_file_set_and_ranges(tmp_path: Path) -> None:
    corpus(tmp_path)
    records = [
        type(
            "R",
            (),
            {"path": item["path"], "line_count": 3 if item["path"].endswith(".md") else 1},
        )
        for item in build_inventory(tmp_path)["files"]
    ]
    coverage = tmp_path / "coverage.toml"
    coverage.write_text(
        "version = 1\n[[file]]\npath = 'specs/auth/high-level.md'\nstate = 'full'\n",
        encoding="utf-8",
    )
    with pytest.raises(SpecCorpusError, match="missing"):
        load_coverage(coverage, records)
    partial = "\n".join(
        f"[[file]]\npath = '{record.path}'\nstate = 'partial'\nranges = ['1-3']"
        for record in records
    )
    coverage.write_text("version = 1\n" + partial, encoding="utf-8")
    with pytest.raises(SpecCorpusError, match="whole"):
        load_coverage(coverage, records)
    full = "\n".join(f"[[file]]\npath = '{record.path}'\nstate = 'full'" for record in records)
    coverage.write_text(
        "version = 1\n" + full + "\n[[file]]\npath = 'specs/auth/high-level.md'\nstate = 'full'",
        encoding="utf-8",
    )
    with pytest.raises(SpecCorpusError, match="duplicate"):
        load_coverage(coverage, records)
    unread = "\n".join(f"[[file]]\npath = '{record.path}'\nstate = 'unread'" for record in records)
    coverage.write_text(
        "version = 1\n" + unread + "\n[[file]]\npath = 'other.md'\nstate = 'full'",
        encoding="utf-8",
    )
    with pytest.raises(SpecCorpusError, match="outside"):
        load_coverage(coverage, records)
    coverage.write_text(
        "version = 1\n" + full.replace("state = 'full'", "state = 'unknown'", 1),
        encoding="utf-8",
    )
    with pytest.raises(SpecCorpusError, match="invalid coverage state"):
        load_coverage(coverage, records)
    invalid_range = partial.replace("['1-3']", "['2-3', '1-1']", 1)
    coverage.write_text("version = 1\n" + invalid_range, encoding="utf-8")
    with pytest.raises(SpecCorpusError, match="sorted"):
        load_coverage(coverage, records)


@pytest.mark.parametrize(
    ("replacement", "message"),
    [
        ("version = true", "version"),
        ("state = 'full'\nranges = ['1-1']", "only allowed"),
        ("state = 'partial'\nranges = ['0-1']", "out of bounds"),
        ("state = 'partial'\nranges = ['1-2', '2-2']", "non-overlapping"),
    ],
    ids=["boolean-version", "ranges-on-full", "out-of-bounds", "overlap"],
)
def test_coverage_rejects_malformed_state_and_range_combinations(
    tmp_path: Path,
    replacement: str,
    message: str,
) -> None:
    corpus(tmp_path)
    items = build_inventory(tmp_path)["files"]
    entries = [f"[[file]]\npath = '{item['path']}'\nstate = 'unread'" for item in items]
    document = "version = 1\n" + "\n".join(entries)
    if replacement.startswith("version"):
        document = document.replace("version = 1", replacement)
    else:
        document = document.replace("state = 'unread'", replacement, 1)
    coverage = tmp_path / "coverage.toml"
    coverage.write_text(document, encoding="utf-8")

    with pytest.raises(SpecCorpusError, match=message):
        build_inventory(tmp_path, coverage)


def test_coverage_totals_do_not_treat_partial_as_full(tmp_path: Path) -> None:
    corpus(tmp_path)
    paths = [item["path"] for item in build_inventory(tmp_path)["files"]]
    entries = []
    for path in paths:
        state = "partial" if path.endswith("high-level.md") else "unread"
        ranges = "\nranges = ['1-1']" if state == "partial" else ""
        entries.append(f"[[file]]\npath = '{path}'\nstate = '{state}'{ranges}")
    coverage = tmp_path / "coverage.toml"
    coverage.write_text("version = 1\n" + "\n".join(entries), encoding="utf-8")
    summary = build_inventory(tmp_path, coverage)["summary"]["coverage"]
    assert summary["files_by_state"]["full"] == 0
    assert summary["fully_read_files"] == 0
    assert summary["reviewed_lines_by_state"]["partial"] == 1


def test_cli_json_is_deterministic(tmp_path: Path) -> None:
    corpus(tmp_path)
    script = Path(__file__).parents[1] / "scripts/spec_corpus_inventory.py"
    command = [sys.executable, str(script), "--root", str(tmp_path), "--format", "json"]
    first = subprocess.run(command, capture_output=True, text=True, check=True).stdout
    second = subprocess.run(command, capture_output=True, text=True, check=True).stdout
    assert first == second
    assert json.loads(first)["snapshot"]["policy"] == (
        "working-tree on-disk bytes; staged and unstaged content present "
        "there plus untracked in-scope files are included"
    )
