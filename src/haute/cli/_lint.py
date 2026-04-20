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

import click

from haute._project import resolve_pipeline_file


@dataclass
class LintConfig:
    """Parsed inputs for the ``haute lint`` command."""

    pipeline_file: Path


def handle_lint(config: LintConfig) -> None:
    """Parse the pipeline and report structural issues.

    Exits with ``SystemExit(1)`` on parse errors, disconnected nodes, or
    any other structural problem — the whole point of the command is to
    surface those issues loudly.
    """
    from haute.parser import parse_pipeline_file

    filepath = config.pipeline_file

    click.echo(f"Linting pipeline: {filepath}")

    try:
        graph = parse_pipeline_file(filepath)
    except Exception as e:
        click.echo(f"  \u2717 Parse error: {e}", err=True)
        raise SystemExit(1)

    nodes = graph.nodes
    edges = graph.edges
    node_ids = {n.id for n in nodes}

    if not nodes:
        click.echo("  \u2717 No nodes found in pipeline.", err=True)
        raise SystemExit(1)

    errors: list[str] = []

    # Check for edges referencing non-existent nodes
    for edge in edges:
        if edge.source not in node_ids:
            errors.append(f"Edge references missing source node: {edge.source}")
        if edge.target not in node_ids:
            errors.append(f"Edge references missing target node: {edge.target}")

    # Check for nodes with parse errors
    for node in nodes:
        if node.data.config.get("parseError"):
            errors.append(f"Node '{node.id}' has parse error: {node.data.config['parseError']}")

    # Check for orphan nodes (no edges at all, in a multi-node graph)
    if len(nodes) > 1:
        connected = set()
        for edge in edges:
            connected.add(edge.source)
            connected.add(edge.target)
        orphans = node_ids - connected
        for orphan in orphans:
            errors.append(f"Node '{orphan}' is disconnected (no edges)")

    if errors:
        click.echo(f"\n  Found {len(errors)} issue(s):", err=True)
        for err in errors:
            click.echo(f"  \u2717 {err}", err=True)
        raise SystemExit(1)

    name = graph.pipeline_name or filepath.stem
    click.echo(f"  \u2713 Pipeline '{name}': {len(nodes)} nodes, {len(edges)} edges")
    click.echo("  \u2713 No structural issues found.")


@click.command()
@click.argument("pipeline_file", required=False)
def lint(pipeline_file: str | None) -> None:
    """Validate pipeline structure without deploying.

    Parses the pipeline, checks for structural issues (orphan nodes,
    missing edges, syntax errors), and reports any problems found.
    """
    try:
        resolved = resolve_pipeline_file(
            Path(pipeline_file) if pipeline_file else None,
        )
    except FileNotFoundError as exc:
        click.echo(f"Error: {exc}", err=True)
        raise SystemExit(1) from exc
    config = LintConfig(pipeline_file=resolved)
    handle_lint(config)
