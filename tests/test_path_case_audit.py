"""Tests for the runtime input-path case-ambiguity audit.

Companion to the no-normalization pin in
``tests/test_pipeline_runtime_path_validation.py``: haute never rewrites a
user-supplied data path, so the audit's job is to WARN when the spelling in
play is case-ambiguous against the on-disk entries — coexisting case-twins
on a case-sensitive filesystem (Linux), or a requested spelling differing
from the single on-disk entry on a case-insensitive one (macOS/Windows).
Real-twin scenarios only materialise on case-sensitive filesystems, so those
tests probe the filesystem and adapt; the wrapper/dedupe/seam mechanics are
platform-independent.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import haute._path_case_audit as audit_mod
from haute._path_case_audit import (
    case_equivalent_siblings,
    warn_if_case_ambiguous,
    wrap_path_case_audit,
)


@pytest.fixture(autouse=True)
def _fresh_warned_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(audit_mod, "_warned", set())


def _fs_case_insensitive(tmp_path: Path) -> bool:
    probe = tmp_path / "CaseProbe"
    probe.write_text("x", encoding="utf-8")
    try:
        return (tmp_path / "caseprobe").exists()
    finally:
        probe.unlink()


def test_unambiguous_path_yields_no_findings(tmp_path: Path) -> None:
    target = tmp_path / "data" / "rates.csv"
    target.parent.mkdir()
    target.write_text("x", encoding="utf-8")

    assert case_equivalent_siblings(target, stop=tmp_path) == {}
    assert warn_if_case_ambiguous(target, stop=tmp_path) == {}


def test_spelling_differs_from_on_disk_entry_is_flagged(tmp_path: Path) -> None:
    """Requesting ``foo.csv`` when disk holds ``Foo.csv`` is ambiguous.

    On a case-insensitive filesystem this open *works* — and breaks the
    moment the checkout lands on Linux. On a case-sensitive one the open
    fails while a confusable sibling exists. Both directions must warn.
    """
    (tmp_path / "Foo.csv").write_text("x", encoding="utf-8")

    found = case_equivalent_siblings(tmp_path / "foo.csv", stop=tmp_path)

    assert found == {str(tmp_path / "foo.csv"): ["Foo.csv"]}


def test_coexisting_case_twins_are_flagged(tmp_path: Path) -> None:
    """Real twins (Linux): referencing either spelling reports the other."""
    if _fs_case_insensitive(tmp_path):
        pytest.skip("case-insensitive filesystem cannot host coexisting twins")
    (tmp_path / "Foo.csv").write_text("upper", encoding="utf-8")
    (tmp_path / "foo.csv").write_text("lower", encoding="utf-8")

    found = case_equivalent_siblings(tmp_path / "foo.csv", stop=tmp_path)

    assert found == {str(tmp_path / "foo.csv"): ["Foo.csv"]}


def test_ancestor_directories_are_audited_up_to_stop(tmp_path: Path) -> None:
    """An ambiguous DIRECTORY segment is a finding, not just the file."""
    (tmp_path / "Data").mkdir()
    target = tmp_path / "data" / "rates.csv"

    found = case_equivalent_siblings(target, stop=tmp_path)

    assert str(tmp_path / "data") in found
    assert found[str(tmp_path / "data")] == ["Data"]


def test_path_outside_stop_checks_only_final_segment(tmp_path: Path) -> None:
    """Outside the project root only the file's own parent is listed."""
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "Foo.csv").write_text("x", encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()

    found = case_equivalent_siblings(outside / "foo.csv", stop=project)

    assert found == {str(outside / "foo.csv"): ["Foo.csv"]}


def test_warn_logs_once_per_path(tmp_path: Path) -> None:
    (tmp_path / "Foo.csv").write_text("x", encoding="utf-8")
    target = tmp_path / "foo.csv"

    first = warn_if_case_ambiguous(target, stop=tmp_path)
    second = warn_if_case_ambiguous(target, stop=tmp_path)

    assert first != {}
    assert second == {}  # cached — no repeat warning for the same path


def test_wrap_path_case_audit_positional_and_keyword(tmp_path: Path) -> None:
    (tmp_path / "Foo.csv").write_text("x", encoding="utf-8")
    seen: list[str] = []

    def loader(kind: str, path: str) -> str:
        seen.append(path)
        return kind

    wrapped_pos = wrap_path_case_audit(loader, 1, stop=tmp_path)
    assert wrapped_pos("csv", str(tmp_path / "foo.csv")) == "csv"
    assert audit_mod._warned == {str(tmp_path / "foo.csv")}

    wrapped_kw = wrap_path_case_audit(loader, "path", stop=tmp_path)
    assert wrapped_kw("csv", path=str(tmp_path / "Foo.csv")) == "csv"
    assert seen == [str(tmp_path / "foo.csv"), str(tmp_path / "Foo.csv")]


def test_wrap_path_case_audit_missing_argument_is_harmless() -> None:
    wrapped = wrap_path_case_audit(lambda: "ok", 0)
    assert wrapped() == "ok"


def test_resolver_seam_runs_the_audit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_resolve_runtime_data_path` must audit every resolved input path."""
    from haute import _builders

    monkeypatch.chdir(tmp_path)
    (tmp_path / "Foo.csv").write_text("x", encoding="utf-8")

    audited: list[str] = []
    monkeypatch.setattr(
        audit_mod,
        "warn_if_case_ambiguous",
        lambda path, *, stop=None: audited.append(str(path)) or {},
    )

    resolved = _builders._resolve_runtime_data_path("foo.csv")

    assert audited == [resolved]
    assert resolved.endswith("foo.csv")  # spelling preserved, never rewritten
