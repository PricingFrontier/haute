"""Direct contract tests for the one anchoring rule.

The property tests cover broad invariants; these tests pin the specific
decision points that determine which file preview, trace, run and deploy touch,
and when a relative path names two copies that differ.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from haute._path_resolution import (
    MalformedRuntimePathError,
    RuntimePathOutsideProjectError,
    RuntimePathWrittenOnWindowsError,
    copies_differ,
    node_input_path_copies,
    portable_path_refusal,
    resolve_node_input_path,
)
from tests._project import use_project, work_elsewhere


def _layout(tmp_path: Path) -> tuple[Path, Path]:
    project_root = tmp_path / "project"
    pipeline_dir = project_root / "pipelines"
    pipeline_dir.mkdir(parents=True)
    return project_root, pipeline_dir


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _link(link: Path, folder: Path) -> None:
    """Make *link* a link to *folder*: a junction on Windows, which needs no privilege."""
    link.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        import _winapi

        _winapi.CreateJunction(str(folder), str(link))
    else:
        link.symlink_to(folder, target_is_directory=True)


def test_the_project_candidate_wins_when_both_exist(tmp_path: Path) -> None:
    project_root, pipeline_dir = _layout(tmp_path)
    _write(project_root / "data.json", "project")
    _write(pipeline_dir / "data.json", "pipeline")

    resolved = resolve_node_input_path(
        "data.json", project_root=project_root, pipeline_dir=pipeline_dir
    )

    assert resolved == (project_root / "data.json").resolve()


def test_a_glob_pattern_resolves_where_it_matches_a_file(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    pipeline_dir = project_root / "rating"
    _write(pipeline_dir / "files" / "data" / "a.parquet", "x")

    resolved = resolve_node_input_path(
        "files/data/*.parquet", project_root=project_root, pipeline_dir=pipeline_dir
    )

    assert resolved == pipeline_dir / "files" / "data" / "*.parquet"


def test_missing_candidates_keep_the_project_root_candidate(tmp_path: Path) -> None:
    project_root, pipeline_dir = _layout(tmp_path)

    resolved = resolve_node_input_path(
        "missing.json", project_root=project_root, pipeline_dir=pipeline_dir
    )

    assert resolved == (project_root / "missing.json").resolve()


def test_a_source_file_outside_the_installed_project_gives_it_no_root_of_its_own(
    tmp_path: Path,
) -> None:
    """The root is the installed project's: a pipeline file elsewhere does not widen it,
    so a relative path names the project's file, never one beside the outside pipeline."""
    project = tmp_path / "project"
    project.mkdir()
    use_project(project)
    source_file = _write(tmp_path / "external" / "pipelines" / "pricing.py", "# pipeline")
    _write(source_file.parent / "inputs" / "data.parquet", "outside")

    resolved = resolve_node_input_path("inputs/data.parquet", source_file=source_file)

    assert resolved == (project / "inputs" / "data.parquet").resolve()


def test_relative_source_file_uses_explicit_project_root(tmp_path: Path) -> None:
    project_root, _pipeline_dir = _layout(tmp_path)

    resolved = resolve_node_input_path(
        "inputs/data.parquet", project_root=project_root, source_file="pipelines/pricing.py"
    )

    assert resolved == (project_root / "inputs" / "data.parquet").resolve()


