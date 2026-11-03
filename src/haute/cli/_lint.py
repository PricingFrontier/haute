"""``haute lint`` command.

Split into three pieces:

* :class:`LintConfig` — the typed bag of values the command needs.
* :func:`handle_lint` — the pure function that does the work, usable
  without Click (tests, programmatic callers).
* :func:`lint` — the thin ``@click.command`` entry point that parses
  args into a :class:`LintConfig` and dispatches to :func:`handle_lint`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import click

from haute._project import resolve_pipeline_file
from haute._project_context import current_project
from haute.cli._helpers import EXPECTED_FAILURES
from haute.errors import ConfigError

if TYPE_CHECKING:
    from haute._types import PipelineGraph


@dataclass
class LintConfig:
    """Parsed inputs for the ``haute lint`` command."""

    pipeline_file: Path


def _lint_root(filepath: Path) -> Path:
    """The root the editor saves *filepath* under: the project holding it, which the
    command runs in."""
    del filepath
    return current_project().root


def _document_problems(filepath: Path, root: Path) -> tuple[list[str], list[str]]:
    """The editor document's diagnostics for *filepath*, ``file:line: message``: its
    errors, which leave the document unready so the editor refuses to save it (a statement
    or comment a save would drop among them), and its warnings."""
    from haute._pipeline_recovery import load_pipeline_editor_document

    document = load_pipeline_editor_document(filepath, project_root=root)
    errors: list[str] = []
    warnings: list[str] = []
    for diagnostic in document.diagnostics:
        where = diagnostic.source_file or filepath.relative_to(root).as_posix()
        if diagnostic.source_span is not None:
            where = f"{where}:{diagnostic.source_span.start_line}"
        line = f"{where}: {diagnostic.message}"
        if diagnostic.remediation:
            line = f"{line} {diagnostic.remediation}"
        (errors if diagnostic.severity == "error" else warnings).append(line)
    if document.load_status != "ready" and not errors:
        errors.append(f"{filepath.name}: the editor cannot load this pipeline ready to save.")
    return errors, warnings


def _structural_problems(flattened: PipelineGraph) -> list[str]:
    """Edges naming a node the graph lacks, and, in a graph of several nodes, nodes no edge
    touches."""
    node_ids = {node.id for node in flattened.nodes}
    problems: list[str] = []
    for edge in flattened.edges:
        if edge.source not in node_ids:
            problems.append(f"Edge references missing source node: {edge.source}")
        if edge.target not in node_ids:
            problems.append(f"Edge references missing target node: {edge.target}")
    if len(flattened.nodes) > 1:
        connected = {end for edge in flattened.edges for end in (edge.source, edge.target)}
        problems.extend(
            f"Node '{orphan}' is disconnected (no edges)" for orphan in sorted(node_ids - connected)
        )
    return problems


def handle_lint(config: LintConfig) -> None:
    """Run the command in the project holding its pipeline file."""
    from haute._project_context import project_holding, project_scope

    with project_scope(project_holding(Path(config.pipeline_file).resolve().parent)):
        _handle_lint_in_project(config)


def _handle_lint_in_project(config: LintConfig) -> None:
    """Check the pipeline as the editor's save does, without executing it.

    The file is parsed strictly, its editor document's diagnostics are read (with file
    and line), the editor's save-time validation runs over the parsed graph
    (``SavePipelineService.validate_graph``: node configs, names, global constants, joins
    and the rest, each refused with the editor's message), and the flattened graph is
    checked for dangling edges and disconnected nodes. Every problem found is reported,
    and any exits with ``SystemExit(1)``.
    """
    from haute.graph_utils import flatten_graph
    from haute.routes._helpers import parse_pipeline_to_graph
    from haute.routes._save_pipeline import SavePipelineService

    filepath = config.pipeline_file.resolve()
    root = _lint_root(filepath)
    click.echo(f"Linting pipeline: {config.pipeline_file}")

    try:
        graph = parse_pipeline_to_graph(filepath, project_root=root)
    except EXPECTED_FAILURES as e:
        click.echo(f"  ✗ Parse error: {e}", err=True)
        raise SystemExit(1)

    if not graph.nodes:
        click.echo("  ✗ No nodes found in pipeline.", err=True)
        raise SystemExit(1)

    errors, warnings = _document_problems(filepath, root)
    service = SavePipelineService(root, pipeline_root=filepath.parent)
    try:
        warnings.extend(
            service.validate_graph(graph, source_file=filepath.relative_to(root).as_posix())
        )
    except EXPECTED_FAILURES as e:
        errors.append(str(e))
    flattened = flatten_graph(graph)
    errors.extend(_structural_problems(flattened))

    for warning in warnings:
        click.echo(f"  ! {warning}")
    if errors:
        click.echo(f"\n  Found {len(errors)} issue(s):", err=True)
        for err in errors:
            click.echo(f"  ✗ {err}", err=True)
        raise SystemExit(1)

    name = graph.pipeline_name or filepath.stem
    click.echo(f"  ✓ Pipeline '{name}': {len(flattened.nodes)} nodes, {len(flattened.edges)} edges")
    click.echo("  ✓ No issues found.")


@click.command()
@click.argument("pipeline_file", required=False)
def lint(pipeline_file: str | None) -> None:
    """Check a pipeline as the editor's save does, without running it.

    Parses the pipeline, applies the editor's save-time validation and
    checks for structural issues (missing edge ends, disconnected nodes),
    reporting every problem found.
    """
    try:
        # No file named: the pipeline the shell's working directory holds.
        resolved = resolve_pipeline_file(
            Path(pipeline_file) if pipeline_file else current_project().root
        )
    except (FileNotFoundError, ConfigError) as exc:
        click.echo(f"Error: {exc}", err=True)
        raise SystemExit(1) from exc
    config = LintConfig(pipeline_file=resolved)
    handle_lint(config)
