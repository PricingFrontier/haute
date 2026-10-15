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

``dump`` and ``merge`` are git's drivers for the file, which is a binary to git: ``dump`` shows
it as text in a diff, and ``merge`` merges two sides quote by quote against their base.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click

if TYPE_CHECKING:
    from haute._quote_store import QuoteHistory


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


def _migrated_copy(path: Path, scratch: Path, name: str | None = None) -> str:
    """The store URL of a copy of the test-quote file at *path* in *scratch*, migrated to
    the store's newest schema: a file is read, or merged, without changing it. The copy is
    named *name*, or numbered in turn."""
    from haute._quote_store import migrate_store, store_url

    copy = scratch / (name or f"{len(list(scratch.iterdir()))}.sqlite")
    shutil.copyfile(path, copy)
    url = store_url(copy)
    migrate_store(url)
    return url


def _read_quotes(path: Path, scratch: Path) -> dict[str, _Saved]:
    """The test quotes in the file at *path*, read through a migrated copy in *scratch*."""
    from haute._quote_store import open_store

    with open_store(_migrated_copy(path, scratch)) as store:
        return {
            summary.id: _Saved(summary.name, summary.latest, store.revision(summary.id).response)
            for summary in store.quotes()
        }


def _base_file(project: Path, base: str, relative: str, scratch: Path) -> Path | str | None:
    """The test-quote file where this branch left *base*, their merge base, as a file in
    *scratch*: what *base* gained since is not this branch's. None when the merge base has
    no file; the reason, as text, when *base* or the merge base cannot be read."""
    from haute._git_core import _run_git_ok, _run_git_process

    resolved, _ = _run_git_ok("rev-parse", "--verify", "--quiet", f"{base}^{{commit}}", cwd=project)
    if not resolved:
        return f"{base} could not be read as a commit"
    found, merge_base = _run_git_ok("merge-base", "HEAD", base, cwd=project)
    if not found:
        return (
            f"{base} shares no history with this branch here: fetch the full history "
            "to compare with it"
        )
    shown = _run_git_process("show", f"{merge_base}:{relative}", binary=True, cwd=project)
    if shown.returncode != 0:
        return None
    path = scratch / "base-file.sqlite"
    path.write_bytes(shown.stdout)
    return path


def _histories(path: Path, scratch: Path) -> dict[str, QuoteHistory]:
    """The test quotes whole in the file at *path*, read through a migrated copy in *scratch*.

    An empty file, as git gives a merge with no base, is a file with no test quotes.
    """
    from haute._quote_store import open_store

    with open_store(_migrated_copy(path, scratch)) as store:
        return store.histories()


def dump_text(path: Path) -> str:
    """The test-quote file at *path* as text: each quote by name, its newest revision, who
    saved it and when, its request and its response, the JSON with sorted keys."""
    lines: list[str] = []
    with tempfile.TemporaryDirectory() as directory:
        histories = _histories(path, Path(directory))
    for history in sorted(histories.values(), key=lambda h: (h.name, h.quote["id"])):
        newest = history.revisions[-1] if history.revisions else None
        lines.append(f"# {history.name} ({history.quote['id']})")
        if newest is None:
            lines += ["no revisions", ""]
            continue
        lines.append(
            f"revision {newest['number']}, saved by {newest['saved_by'] or 'nobody named'} "
            f"at {newest['saved_at'].isoformat()}"
        )
        for field in ("request", "response"):
            value = None if newest[field] is None else json.loads(newest[field])
            lines.append(f"{field}: {json.dumps(value, sort_keys=True, indent=2)}")
        lines.append("")
    return "\n".join(lines)


def merge_files(base: Path, ours: Path, theirs: Path) -> list[str]:
    """Merge *theirs* into *ours* quote by quote against *base*; the quotes in conflict, by
    name, when any are, *ours* then left as it was.

    A quote one side changed (added, revised or deleted) while the other left it as the base
    had it takes that side's; a quote both changed alike is kept; a quote both changed
    otherwise is a conflict. The result is written over a copy of *ours*, at the store's
    newest schema, and moved into place only when nothing conflicts.
    """
    from haute._quote_store import open_store

    with tempfile.TemporaryDirectory() as directory:
        scratch = Path(directory)
        before = _histories(base, scratch)
        mine = _histories(ours, scratch)
        other = _histories(theirs, scratch)
        conflicts: list[str] = []
        taken: list[QuoteHistory] = []
        dropped: list[str] = []
        for quote_id in sorted(set(before) | set(mine) | set(other)):
            was, kept, came = before.get(quote_id), mine.get(quote_id), other.get(quote_id)
            if kept == came or came == was:
                continue
            if kept != was:
                # The quote is on some side, or it would not be in the union.
                named = kept or came or was
                assert named is not None
                conflicts.append(named.name)
            elif came is None:
                dropped.append(quote_id)
            else:
                taken.append(came)
        if conflicts:
            return sorted(conflicts)
        with open_store(_migrated_copy(ours, scratch, "merged.sqlite")) as store:
            for quote_id in dropped:
                store.delete(quote_id)
            for history in taken:
                store.put_history(history)
        shutil.copyfile(scratch / "merged.sqlite", ours)
    return []


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
    # Accepted anew: a newer revision, or one under the same number holding another
    # response, as two branches each saving revision 2 leave after a merge keeps one.
    anew = sorted(
        (
            (base[key], quote)
            for key, quote in head.items()
            if key in base
            and (
                base[key].number != quote.number
                or response_changes(
                    quote.response or {}, base[key].response or {}, ACCEPTED_TOLERANCE
                )
            )
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
@click.argument("file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
def dump(file: Path) -> None:
    """Write the test-quote FILE as text, for git's diff driver."""
    click.echo(dump_text(file), nl=False)


@test_quotes.command()
@click.argument("base", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.argument("ours", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.argument("theirs", type=click.Path(exists=True, dir_okay=False, path_type=Path))
def merge(base: Path, ours: Path, theirs: Path) -> None:
    """Merge the test-quote files BASE, OURS and THEIRS into OURS, for git's merge driver."""
    conflicts = merge_files(base, ours, theirs)
    if conflicts:
        click.echo(
            "The test quotes conflict: both sides changed "
            + ", ".join(repr(n) for n in conflicts)
            + ". Take one side's test-quote file with git checkout --ours or --theirs, then "
            "run those quotes in Preview and accept the results you keep.",
            err=True,
        )
        raise SystemExit(1)


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
