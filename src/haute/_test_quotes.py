"""The test quotes an analyst keeps in Preview (specs/workbench): the project's test-quote file
and what a test quote is saved with.

The file is ``test_quotes.sqlite`` in the test-quote directory, ``[test_quotes] dir`` of
``haute.toml`` (``tests/quotes`` when absent), beside the hand-written test-quote files deploy
reads, and the quote store holds it. A test quote keeps its values, the request Price builds
from them and the response its pricing gave, shaped as a deployed pipeline answers.
"""

from __future__ import annotations

import sys
import tomllib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from haute._artifact_paths import PathProblem, ordinary_path_problem
from haute._workbench_config import WorkbenchError
from haute._workbench_form import FormSpec
from haute._workbench_tables import output_tables, port_tables, sample_quote

#: The test-quote file's name, in the test-quote directory.
TEST_QUOTES_FILE = "test_quotes.sqlite"
#: The test-quote directory when ``haute.toml`` names none, as ``haute init`` writes it.
DEFAULT_TEST_QUOTES_DIR = "tests/quotes"
#: A test quote's accepted numbers are matched within one part in a billion, so a pricing
#: that differs only in a float's last bits is no change.
ACCEPTED_TOLERANCE = 1e-9


class WorkbenchTestQuoteError(WorkbenchError):
    """A test quote cannot be saved as asked: the message says why."""

    error_code = "test_quote_invalid"


def project_test_quotes_path(project_root: Path) -> Path:
    """The project's test-quote file: ``test_quotes.sqlite`` in its test-quote directory.

    The path is an ordinary one inside the project, as the Files panels take one: no link or
    junction on the way, and the file no hard link, which SQLite would write through to the
    file it shares.
    """
    directory = DEFAULT_TEST_QUOTES_DIR
    toml_path = project_root / "haute.toml"
    if toml_path.is_file():
        try:
            with toml_path.open("rb") as handle:
                table = tomllib.load(handle).get("test_quotes", {})
        except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
            raise WorkbenchTestQuoteError(f"haute.toml could not be read: {exc}") from exc
        configured = table.get("dir") if isinstance(table, dict) else None
        if isinstance(configured, str) and configured:
            directory = configured
    relative = Path(directory) / TEST_QUOTES_FILE
    path, problem = ordinary_path_problem(project_root, str(relative))
    if problem is None:
        return path
    named = relative.as_posix() if problem == "not_ordinary" else _from_root(project_root, path)
    raise WorkbenchTestQuoteError(
        f"{named} {_PATH_PROBLEMS[problem]}: [test_quotes] dir must be a folder of the "
        f"project's own, and {TEST_QUOTES_FILE} a file inside it."
    )


def project_gitattributes_path(project_root: Path) -> Path:
    """The project's ``.gitattributes``, an ordinary file of the project's own, as the test
    quotes' path is."""
    path, problem = ordinary_path_problem(project_root, ".gitattributes")
    if problem is None:
        return path
    raise WorkbenchTestQuoteError(
        f".gitattributes {_PATH_PROBLEMS[problem]}: make it a file of the project's own."
    )


#: Why a path the test quotes write through is refused, after the path it names.
_PATH_PROBLEMS: dict[PathProblem, str] = {
    "not_ordinary": "isn't a path inside the project, named from its folder",
    "link": "is reached through a link or junction",
    "multiply_linked": "is a hard link to another file, which a write would change too",
    "not_a_file": "isn't an ordinary file or folder",
    "outside": "is outside the project",
}


def _from_root(project_root: Path, path: Path) -> str:
    return path.relative_to(project_root.resolve()).as_posix()


def _attributes_pattern(relative: str) -> str:
    """*relative* as a ``.gitattributes`` pattern: quoted as git reads a quoted pattern when
    it holds a space, a quote or a backslash, which would otherwise end it or escape."""
    if not any(character in relative for character in ' \t"\\'):
        return relative
    escaped = relative.replace("\\", "\\\\").replace('"', '\\"').replace("\t", "\\t")
    return f'"{escaped}"'


def test_quotes_branch(project_root: Path) -> str | None:
    """The branch the project's test quotes are read from and written to now: the branch
    checked out, the commit when none is, None outside a repository."""
    from haute._git_core import _get_current_branch, _run_git_ok

    if not (project_root / ".git").exists():
        return None
    branch = _get_current_branch(project_root)
    if branch != "HEAD":
        return branch
    ok, commit = _run_git_ok("rev-parse", "HEAD", cwd=project_root)
    return commit.strip() if ok else None


#: The git drivers' name for the test-quote file, in ``.gitattributes`` and the clone's config.
GIT_DRIVER = "haute-test-quotes"


def ensure_test_quote_git(project_root: Path, relative: str) -> bool:
    """Give the clone at *project_root* git's drivers for the test-quote file at *relative*.

    ``.gitattributes`` names the file with the drivers, and the clone's own config runs them
    through the Python running Haute: ``dump`` for a diff and ``merge`` for a merge. Returns
    whether ``.gitattributes`` changed, for the caller to capture; nothing outside a repository.
    """
    from haute._git_core import _run_git_ok

    if not (project_root / ".git").exists():
        return False
    python = Path(sys.executable).as_posix()
    wanted = {
        f"diff.{GIT_DRIVER}.textconv": f'"{python}" -m haute.cli._test_quotes dump',
        f"merge.{GIT_DRIVER}.name": "Haute's test quotes, merged quote by quote",
        f"merge.{GIT_DRIVER}.driver": f'"{python}" -m haute.cli._test_quotes merge %O %A %B',
    }
    for key, value in wanted.items():
        ok, current = _run_git_ok("config", "--local", "--get", key, cwd=project_root)
        if not ok or current.strip() != value:
            _run_git_ok("config", "--local", key, value, cwd=project_root)
    attributes = project_gitattributes_path(project_root)
    line = f"{_attributes_pattern(relative)} diff={GIT_DRIVER} merge={GIT_DRIVER}"
    text = attributes.read_text(encoding="utf-8") if attributes.is_file() else ""
    if line in text.splitlines():
        return False
    # A file that does not end its last line would run the two lines together.
    lead = "\n" if text and not text.endswith("\n") else ""
    with attributes.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(f"{lead}{line}\n")
    return True


def quote_request(spec: FormSpec, values: Mapping[str, Any]) -> dict[str, Any]:
    """The request Price builds from *values* typed into *spec*'s sheets: each input table's
    filled rows by name, read as the server reads the sample, the cells the sheets hide left
    out."""
    return sample_quote(spec.model_copy(update={"sample": dict(values)}))


def response_document(
    spec: FormSpec, tables: Mapping[str, Sequence[Mapping[str, Any]]]
) -> dict[str, Any]:
    """The priced *tables*, each output table's rows by name, as a deployed pipeline answers:
    a one-row table as an object of its columns and a many-row table as a list of them.

    Raises :class:`WorkbenchTestQuoteError` naming a table the pricing did not give, or a
    one-row table priced with other than one row.
    """
    document: dict[str, Any] = {}
    for table in port_tables(output_tables(spec)):
        rows = tables.get(table.name)
        if rows is None:
            raise WorkbenchTestQuoteError(
                f"The priced response has no {table.name!r} table: price the quote again."
            )
        if not table.one_row:
            document[table.name] = [dict(row) for row in rows]
            continue
        if len(rows) != 1:
            raise WorkbenchTestQuoteError(
                f"The {table.name!r} table has one row per quote, but the pricing gave it "
                f"{len(rows)} rows."
            )
        document[table.name] = dict(rows[0])
    return document
