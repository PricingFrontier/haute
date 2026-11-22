"""``haute run`` command.

Split into:

* :class:`RunConfig` — the typed bag of CLI inputs.
* :func:`run_frames` — the work: the frames the command collects.
* :func:`handle_run` — the work, its frames printed.
* :func:`run` — the thin ``@click.command`` entry point.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn

import click

from haute._project import resolve_pipeline_file
from haute._project_context import current_project
from haute.cli._helpers import EXPECTED_FAILURES, HauteCommand, pipeline_file_project
from haute.errors import ConfigError

if TYPE_CHECKING:
    import polars as pl


@dataclass
class RunConfig:
    """Parsed inputs for the ``haute run`` command."""

    pipeline_file: Path
    #: The node to run to, by the name the command line takes
    #: (``haute._node_addresses.resolve_spelled``); ``None`` runs to the pipeline's output node.
    node: str | None = None
    #: The source the pipeline runs under, as the editor's source selector names it.
    source: str = "live"
    #: The output rows to collect, or ``None`` for all of them.
    rows: int | None = None


def _frames(
    name: str, output: Any, port: str | None = None
) -> list[tuple[str, pl.LazyFrame | pl.DataFrame]]:
    """A node's frames by display name: one, each frame of a multi-frame result, or the one
    *port* reads of it (a submodel occurrence's output), as the preview reads its target's:
    a bundle of one frame by that frame, one of several by its port's label."""
    if isinstance(output, dict):
        if port is not None:
            return [(name, next(iter(output.values())) if len(output) == 1 else output[port])]
        return [(f"{name}[{label}]", frame) for label, frame in output.items()]
    return [(name, output)]


def _fail(message: str) -> NoReturn:
    if message:
        click.echo(message, err=True)
    raise SystemExit(1)


def handle_run(config: RunConfig) -> None:
    """Run the command in the project holding its pipeline file, and print its output."""
    for label, df in run_frames(config):
        shown = "" if config.rows is None else f", first {config.rows:,}"
        click.echo(f"\nOutput - {label} ({df.height:,} rows{shown}):")
        click.echo(df)


def run_frames(config: RunConfig) -> list[tuple[str, pl.DataFrame]]:
    """What the command collects, by display name, run in the project holding its file.

    The command prints these frames; a failure is said and exits as the command exits.
    """
    from haute._project_context import project_scope

    with project_scope(pipeline_file_project(Path(config.pipeline_file))):
        return _run_in_project(config)


def _run_in_project(config: RunConfig) -> list[tuple[str, pl.DataFrame]]:
    """Run the pipeline in *config.pipeline_file* to its output node, or *config.node*, lazily.

    The output node's ancestors are walked once as lazy plans
    (:func:`haute.execution.execute_lazy_graph`) under the ``LAZY_SINK`` profile and
    *config.source*; each node's schema is read from its plan, and only the output is
    collected, all of it or its first *config.rows* rows. Nodes are named as the command line
    takes them, a submodel occurrence's node as ``<occurrence>/<node>``
    (``spelled_name``), and *config.node* is resolved through the expansion's record
    (``resolve_spelled``). A failure exits with ``SystemExit(1)``: a node's names the node
    and, when the executor located it, the line of its code.
    """
    import polars as pl

    from haute._execute_lazy import _extract_error_line
    from haute._execution_admission import create_admitted_execution_context
    from haute._execution_context import ExecutionProfile
    from haute._memory_errors import memory_error_in
    from haute._node_addresses import SpelledTarget, resolve_spelled, spelled_name
    from haute._polars_utils import streaming_collect
    from haute._project_context import current_project, graph_pipeline_dir
    from haute._workbench_nodes import form_owner, with_form_tables
    from haute.deploy._pruner import find_output_node
    from haute.errors import NotFoundError, failing_node, mark_failing_node
    from haute.execution import execute_lazy_graph
    from haute.executor import _build_node_fn, _compile_preamble
    from haute.parser import parse_pipeline_file

    filepath = config.pipeline_file
    click.echo(f"Running pipeline: {filepath}")

    try:
        graph = parse_pipeline_file(filepath, flatten=True)
    except EXPECTED_FAILURES as e:
        _fail(f"Error parsing pipeline: {e}")

    # A workbench node's tables are the form folder's, of the project the file is in.
    if form_owner(graph) is not None:
        try:
            graph = with_form_tables(graph, project_root=current_project().root)
        except EXPECTED_FAILURES as e:
            _fail(f"Error reading the workbench's form: {e}")

    if not graph.nodes:
        _fail("Error: No pipeline nodes found in file.")
    if config.node is not None:
        try:
            target = resolve_spelled(graph, config.node)
        except NotFoundError as e:
            _fail(f"Error: {e}")
    else:
        try:
            output_id = find_output_node(graph)
        except ValueError as e:
            _fail(f"Error: {e} Or name the node to run to with --node.")
        target = SpelledTarget(output_id, None, spelled_name(graph, output_id))
    output_id = target.key

    name = graph.pipeline_name or filepath.stem
    click.echo(f"Pipeline: {name} ({len(graph.nodes)} nodes), source {config.source!r}")

    try:
        context = create_admitted_execution_context(
            operation="cli_run", profile=ExecutionProfile.LAZY_SINK
        )
    except EXPECTED_FAILURES as e:
        _fail(f"Error: {e}")
    # A pipeline's data failing is a Polars error; it is expected as Haute's own are.
    expected: tuple[type[Exception], ...] = (*EXPECTED_FAILURES, pl.exceptions.PolarsError)
    try:
        try:
            preamble_ns = _compile_preamble(
                graph.preamble or "", pipeline_dir=graph_pipeline_dir(graph)
            )
            outputs, order, _parents, _names = execute_lazy_graph(
                graph,
                _build_node_fn,
                target_node_id=output_id,
                preamble_ns=preamble_ns or None,
                source=config.source,
                execution_context=context,
            )
            # Occurrence entries carry warnings only, no frames of their own.
            walked = [nid for nid in order if nid not in graph._submodel_shared_bindings]
            for nid in walked:
                for label, frame in _frames(spelled_name(graph, nid), outputs[nid]):
                    try:
                        width = len(frame.collect_schema())
                    except Exception as e:
                        mark_failing_node(e, nid)
                        raise
                    # Projection pushdown leaves an ancestor only the columns the target needs.
                    carried = "" if nid == output_id else " carried"
                    click.echo(f"  \u2713 {label}: {width} cols{carried}")
            collected: list[tuple[str, pl.DataFrame]] = []
            for label, frame in _frames(target.shown, outputs[output_id], target.port_label):
                if config.rows is not None:
                    frame = frame.head(config.rows)
                if isinstance(frame, pl.LazyFrame):
                    try:
                        frame = streaming_collect(frame, execution_context=context)
                    except expected as e:
                        # The plan fails as a whole: no node is to blame, only the frame.
                        _fail(f"Error collecting the output of {label}: {e}")
                collected.append((label, frame))
        except Exception as e:
            # Out of memory, however it arrives, is said as every edge says it, with the
            # limit and the setting that raises it (haute.cli._helpers.answer_command_failure).
            if memory_error_in(e) is not None:
                raise
            # The walk names the node a failure came from, whatever the failure: a node's
            # own failure is the analyst's, said with its node and line. An expected one
            # the walk did not place ends with its own words; anything else is answered by
            # the command line's edge.
            failed = failing_node(e)
            if failed is not None:
                line = _extract_error_line(e)
                named = spelled_name(graph, failed)
                click.echo(
                    f"  \u2717 {named}{'' if line is None else f' (line {line})'}: {e}",
                    err=True,
                )
                _fail("")
            if isinstance(e, expected):
                _fail(f"Error executing pipeline: {e}")
            raise
    finally:
        context.release_admission(preserve_primary_error=True)
    return collected


@click.command(cls=HauteCommand, need="pipeline_file")
@click.argument("pipeline_file", required=False)
@click.option(
    "--node",
    default=None,
    help=(
        "Run to this node instead of the pipeline's output node: a node inside a submodel "
        "by its name or as SUBMODEL/NODE, a submodel by its name for its first output."
    ),
)
@click.option(
    "--source",
    default="live",
    show_default=True,
    help="The source to run the pipeline under, as the editor's source selector names it.",
)
@click.option(
    "--rows",
    type=click.IntRange(min=1),
    default=None,
    help="Collect only the output's first ROWS rows.",
)
def run(pipeline_file: str | None, node: str | None, source: str, rows: int | None) -> None:
    """Run a pipeline to its output node, or --node, and print that node's output.

    The pipeline is walked lazily, as a batch run walks it, so only the
    output is ever collected.
    """
    try:
        # No file named: the pipeline the shell's working directory holds.
        resolved = resolve_pipeline_file(
            Path(pipeline_file) if pipeline_file else current_project().root
        )
    except (FileNotFoundError, ConfigError) as exc:
        click.echo(f"Error: {exc}", err=True)
        raise SystemExit(1) from exc
    config = RunConfig(pipeline_file=resolved, node=node, source=source, rows=rows)
    handle_run(config)
