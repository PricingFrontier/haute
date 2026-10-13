"""``haute test-quotes report`` (specs/cli): what a branch does to the workbench's test quotes
against its base, as Markdown, read through migrated copies so the project's file is left
alone."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from haute._quote_store import QuoteStore, migrate_store, open_store, store_url
from haute._test_quotes import ensure_test_quote_git
from haute._workbench_form import FormSpec
from haute.cli import cli
from haute.cli._test_quotes import (
    ReportConfig,
    _Saved,
    dump_text,
    handle_report,
    merge_files,
    report_markdown,
)

FILE = "tests/quotes/test_quotes.sqlite"
FORM = FormSpec.model_validate(
    {
        "name": "motor",
        "schema": {"tables": []},
        "pages": [
            {
                "id": "p",
                "title": "Sheet 1",
                "width": 1280,
                "sections": [{"id": "s", "title": "s", "height": 120, "widgets": []}],
            }
        ],
    }
)


def git(project: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=project, check=True, capture_output=True)


def quotes(project: Path) -> QuoteStore:
    url = store_url(project / FILE)
    migrate_store(url)
    return open_store(url)


def create(store: QuoteStore, name: str, response: dict[str, Any]) -> str:
    return store.create(
        name=name,
        owner="ana",
        saved_by="ana",
        form=FORM,
        inputs={},
        request={},
        response=response,
        release=None,
    ).quote_id


@pytest.fixture()
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A repository whose main holds two test quotes; the working tree is a branch off it."""
    monkeypatch.chdir(tmp_path)
    git(tmp_path, "init", "-b", "main")
    git(tmp_path, "config", "user.name", "Test Actuary")
    git(tmp_path, "config", "user.email", "test@example.com")
    (tmp_path / "tests/quotes").mkdir(parents=True)
    with quotes(tmp_path) as store:
        create(store, "Northern fleet", {"pricing": {"premium": 512.4}})
        create(store, "Southern fleet", {"pricing": {"premium": 300.0}})
    git(tmp_path, "add", FILE)
    git(tmp_path, "commit", "-m", "two test quotes")
    git(tmp_path, "checkout", "-b", "rates")
    return tmp_path


def test_the_report_names_what_the_branch_added_removed_and_accepted_anew(
    project: Path,
) -> None:
    with quotes(project) as store:
        by_name = {quote.name: quote.id for quote in store.quotes()}
        store.save_revision(
            by_name["Northern fleet"],
            after=1,
            saved_by="ana",
            form=FORM,
            inputs={},
            request={},
            response={"pricing": {"premium": 530.0, "tax": 3.0}},
            release=None,
        )
        store.delete(by_name["Southern fleet"])
        create(store, "Coastal", {"pricing": {"premium": 1.0}})
    before = (project / FILE).read_bytes()

    report = handle_report(ReportConfig(base="main", output=None))

    assert report == (
        "## Test quotes\n"
        "\n"
        "**1 accepted anew, 1 added, 1 removed, 0 unchanged.**\n"
        "\n"
        "### Accepted anew\n"
        "- **Northern fleet** (revision 1 -> 2)\n"
        "  - `pricing.premium`: 512.4 -> 530.0\n"
        "  - `pricing.tax` added: 3.0\n"
        "\n"
        "### Added\n"
        "- **Coastal**\n"
        "\n"
        "### Removed\n"
        "- **Southern fleet**\n"
    )
    assert (project / FILE).read_bytes() == before


def test_a_branch_that_changed_nothing_says_so(project: Path) -> None:
    assert handle_report(ReportConfig(base="main", output=None)).endswith(
        "**0 accepted anew, 0 added, 0 removed, 2 unchanged.**\n"
    )


def test_a_revision_under_the_same_number_with_another_response_is_accepted_anew() -> None:
    # Two branches each saved revision 2, and the merge kept the head's.
    base = {"q": _Saved("Northern fleet", 2, {"pricing": {"premium": 100.0}})}
    head = {"q": _Saved("Northern fleet", 2, {"pricing": {"premium": 900.0}})}

    report = report_markdown(base, head)

    assert "**1 accepted anew, 0 added, 0 removed, 0 unchanged.**" in report
    assert "`pricing.premium`: 100.0 -> 900.0" in report
    assert report_markdown(base, base).count("1 unchanged") == 1


def test_a_base_without_the_file_reads_as_no_test_quotes(project: Path) -> None:
    # A commit of the empty tree: a base that never had the file.
    tree = (
        subprocess.run(["git", "mktree"], cwd=project, input=b"", capture_output=True, check=True)
        .stdout.decode()
        .strip()
    )
    commit = (
        subprocess.run(
            ["git", "commit-tree", tree, "-m", "empty"],
            cwd=project,
            capture_output=True,
            check=True,
        )
        .stdout.decode()
        .strip()
    )
    git(project, "branch", "empty", commit)

    report = handle_report(ReportConfig(base="empty", output=None))

    assert "**0 accepted anew, 2 added, 0 removed, 0 unchanged.**" in report


def test_a_base_that_cannot_be_read_is_said_and_the_report_still_written(
    project: Path,
) -> None:
    written = project / "report.md"

    result = CliRunner().invoke(
        cli, ["test-quotes", "report", "--base", "no-such-ref", "--output", str(written)]
    )

    assert result.exit_code == 0, result.output
    report = written.read_text(encoding="utf-8")
    assert "_no-such-ref could not be read as a commit._" in report
    assert "2 added" in report


