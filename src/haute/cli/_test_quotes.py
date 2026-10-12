"""``haute test-quotes`` commands: the workbench's test quotes outside the editor.

Split into:

* :class:`ReportConfig` — the typed bag of CLI inputs.
* :func:`handle_report` — the function that does the work.
* :func:`test_quotes` and its ``report`` — the thin ``@click`` entry points.

``report`` compares the project's test-quote file with the one at a base revision and writes,
as Markdown, the test quotes a branch added, removed and accepted anew, each with the outputs
that moved: what a pull request does to the test quotes. Both files are read through copies
brought to the store's newest schema, so the project's own file is never written, and the
report never fails the run.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import click


@dataclass
class ReportConfig:
    """Parsed inputs for the ``haute test-quotes report`` command."""

    base: str
    output: Path | None


@dataclass(frozen=True, slots=True)
class _Saved:
    """A test quote as a side of the report holds it: its newest revision."""

    name: str
    number: int
    response: dict[str, Any] | None


def _read_quotes(path: Path, scratch: Path) -> dict[str, _Saved]:
    """The test quotes in the file at *path*, read through a migrated copy in *scratch*."""
    from haute._quote_store import migrate_store, open_store, store_url

    copy = scratch / f"{len(list(scratch.iterdir()))}.sqlite"
    shutil.copyfile(path, copy)
    migrate_store(store_url(copy))
    with open_store(store_url(copy)) as store:
        return {
            summary.id: _Saved(summary.name, summary.latest, store.revision(summary.id).response)
            for summary in store.quotes()
        }


def _base_file(project: Path, base: str, relative: str, scratch: Path) -> Path | str | None:
    """The test-quote file at *base* as a file in *scratch*; None when *base* has none; the
    reason, as text, when *base* cannot be read."""
    resolved = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"{base}^{{commit}}"],
        cwd=project,
        capture_output=True,
        text=True,
    )
    if resolved.returncode != 0:
        return f"{base} could not be read as a commit"
    shown = subprocess.run(["git", "show", f"{base}:{relative}"], cwd=project, capture_output=True)
    if shown.returncode != 0:
        return None
    path = scratch / "base-file.sqlite"
    path.write_bytes(shown.stdout)
    return path


def _shown(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value)


def report_markdown(
    base: dict[str, _Saved], head: dict[str, _Saved], note: str | None = None
) -> str:
    """What *head*'s test quotes are against *base*'s, as Markdown, quotes by name."""
    from haute._test_quotes import ACCEPTED_TOLERANCE
    from haute.deploy._validators import response_changes

    added = sorted((quote for key, quote in head.items() if key not in base), key=lambda q: q.name)
    removed = sorted(
        (quote for key, quote in base.items() if key not in head), key=lambda q: q.name
    )
    anew = sorted(
        (
            (base[key], quote)
            for key, quote in head.items()
            if key in base and base[key].number != quote.number
        ),
        key=lambda pair: pair[1].name,
    )
    unchanged = len(head) - len(added) - len(anew)
    lines = ["## Test quotes", ""]
    if note is not None:
        lines += [f"_{note}._", ""]
    lines += [
        f"**{len(anew)} accepted anew, {len(added)} added, {len(removed)} removed, "
        f"{unchanged} unchanged.**",
    ]
    if anew:
        lines += ["", "### Accepted anew"]
        for before, after in anew:
            lines.append(f"- **{after.name}** (revision {before.number} -> {after.number})")
            changes = response_changes(
                after.response or {}, before.response or {}, ACCEPTED_TOLERANCE
            )
            if not changes:
                lines.append("  - no output moved")
            for change in changes:
                if change.kind == "added":
                    lines.append(f"  - `{change.path}` added: {_shown(change.priced)}")
                elif change.kind == "removed":
                    lines.append(f"  - `{change.path}` removed (was {_shown(change.accepted)})")
                else:
                    lines.append(
                        f"  - `{change.path}`: {_shown(change.accepted)} -> {_shown(change.priced)}"
                    )
    if added:
        lines += ["", "### Added", *(f"- **{quote.name}**" for quote in added)]
    if removed:
        lines += ["", "### Removed", *(f"- **{quote.name}**" for quote in removed)]
    return "\n".join(lines) + "\n"


def handle_report(config: ReportConfig) -> str:
    """Write what the branch does to the test quotes against ``config.base``, and return it."""
    from haute._test_quotes import project_test_quotes_path

    project = Path.cwd()
    path = project_test_quotes_path(project)
    relative = path.relative_to(project.resolve()).as_posix()
    with tempfile.TemporaryDirectory() as directory:
        scratch = Path(directory)
        head = _read_quotes(path, scratch) if path.is_file() else {}
        base_file = _base_file(project, config.base, relative, scratch)
        note = base_file if isinstance(base_file, str) else None
        base = _read_quotes(base_file, scratch) if isinstance(base_file, Path) else {}
    markdown = report_markdown(base, head, note)
    if config.output is None:
        click.echo(markdown, nl=False)
    else:
        config.output.write_text(markdown, encoding="utf-8")
    return markdown


@click.group(name="test-quotes")
def test_quotes() -> None:
    """The workbench's test quotes, kept in Preview."""


@test_quotes.command()
@click.option(
    "--base",
    default="origin/main",
    show_default=True,
    help="The revision to compare the test quotes with, such as the pull request's target.",
)
@click.option(
    "--output",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Write the Markdown report to this file instead of the terminal.",
)
def report(base: str, output: Path | None) -> None:
    """Report the test quotes this branch added, removed and accepted anew against BASE."""
    handle_report(ReportConfig(base=base, output=output))
