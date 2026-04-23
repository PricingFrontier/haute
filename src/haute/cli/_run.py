"""``haute run`` command.

Split into:

* :class:`RunConfig` — the typed bag of CLI inputs.
* :func:`handle_run` — the pure function that does the work.
* :func:`run` — the thin ``@click.command`` entry point.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import click

from haute._project import resolve_pipeline_file


@dataclass
class RunConfig:
    """Parsed inputs for the ``haute run`` command."""

    pipeline_file: Path


def handle_run(config: RunConfig) -> None:
    """Execute the pipeline described in *config.pipeline_file*.

    Parses the pipeline, runs every node through :func:`execute_graph`,
    and prints a per-node status line plus the last node's preview.
    Exits with ``SystemExit(1)`` on any parse or execution error.
    """
    from haute.executor import execute_graph
    from haute.parser import parse_pipeline_file

    filepath = config.pipeline_file
    click.echo(f"Running pipeline: {filepath}")

    try:
        graph = parse_pipeline_file(filepath)
    except Exception as e:
        click.echo(f"Error parsing pipeline: {e}", err=True)
        raise SystemExit(1)

    nodes = graph.nodes
    if not nodes:
        click.echo("Error: No pipeline nodes found in file.", err=True)
        raise SystemExit(1)

    name = graph.pipeline_name or filepath.stem
    click.echo(f"Pipeline: {name} ({len(nodes)} nodes)")

    try:
        results = execute_graph(graph)
    except Exception as e:
        click.echo(f"Error executing pipeline: {e}", err=True)
        raise SystemExit(1)

    # Report per-node results
    errors = 0
    for nid, res in results.items():
        if res.status == "ok":
            click.echo(f"  \u2713 {nid}: {res.row_count:,} rows \u00d7 {res.column_count} cols")
        else:
            errors += 1
            click.echo(f"  \u2717 {nid}: {res.error or 'unknown error'}")

    if errors:
        click.echo(f"\n{errors} node(s) failed.", err=True)
        raise SystemExit(1)

    # Print the last node's preview
    last_nid = list(results.keys())[-1]
    last = results[last_nid]
    if last.preview:
        import polars as pl

        df = pl.DataFrame(last.preview)
        click.echo(f"\nOutput - {last_nid} ({last.row_count:,} rows):")
        click.echo(df)


@click.command()
@click.argument("pipeline_file", required=False)
def run(pipeline_file: str | None) -> None:
    """Execute a pipeline and print the result.

    Uses the same parse -> execute_graph path as the GUI so both
    produce identical results from the same .py file.
    """
    try:
        resolved = resolve_pipeline_file(
            Path(pipeline_file) if pipeline_file else None,
        )
    except FileNotFoundError as exc:
        click.echo(f"Error: {exc}", err=True)
        raise SystemExit(1) from exc
    config = RunConfig(pipeline_file=resolved)
    handle_run(config)
