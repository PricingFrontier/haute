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
checked before a merge; the quotes saved there are kept in dev's quote store, a SQLite file
beside the release, not in it, so a rebuild keeps them. Its tables are the form's, brought to
it as it starts by the store's planner in dev's mode, which drops what the form removed and
builds again what changed while holding values, naming each. No command changes a hosted
environment's tables: the people who own its database change them in SQL.
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
#: Where ``serve`` keeps the quotes saved in it, dev's quote store: beside the release,
#: which a build replaces.
QUOTES_FILE = Path(".haute") / "app" / "store.sqlite"
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
    from haute._quote_store import DEV, StoreLocation

    release_dir = (project_dir / RELEASE_DIR).resolve()
    _built(project_dir, release_dir)
    url = _local_store(project_dir, release_dir)
    with _said():
        application = create_app(
            release_dir,
            local_principal=principal,
            static_dir=config.static_dir,
            store=StoreLocation(DEV, url),
            local_reader=config.reader,
        )
    reading = ", reading quotes only" if config.reader else ""
    click.echo(f"  ✓ Signed in as {principal} (your git identity){reading}")
    click.echo(f"  ✓ Quotes kept in {QUOTES_FILE.as_posix()}")
    click.echo(f"\nThe underwriter app: http://{SERVE_HOST}:{config.port}/")
    uvicorn.run(application, host=SERVE_HOST, port=config.port, log_level="warning")


def _local_store(project_dir: Path, release_dir: Path) -> str:
    """The URL of ``serve``'s own quote store, dev's, brought to the tables of the form of the
    release in *release_dir* as it starts: each table whose quotes' rows dev's plan drops, and
    each column whose values it drops, named."""
    from haute._quote_store import prepare_store, store_url
    from haute._workbench_app import read_release

    path = project_dir / QUOTES_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    url = store_url(path)
    with _said():
        release, form, _ = read_release(release_dir)
        made = prepare_store(
            url,
            form,
            project=release.pipeline_name,
            haute_version=release.haute_version,
            release=release.id,
        )
    for name in made.dropped:
        click.echo(f"  ! The form's change took {name}'s quotes' rows")
    for name in made.cleared:
        click.echo(f"  ! The form's change took the quotes' values in {name}")
    return url


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
