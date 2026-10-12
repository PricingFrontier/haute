"""``haute test-quotes report`` (specs/cli): what a branch does to the workbench's test quotes
against its base, as Markdown, read through migrated copies so the project's file is left
alone."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from haute._quote_store import QuoteStore, migrate_store, open_store, store_url
from haute._workbench_form import FormSpec
from haute.cli import cli
from haute.cli._test_quotes import ReportConfig, handle_report

FILE = "tests/quotes/test_quotes.sqlite"
FORM = FormSpec.model_validate(
    {
        "name": "motor",
        "schema": {"tables": []},
        "pages": [
            {
                "id": "p",
                "title": "Sheet 1",
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
