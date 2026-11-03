"""``haute app`` commands: the underwriter app, built and served from the project.

Split into:

* :class:`AppBuildConfig`, :class:`AppRolloutConfig`, :class:`AppImpactConfig` and
  :class:`AppServeConfig` — the typed bags of CLI inputs.
* :func:`handle_app_build`, :func:`handle_app_rollout`, :func:`handle_app_impact` and
  :func:`handle_app_serve` — the functions that do the work.
* :func:`app` and its ``build``, ``rollout``, ``impact`` and ``serve`` — the thin ``@click``
  entry points.

``build`` writes the project's release into a directory, ``.haute/app/release`` unless
another is named: the pipeline bundled as ``haute deploy`` bundles it, after the same
validation and test quotes, with the sheets, what the release is and its entry point.
``serve`` builds the release there and serves it on the analyst's machine, on loopback
alone, signed in as the analyst's git identity, so the sheets underwriters will see are
checked before a merge; the quotes saved there are kept in the ``dev`` environment, at the
database its connection in ``connections.toml`` names, checked as a hosted app checks its own.
No command changes any environment's tables: the people who own its database change them in
SQL, starting from the SQL the Workbench view's schema pane writes.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import click

if TYPE_CHECKING:
    from haute._workbench_test_quotes import Checked

#: Where ``build`` writes a release unless told otherwise, and ``serve`` always: the
#: clone's own state directory, which git ignores, model files and all.
RELEASE_DIR = Path(".haute") / "app" / "release"
#: The environment ``serve`` keeps the quotes saved in it in.
SERVE_ENVIRONMENT = "dev"
#: ``serve`` binds this address alone: everyone reaching it is signed in as the analyst.
SERVE_HOST = "127.0.0.1"


@dataclass
class AppBuildConfig:
    """Parsed inputs for the ``haute app build`` command."""

    output: Path
    #: The tag to build the release's image as, with Docker; None builds no image.
    image: str | None = None
    #: Build the release's image as ``image_reference`` names it, push it to the project's
    #: registry and print the reference by digest it pushed.
    push: bool = False


@dataclass
class AppRolloutConfig:
    """Parsed inputs for the ``haute app rollout`` command: the hosted environment rolled out,
    and the image, by digest, or the environment whose recorded release it takes."""

    environment: str
    image: str | None = None
    source: str | None = None


@dataclass
class AppImpactConfig:
    """Parsed inputs for the ``haute app impact`` command: how many of production's final
    quotes to price on both apps."""

    sample: int


@dataclass
class AppServeConfig:
    """Parsed inputs for the ``haute app serve`` command."""

    port: int
    static_dir: Path | None = None
    #: Sign in as a reader, who may not change quotes, to see the app as one sees it.
    reader: bool = False


@contextmanager
def _said() -> Iterator[None]:
    """A fault Haute or the quote store names, said as the command's error rather than a
    traceback: a project, release or store that is not as the command needs it. Any other
    error is a fault of Haute's and keeps its traceback."""
    from sqlalchemy.exc import SQLAlchemyError

    from haute.errors import HauteError

    try:
        yield
    except (HauteError, SQLAlchemyError) as exc:
        raise click.ClickException(str(exc)) from exc


