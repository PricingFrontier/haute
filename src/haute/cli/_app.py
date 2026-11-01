"""``haute app`` commands: the underwriter app, built and served from the project.

Split into:

* :class:`AppBuildConfig` and :class:`AppServeConfig` — the typed bags of CLI inputs.
* :func:`handle_app_build` and :func:`handle_app_serve` — the functions that do the work.
* :func:`app` and its ``build`` and ``serve`` — the thin ``@click`` entry points.

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

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import click

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


def _built(project_dir: Path, release_dir: Path) -> None:
    from haute._workbench_app import build_release

    try:
        with _said():
            release = build_release(project_dir, release_dir)
    # A project file the build reads missing, or one it cannot parse.
    except (FileNotFoundError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"  ✓ Release {release.id} of {release.pipeline_name} in {release_dir}")
    if release.host is None:
        click.echo(
            "  ! [workbench.app] host is not set: hosted, this release answers its health "
            "check alone."
        )


def handle_app_build(config: AppBuildConfig) -> None:
    """Build the project in the working directory into ``config.output``, and its image when
    ``config.image`` names a tag."""
    from haute._workbench_app import build_image

    release_dir = config.output.resolve()
    _built(Path.cwd(), release_dir)
    if config.image is None:
        return
    with _said():
        build_image(release_dir, config.image)
    click.echo(f"  ✓ Image built: {config.image}")


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
def build(output: Path, image: str | None) -> None:
    """Build the release the underwriter app serves into OUTPUT (default .haute/app/release): the
    image's build context, Dockerfile included."""
    handle_app_build(AppBuildConfig(output=output, image=image))


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