def test_implicit_root_uses_configured_current_project(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    use_project(project_root)

    resolved = resolve_node_input_path("inputs/data.parquet")

    assert resolved == (project_root / "inputs" / "data.parquet").resolve()


def test_relative_source_does_not_override_selected_project_with_working_directory(
    tmp_path: Path,
) -> None:
    working_directory = tmp_path / "working-directory"
    _write(working_directory / "pipelines" / "pricing.py", "# pipeline")
    selected_root = tmp_path / "selected-project"
    selected_root.mkdir()

    use_project(selected_root)
    work_elsewhere(working_directory)

    resolved = resolve_node_input_path("inputs/data.parquet", source_file="pipelines/pricing.py")

    assert resolved == (selected_root / "inputs" / "data.parquet").resolve()


def test_a_relative_source_file_anchors_its_pipeline_dir(tmp_path: Path) -> None:
    project_root, pipeline_dir = _layout(tmp_path)
    expected = _write(pipeline_dir / "inputs" / "data.parquet", "pipeline")

    resolved = resolve_node_input_path(
        "inputs/data.parquet", project_root=project_root, source_file="pipelines/pricing.py"
    )

    assert resolved == expected.resolve()


def test_empty_source_file_falls_back_to_project_candidate(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()

    resolved = resolve_node_input_path(
        "inputs/data.parquet", project_root=project_root, source_file=""
    )

    assert resolved == (project_root / "inputs" / "data.parquet").resolve()


def test_pipeline_candidate_outside_root_falls_back_to_project_candidate(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    outside_pipeline_dir = tmp_path / "outside" / "pipelines"
    _write(outside_pipeline_dir / "data.json", "outside")
    _write(project_root / "data.json", "{}")

    resolved = resolve_node_input_path(
        "data.json", project_root=project_root, pipeline_dir=outside_pipeline_dir
    )

    assert resolved == (project_root / "data.json").resolve()


def test_backslash_path_is_normalized_before_resolution(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    expected = _write(project_root / "nested" / "data.json", "{}")

    resolved = resolve_node_input_path(r"nested\data.json", project_root=project_root)

    assert resolved == expected.resolve()


def test_embedded_null_byte_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(MalformedRuntimePathError, match="null byte"):
        resolve_node_input_path("bad\x00name.json", project_root=tmp_path)


def test_absolute_raw_path_outside_root_is_rejected(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    outside = _write(tmp_path / "outside.json", "{}")

    with pytest.raises(RuntimePathOutsideProjectError, match="outside the project root"):
        resolve_node_input_path(outside, project_root=project_root)


@pytest.mark.parametrize(
    "raw_path",
    ["C:/outside.json", r"C:\outside.json", "C:outside.json"],
)
def test_windows_drive_path_never_becomes_project_relative(
    tmp_path: Path,
    raw_path: str,
) -> None:
    """Absolute here (on Windows), it is outside the project; where it is not absolute,
    it is said to be written on Windows, never read as a path inside the project."""
    project_root = tmp_path / "project"
    project_root.mkdir()

    with pytest.raises(RuntimePathOutsideProjectError) as refused:
        resolve_node_input_path(raw_path, project_root=project_root)

    if Path(raw_path.replace("\\", "/")).is_absolute():
        assert "outside the project root" in str(refused.value)
    else:
        assert isinstance(refused.value, RuntimePathWrittenOnWindowsError)
        assert f"Path {raw_path!r} was written on Windows" in str(refused.value)


def test_absolute_raw_path_inside_root_is_allowed(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    inside = _write(project_root / "inside.json", "{}")

    resolved = resolve_node_input_path(inside, project_root=project_root)

    assert resolved == inside.resolve()


# ---------------------------------------------------------------------------
# Two copies of one relative path
# ---------------------------------------------------------------------------


def _copies(raw: str, project_root: Path, pipeline_dir: Path) -> tuple[Path, Path] | None:
    return node_input_path_copies(raw, project_root=project_root, pipeline_dir=pipeline_dir)


def test_a_path_in_both_places_names_both_copies_the_rule_s_first(tmp_path: Path) -> None:
    project_root, pipeline_dir = _layout(tmp_path)
    _write(project_root / "data.json", "project")
    _write(pipeline_dir / "data.json", "pipeline")

    copies = _copies("data.json", project_root, pipeline_dir)

    assert copies == (
        resolve_node_input_path("data.json", project_root=project_root, pipeline_dir=pipeline_dir),
        pipeline_dir / "data.json",
    )
    assert copies is not None and copies_differ(*copies, project_root=project_root)


@pytest.mark.parametrize("where", ["project", "pipeline", "nowhere"])
def test_a_path_in_at_most_one_place_has_no_copies(tmp_path: Path, where: str) -> None:
    project_root, pipeline_dir = _layout(tmp_path)
    if where != "nowhere":
        _write((project_root if where == "project" else pipeline_dir) / "data.json", "{}")

    assert _copies("data.json", project_root, pipeline_dir) is None


def test_an_absolute_path_has_no_copies(tmp_path: Path) -> None:
    project_root, pipeline_dir = _layout(tmp_path)
    absolute = _write(project_root / "data.json", "{}")
    _write(pipeline_dir / "data.json", "{}")

    assert _copies(str(absolute), project_root, pipeline_dir) is None


def test_one_entry_reached_twice_is_one_copy(tmp_path: Path) -> None:
    """The pipeline directory is the project root, or a link leads it there."""
    project_root = tmp_path / "project"
    _write(project_root / "data.json", "{}")
    assert _copies("data.json", project_root, project_root) is None

    # The pipeline directory is a link inside the project back to its root.
    _link(project_root / "linked", project_root)
    assert _copies("data.json", project_root, project_root / "linked") is None


def test_identical_file_copies_do_not_differ(tmp_path: Path) -> None:
    project_root, pipeline_dir = _layout(tmp_path)
    _write(project_root / "data.json", '{"factor": 1}')
    _write(pipeline_dir / "data.json", '{"factor": 1}')

    copies = _copies("data.json", project_root, pipeline_dir)

    assert copies is not None and not copies_differ(*copies, project_root=project_root)


@pytest.mark.parametrize("change", ["bytes", "extra file", "missing file", "renamed file"])
def test_folder_copies_differ_by_any_file(tmp_path: Path, change: str) -> None:
    project_root, pipeline_dir = _layout(tmp_path)
    for folder in (project_root, pipeline_dir):
        _write(folder / "rates" / "region=N" / "part.parquet", "north")
        _write(folder / "rates" / "region=S" / "part.parquet", "south")
    copies = _copies("rates", project_root, pipeline_dir)
    assert copies is not None and not copies_differ(*copies, project_root=project_root)

    changed = pipeline_dir / "rates" / "region=S" / "part.parquet"
    if change == "bytes":
        _write(changed, "SOUTH")
    elif change == "extra file":
        _write(pipeline_dir / "rates" / "region=W" / "part.parquet", "west")
    elif change == "missing file":
        changed.unlink()
    else:
        changed.rename(changed.with_name("other.parquet"))

    assert copies_differ(*copies, project_root=project_root)


def test_glob_copies_compare_the_files_they_match(tmp_path: Path) -> None:
    project_root, pipeline_dir = _layout(tmp_path)
    for folder in (project_root, pipeline_dir):
        _write(folder / "data" / "r1.parquet", "one")
        _write(folder / "data" / "r2.parquet", "two")
    # A file the pattern does not match is not part of either copy.
    _write(pipeline_dir / "data" / "other.parquet", "not matched")
    copies = _copies("data/r*.parquet", project_root, pipeline_dir)
    assert copies is not None and not copies_differ(*copies, project_root=project_root)

    _write(pipeline_dir / "data" / "r2.parquet", "TWO")

    assert copies_differ(*copies, project_root=project_root)


def test_a_project_folder_named_with_a_glob_marker_still_compares_its_files(
    tmp_path: Path,
) -> None:
    """``[2026]`` in the project's folder is part of a name, not a pattern."""
    project_root = tmp_path / "Pricing [2026]"
    pipeline_dir = project_root / "rating"
    _write(project_root / "data.json", "project")
    _write(pipeline_dir / "data.json", "pipeline")

    copies = _copies("data.json", project_root, pipeline_dir)

    assert copies is not None and copies_differ(*copies, project_root=project_root)


def test_a_file_and_a_folder_differ(tmp_path: Path) -> None:
    project_root, pipeline_dir = _layout(tmp_path)
    _write(project_root / "rates", "a file")
    _write(pipeline_dir / "rates" / "part.parquet", "a folder")

    copies = _copies("rates", project_root, pipeline_dir)

    assert copies is not None and copies_differ(*copies, project_root=project_root)


def test_hard_linked_copies_are_one_file(tmp_path: Path) -> None:
    project_root, pipeline_dir = _layout(tmp_path)
    original = _write(project_root / "data.json", "{}")
    os.link(original, pipeline_dir / "data.json")

    copies = _copies("data.json", project_root, pipeline_dir)

    assert copies is not None and not copies_differ(*copies, project_root=project_root)


def test_a_rewrite_keeping_size_and_time_is_compared_by_its_content(tmp_path: Path) -> None:
    """Nothing is cached: a second comparison in the same process reads the files again."""
    project_root, pipeline_dir = _layout(tmp_path)
    _write(project_root / "data.json", '{"factor": 1}')
    rewritten = _write(pipeline_dir / "data.json", '{"factor": 1}')
    copies = _copies("data.json", project_root, pipeline_dir)
    assert copies is not None and not copies_differ(*copies, project_root=project_root)
    stamp = rewritten.stat()

    _write(rewritten, '{"factor": 9}')
    os.utime(rewritten, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))

    assert rewritten.stat().st_size == stamp.st_size
    assert rewritten.stat().st_mtime_ns == stamp.st_mtime_ns
    assert copies_differ(*copies, project_root=project_root)


@pytest.mark.parametrize(
    "raw",
    ["rates", "rates/*", "rates/**/*.parquet"],
    ids=["folder", "glob", "recursive glob"],
)
def test_a_copy_leading_out_of_the_project_is_refused_before_anything_is_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    """A link inside one copy to a folder outside the project is refused: the outside
    folder is never listed, no file is opened, and no glob expands the pattern."""
    project_root, pipeline_dir = _layout(tmp_path)
    outside = tmp_path / "outside"
    _write(outside / "part.parquet", "outside")
    _link(project_root / "rates" / "linked", outside)
    _write(pipeline_dir / "rates" / "linked" / "part.parquet", "outside")
    copies = _copies(raw, project_root, pipeline_dir)
    assert copies is not None
    listed: list[Path] = []
    opened: list[Path] = []
    list_folder = Path.iterdir

    def recording_iterdir(folder: Path) -> Iterator[Path]:
        listed.append(Path(os.path.realpath(folder)))
        return list_folder(folder)

    def no_glob(*_args: object, **_kwargs: object) -> Iterator[str]:
        raise AssertionError("the comparison expands a glob outside its checked walk")

    monkeypatch.setattr(Path, "iterdir", recording_iterdir)
    monkeypatch.setattr("haute._path_resolution._same_bytes", lambda first, _: opened.append(first))
    monkeypatch.setattr("haute._path_resolution.glob.iglob", no_glob)

    with pytest.raises(RuntimePathOutsideProjectError, match="leads outside the project root"):
        copies_differ(*copies, project_root=project_root)

    assert opened == []
    assert listed
    assert not [folder for folder in listed if folder.is_relative_to(outside.resolve())]


def test_an_alias_and_a_copied_folder_hold_the_same_files(tmp_path: Path) -> None:
    """A link to a folder beside it is not a cycle: its files keep their own paths."""
    project_root, pipeline_dir = _layout(tmp_path)
    _write(project_root / "rates" / "actual" / "part.parquet", "rows")
    _link(project_root / "rates" / "alias", project_root / "rates" / "actual")
    for name in ("actual", "alias"):
        _write(pipeline_dir / "rates" / name / "part.parquet", "rows")
    copies = _copies("rates", project_root, pipeline_dir)
    assert copies is not None and not copies_differ(*copies, project_root=project_root)

    _write(pipeline_dir / "rates" / "alias" / "part.parquet", "other rows")

    assert copies_differ(*copies, project_root=project_root)


@pytest.mark.parametrize("raw", ["rates", "rates/**/*.parquet"], ids=["folder", "recursive glob"])
def test_a_link_back_to_a_folder_above_ends_the_walk(tmp_path: Path, raw: str) -> None:
    project_root, pipeline_dir = _layout(tmp_path)
    for folder in (project_root, pipeline_dir):
        _write(folder / "rates" / "part.parquet", "rows")
    _link(project_root / "rates" / "nested" / "loop", project_root / "rates")
    copies = _copies(raw, project_root, pipeline_dir)

    assert copies is not None and not copies_differ(*copies, project_root=project_root)


def test_an_outside_link_the_pattern_cannot_reach_is_left_alone(tmp_path: Path) -> None:
    """Only what a pattern names is part of its copy, as a glob reads it."""
    project_root, pipeline_dir = _layout(tmp_path)
    _write(tmp_path / "outside" / "part.parquet", "outside")
    for folder in (project_root, pipeline_dir):
        _write(folder / "rates" / "north.parquet", "north")
    _link(project_root / "rates" / "linked", tmp_path / "outside")
    copies = _copies("rates/*.parquet", project_root, pipeline_dir)

    assert copies is not None and not copies_differ(*copies, project_root=project_root)


# ---------------------------------------------------------------------------
# A path carried to another checkout
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("written", "remedy"),
    [
        ("data/synthetic.csv", None),
        ("inputs/rates.csv", None),
        ("data/Synthetic.csv", "Write data/synthetic.csv."),
        ("Data/synthetic.csv", "Write data/synthetic.csv."),
        (r"DATA\SYNTHETIC.csv", "Write data/synthetic.csv."),
        ("inputs/Rates.csv", "Write inputs/rates.csv."),
        ("<the file's absolute path>", "Write it from the project root: data/synthetic.csv."),
        (r"C:\Users\analyst\pricing\data\synthetic.csv", "from the project root"),
        ("../../elsewhere/synthetic.csv", "from the project root"),
        ("data/*.csv", None),
        ("*/synthetic.csv", None),
        ("**/rates.csv", None),
        ("data/*.CSV", "matches 'data/synthetic.csv' only where letter case is ignored"),
        ("*/Synthetic.csv", "matches 'data/synthetic.csv' only where letter case is ignored"),
        ("**/Rates.csv", "only where letter case is ignored"),
    ],
)
def test_a_node_path_has_one_verdict_on_every_system(
    tmp_path: Path, written: str, remedy: str | None
) -> None:
    """One project's paths, carried from Windows to Linux and back: CI's platform smoke
    runs this module on Windows and macOS, which read a file in any letter case, and on
    Linux, which does not. Each path is accepted on all three or refused on all three,
    the refusal quoting it and saying what to write."""
    project_root, pipeline_dir = _layout(tmp_path)
    data = _write(project_root / "data" / "synthetic.csv", "a\n1\n")
    _write(pipeline_dir / "inputs" / "rates.csv", "a\n1\n")
    if written == "<the file's absolute path>":
        written = str(data)

    refusal = portable_path_refusal(written, pipeline_dir=pipeline_dir, project_root=project_root)

    if remedy is None:
        assert refusal is None
    else:
        assert refusal is not None
        assert f"Path {written!r}" in refusal
        assert remedy in refusal


def test_a_folder_in_another_letter_case_names_nothing_the_rule_would_read(
    tmp_path: Path,
) -> None:
    """An empty ``Data`` folder at the root is no case-variant of the pipeline folder's
    ``data/quotes.csv``, which every system reads: the path is accepted."""
    project_root, pipeline_dir = _layout(tmp_path)
    (project_root / "Data").mkdir()
    _write(pipeline_dir / "data" / "quotes.csv", "a\n1\n")

    assert (
        portable_path_refusal(
            "data/quotes.csv", pipeline_dir=pipeline_dir, project_root=project_root
        )
        is None
    )


def _folds_letter_case(folder: Path) -> bool:
    probe = _write(folder / "case-probe", "")
    try:
        return (folder / "CASE-PROBE").exists()
    finally:
        probe.unlink()


def test_twins_in_letter_case_are_refused_where_they_can_be_held(tmp_path: Path) -> None:
    """On a filesystem that tells letter case apart, ``Quotes.csv`` beside ``quotes.csv``
    is one file on Windows and macOS: a path naming either is refused."""
    project_root, pipeline_dir = _layout(tmp_path)
    if _folds_letter_case(project_root):
        pytest.skip("this filesystem holds one file for both spellings")
    _write(project_root / "data" / "quotes.csv", "a\n1\n")
    _write(project_root / "data" / "Quotes.csv", "a\n2\n")

    refusal = portable_path_refusal(
        "data/quotes.csv", pipeline_dir=pipeline_dir, project_root=project_root
    )

    assert refusal is not None
    assert "also matches 'data/Quotes.csv' where letter case is ignored" in refusal