# ── git's drivers: the dump and the merge ─────────────────────────────────


def _file(directory: Path, name: str) -> Path:
    path = directory / name
    migrate_store(store_url(path))
    return path


def _names(path: Path) -> list[str]:
    with open_store(store_url(path)) as store:
        return sorted(quote.name for quote in store.quotes())


def test_the_dump_shows_each_quote_by_name_with_its_newest_revision(tmp_path: Path) -> None:
    path = _file(tmp_path, "q.sqlite")
    with open_store(store_url(path)) as store:
        create(store, "Northern fleet", {"pricing": {"premium": 512.4, "band": "A"}})

    text = dump_text(path)

    lines = text.splitlines()
    assert lines[0].startswith("# Northern fleet (")
    assert lines[1].startswith("revision 1, saved by ana at ")
    assert "request: {}" in lines
    # Keys sorted, so a diff of two dumps shows what changed and nothing else.
    assert text.index('"band": "A"') < text.index('"premium": 512.4')


def test_a_merge_takes_each_quote_from_the_side_that_changed_it(tmp_path: Path) -> None:
    base = _file(tmp_path, "base.sqlite")
    with open_store(store_url(base)) as store:
        kept = create(store, "Kept", {"p": 1})
        revised = create(store, "Revised", {"p": 1})
        create(store, "Deleted", {"p": 1})
    ours = tmp_path / "ours.sqlite"
    theirs = tmp_path / "theirs.sqlite"
    shutil.copyfile(base, ours)
    shutil.copyfile(base, theirs)
    with open_store(store_url(ours)) as store:
        create(store, "Ours", {"p": 2})
    with open_store(store_url(theirs)) as store:
        create(store, "Theirs", {"p": 3})
        store.save_revision(
            revised,
            after=1,
            saved_by="ben",
            form=FORM,
            inputs={},
            request={},
            response={"p": 9},
            release=None,
        )
        by_name = {quote.name: quote.id for quote in store.quotes()}
        store.delete(by_name["Deleted"])

    assert merge_files(base, ours, theirs) == []

    assert _names(ours) == ["Kept", "Ours", "Revised", "Theirs"]
    with open_store(store_url(ours)) as store:
        assert store.revision(revised).response == {"p": 9}
        assert store.revision(kept).number == 1


def test_a_quote_both_sides_changed_is_a_conflict_and_ours_is_left_alone(
    tmp_path: Path,
) -> None:
    base = _file(tmp_path, "base.sqlite")
    with open_store(store_url(base)) as store:
        both = create(store, "Northern fleet", {"p": 1})
    ours = tmp_path / "ours.sqlite"
    theirs = tmp_path / "theirs.sqlite"
    shutil.copyfile(base, ours)
    shutil.copyfile(base, theirs)
    for path, premium in ((ours, 2), (theirs, 3)):
        with open_store(store_url(path)) as store:
            store.save_revision(
                both,
                after=1,
                saved_by="ana",
                form=FORM,
                inputs={},
                request={},
                response={"p": premium},
                release=None,
            )
    before = ours.read_bytes()

    result = CliRunner().invoke(cli, ["test-quotes", "merge", str(base), str(ours), str(theirs)])

    assert result.exit_code == 1
    assert "both sides changed 'Northern fleet'" in result.output
    assert ours.read_bytes() == before


def test_a_merge_with_no_common_base_takes_both_sides_quotes(tmp_path: Path) -> None:
    base = tmp_path / "base.sqlite"
    base.write_bytes(b"")
    ours = _file(tmp_path, "ours.sqlite")
    theirs = _file(tmp_path, "theirs.sqlite")
    with open_store(store_url(ours)) as store:
        create(store, "Ours", {"p": 1})
    with open_store(store_url(theirs)) as store:
        create(store, "Theirs", {"p": 1})

    assert merge_files(base, ours, theirs) == []
    assert _names(ours) == ["Ours", "Theirs"]


def test_git_merges_two_branches_quotes_through_the_registered_driver(project: Path) -> None:
    assert ensure_test_quote_git(project, FILE) is True
    assert ensure_test_quote_git(project, FILE) is False
    git(project, "add", ".gitattributes")
    git(project, "commit", "-m", "the test-quote drivers")
    git(project, "branch", "coast")
    with quotes(project) as store:
        create(store, "Rates branch's quote", {"p": 1})
    git(project, "commit", "-am", "a quote on rates")
    git(project, "checkout", "coast")
    with quotes(project) as store:
        create(store, "Coast branch's quote", {"p": 2})
    git(project, "commit", "-am", "a quote on coast")
    git(project, "checkout", "rates")

    git(project, "merge", "--no-edit", "coast")

    assert _names(project / FILE) == [
        "Coast branch's quote",
        "Northern fleet",
        "Rates branch's quote",
        "Southern fleet",
    ]
    shown = subprocess.run(
        ["git", "diff", "HEAD^1", "HEAD", "--", FILE],
        cwd=project,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    # The diff driver shows the file as text: the quote the merge brought in, by name.
    assert "+# Coast branch's quote (" in shown