def _built(project_dir: Path, release_dir: Path, *, report: bool = False) -> None:
    """Build the release, printing each test quote that did not stay unchanged; with
    *report*, write the test quotes' report in the working directory once they were checked,
    whether the build passes or fails."""
    from haute._workbench_app import TestQuotesFailedError, build_release

    try:
        with _said():
            try:
                built = build_release(project_dir, release_dir)
            except TestQuotesFailedError as exc:
                _told(exc.results, report=report)
                raise
    # A project file the build reads missing, or one it cannot parse.
    except (FileNotFoundError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    _told(built.test_quotes, report=report)
    release = built.release
    click.echo(f"  ✓ Release {release.id} of {release.pipeline_name} in {release_dir}")
    if release.host is None:
        click.echo(
            "  ! [workbench.app] host is not set: hosted, this release answers its health "
            "check alone."
        )


def _told(results: Sequence[Checked], *, report: bool) -> None:
    """Print each test quote that did not stay unchanged, and with *report* write the
    report."""
    from haute._workbench_test_quotes import REPORT_FILE, render_test_quotes_report

    for result in results:
        if result.outcome == "moved":
            fields = [
                f"{move.table}.{move.column}" if move.column else move.table
                for move in result.moves
            ] + list(result.removed)
            click.echo(f"  ! Test quote {result.file} moved: {', '.join(dict.fromkeys(fields))}")
        elif result.outcome == "no_longer_compares":
            click.echo(f"  ! Test quote {result.file} no longer compares: {result.why}")
        elif result.outcome == "failed":
            click.echo(f"  ✗ Test quote {result.file} failed: {result.why}")
    if report:
        (Path.cwd() / REPORT_FILE).write_bytes(render_test_quotes_report(results).encode("utf-8"))
        click.echo(f"  ✓ Test quotes report written: {REPORT_FILE}")


def handle_app_build(config: AppBuildConfig) -> None:
    """Build the project in the working directory into ``config.output``, and its image when
    ``config.image`` names a tag; with ``config.push``, build the image as ``image_reference``
    names it, push it and print the reference by digest it pushed as the last line."""
    from haute._workbench_app import build_image, image_reference, push_image

    project_dir = Path.cwd()
    reference: str | None = None
    if config.push:
        # A push needs a registry: a project naming none is refused before anything is built.
        try:
            with _said():
                reference = image_reference(project_dir)
        except (FileNotFoundError, ValueError) as exc:
            raise click.ClickException(str(exc)) from exc
    release_dir = config.output.resolve()
    _built(project_dir, release_dir, report=True)
    if reference is not None:
        with _said():
            pushed = push_image(release_dir, reference)
        click.echo(f"  ✓ Image pushed: {reference}")
        # The last line: what CI records and points a host at.
        click.echo(pushed)
        return
    if config.image is None:
        return
    with _said():
        build_image(release_dir, config.image)
    click.echo(f"  ✓ Image built: {config.image}")


def handle_app_rollout(config: AppRolloutConfig) -> None:
    """Roll the hosted environment's app out to the image, check it there and record it
    (specs/workbench, Control flow 35), printing each step and the release id last. Every
    refusal and failure, quotes closed by a store mismatch included, is a Click error."""
    from haute import _workbench_rollout as rollouts
    from haute._workbench_config import read_workbench_config

    project_dir = Path.cwd()
    with _said():
        workbench = read_workbench_config(project_dir)
        target = rollouts.app_environment(workbench, config.environment)
        # Every refusal comes before anything changes: the source's record is read first too.
        source = (
            None if config.source is None else rollouts.app_environment(workbench, config.source)
        )
        commit = rollouts.head_commit(project_dir)
        values = rollouts.credentials()
        if source is not None:
            assert config.source is not None
            image = rollouts.promoted(
                rollouts.AzureContainerApps(source, values), config.source, commit
            )
            click.echo(f"  ✓ {config.source} runs {image} for commit {commit[:12]}")
        else:
            assert config.image is not None
            image = config.image
        release = rollouts.rollout(
            rollouts.AzureContainerApps(target, values),
            environment=config.environment,
            image=image,
            commit=commit,
            say=click.echo,
            get=rollouts.fetch,
        )
    # The last line: the release that runs.
    click.echo(release)


def handle_app_impact(config: AppImpactConfig) -> None:
    """Compare the release staging runs with production's on production's saved quotes
    (specs/workbench, Control flow 36), printing each step, and write the report on every run
    past the refusals before a host is reached; a run that stopped or a quote staging failed
    on is a Click error after the report."""
    from haute import _workbench_impact as impacts
    from haute import _workbench_rollout as rollouts
    from haute._workbench_config import read_workbench_config

    project_dir = Path.cwd()
    with _said():
        workbench = read_workbench_config(project_dir)
        staging = rollouts.app_environment(workbench, "staging")
        production = rollouts.app_environment(workbench, "prod")
        values = rollouts.credentials()
    report = impacts.impact(
        rollouts.AzureContainerApps(production, values),
        rollouts.AzureContainerApps(staging, values),
        sample=config.sample,
        say=click.echo,
    )
    written = project_dir / impacts.REPORT_FILE
    written.write_bytes(impacts.render_report(report).encode("utf-8"))
    click.echo(f"  ✓ Report written: {impacts.REPORT_FILE}")
    if report.verdict == "stopped":
        raise click.ClickException(f"The impact stopped: {report.why}")
    if report.failed:
        raise click.ClickException(
            "Staging failed on quotes production prices:"
            + "".join(f"\n  - {quote.label}: {quote.reason}" for quote in report.failed)
        )


def handle_app_serve(config: AppServeConfig) -> None:
    """Build the project's release under ``.haute/`` and serve it on loopback, signed in as the
    analyst's git identity."""
    import uvicorn

    from haute._git_setup import get_identity
    from haute._workbench_app import create_app

    project_dir = Path.cwd()
    name, email = get_identity(project_dir)
    principal = email or name
    if principal is None:
        raise click.ClickException(
            "Set your git identity first (git config user.email): the app signs you in as it."
        )
    from haute._connections import project_environ
    from haute._quote_store import connected_store
    from haute._workbench_config import read_workbench_config

    with _said():
        # Where dev's database is, before anything is built: a project that does not say
        # stops here, naming what to set.
        store = connected_store(
            read_workbench_config(project_dir), SERVE_ENVIRONMENT, project_environ(project_dir)
        )
    release_dir = (project_dir / RELEASE_DIR).resolve()
    _built(project_dir, release_dir)
    with _said():
        application = create_app(
            release_dir,
            local_principal=principal,
            static_dir=config.static_dir,
            store=store,
            local_reader=config.reader,
        )
    reading = ", reading quotes only" if config.reader else ""
    click.echo(f"  ✓ Signed in as {principal} (your git identity){reading}")
    click.echo(f"  ✓ Quotes kept in the {SERVE_ENVIRONMENT} database ({store.named_by})")
    click.echo(f"\nThe underwriter app: http://{SERVE_HOST}:{config.port}/")
    uvicorn.run(application, host=SERVE_HOST, port=config.port, log_level="warning")


@click.group(name="app")
def app() -> None:
    """The underwriter app: the sheets apart from the builder, priced in the app."""


@app.command()
@click.argument(
    "output",
    type=click.Path(file_okay=False, path_type=Path),
    default=RELEASE_DIR,
)
@click.option(
    "--image", default=None, help="Also build the release's image with Docker, tagged so."
)
@click.option(
    "--push",
    is_flag=True,
    help="Also build the release's image and push it to [deploy.container] registry.",
)
def build(output: Path, image: str | None, push: bool) -> None:
    """Build the release the underwriter app serves into OUTPUT (default .haute/app/release): the
    image's build context, Dockerfile included."""
    if image is not None and push:
        raise click.UsageError("--image and --push each build the image: pass one.")
    handle_app_build(AppBuildConfig(output=output, image=image, push=push))


@app.command(name="rollout")
@click.argument("environment")
@click.option("--image", default=None, help="The image to roll out, named by digest.")
@click.option(
    "--from",
    "source",
    default=None,
    help="Roll out the release this environment's app runs, for the commit checked out.",
)
def rollout_command(environment: str, image: str | None, source: str | None) -> None:
    """Roll a hosted environment's app out to an image, check it there and record it."""
    if (image is None) == (source is None):
        raise click.UsageError("Pass --image or --from, one of them.")
    handle_app_rollout(AppRolloutConfig(environment=environment, image=image, source=source))


@app.command(name="impact")
@click.option(
    "--sample",
    default=100,
    show_default=True,
    type=click.IntRange(1, 1_000),
    help="How many of production's final quotes to price on both apps.",
)
def impact_command(sample: int) -> None:
    """Compare staging's release with production's on production's saved quotes."""
    handle_app_impact(AppImpactConfig(sample=sample))


@app.command()
@click.option("--port", default=8001, show_default=True, help="Port to serve on, on 127.0.0.1.")
@click.option(
    "--static-dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help="A built frontend to serve the page from, in place of Haute's own.",
)
@click.option(
    "--reader",
    is_flag=True,
    help="Sign in as an underwriter who may read quotes but not change them.",
)
def serve(port: int, static_dir: Path | None, reader: bool) -> None:
    """Build the project's release and serve it here, signed in as your git identity."""
    handle_app_serve(AppServeConfig(port=port, static_dir=static_dir, reader=reader))
