"""``haute app`` commands: the underwriter app, built and served from the project.

Split into:

* :class:`AppBuildConfig` and :class:`AppServeConfig` — the typed bags of CLI inputs.
* :func:`handle_app_build`, :func:`handle_app_serve` and :func:`handle_app_migrate` — the
  functions that do the work.
* :func:`app` and its ``build``, ``serve`` and ``migrate`` — the thin ``@click`` entry
  points.

``build`` writes the project's release into a directory, ``.haute/app/release`` unless
another is named: the pipeline bundled as ``haute deploy`` bundles it, after the same
validation and test quotes, with the sheets, what the release is and its entry point.
``serve`` builds the release there and serves it on the analyst's machine, on loopback
alone, signed in as the analyst's git identity, so the sheets underwriters will see are
checked before a merge; the quotes saved there are kept in dev's quote store, a SQLite file
beside the release, not in it, so a rebuild keeps them. Its tables are the form's, brought to
it as it starts by the store's planner in dev's mode, which drops what the form removed and
builds again what changed while holding values, naming each. ``migrate`` brings a hosted
environment's quote store to the tables of a built release's form before that release starts,
the environment one the release declares, its database where the host's own environment says
it is, or prints the plan, offline from another release's form with ``--base``, or prints it
as the SQL a DBA runs.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
    from haute._quote_store import DEV, prepare_store, store_url
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
            environment=DEV,
            haute_version=release.haute_version,
            release=release.id,
        )
    for name in made.dropped:
        click.echo(f"  ! The form's change took {name}'s quotes' rows")
    for name in made.cleared:
        click.echo(f"  ! The form's change took the quotes' values in {name}")
    return url


@dataclass
class AppMigrateConfig:
    """Parsed inputs for the ``haute app migrate`` command."""

    environment: str
    release_dir: Path
    #: Print the plan and change nothing.
    plan: bool = False
    #: Print the plan as the SQL a DBA runs and change nothing.
    sql: bool = False
    #: A built release whose form the plan starts from, with no database.
    base_dir: Path | None = None
    #: The backup or point in time the database can be returned to.
    restore_point: str | None = None


def _release_of(release_dir: Path) -> tuple[Any, Any]:
    from haute._workbench_app import RELEASE_FILE, read_release

    if not (release_dir / RELEASE_FILE).is_file():
        raise click.ClickException(
            f"No release is built in {release_dir.as_posix()}: run haute app build first."
        )
    with _said():
        release, form, _ = read_release(release_dir.resolve())
    return release, form


def handle_app_migrate(config: AppMigrateConfig) -> None:
    """Bring the quote store of ``config.environment``, one the release built in
    ``config.release_dir`` declares, at the database the host's own environment says it is at,
    to that release's form through the planner in a hosted environment's mode; or print the
    plan or its SQL, changing nothing, offline from ``config.base_dir``'s form when given.
    Nothing it prints names where the database is."""
    import os

    from haute._quote_store import (
        offline_plan,
        plan_store,
        prepare_store,
        render_sql,
        render_text,
        resolve_store,
    )
    from haute._quote_store._migrate import PROVIDER_DIALECTS
    from haute._quote_store._providers import PROVIDERS

    release, form = _release_of(config.release_dir)
    if config.base_dir is not None and not (config.plan or config.sql):
        raise click.ClickException("--base plans with no database: give --plan or --sql with it.")
    sql_schema: str | None = None
    with _said():
        declared = {each.name: each for each in release.store_environments()}
        if config.base_dir is not None:
            if config.environment not in declared:
                # Offline, no location is resolved: the environment is named and its provider read.
                resolve_store(tuple(declared.values()), config.environment, {})
            _, base = _release_of(config.base_dir)
            made = offline_plan(
                base, form, provider=PROVIDERS[declared[config.environment].provider]
            )
        else:
            store = resolve_store(tuple(declared.values()), config.environment, os.environ)
            sql_schema = store.sql_schema
            if config.plan or config.sql:
                made = plan_store(
                    store.url,
                    form,
                    project=release.pipeline_name,
                    environment=store.environment,
                    sql_schema=store.sql_schema,
                    sign_in=store.sign_in,
                )
            else:
                made = prepare_store(
                    store.url,
                    form,
                    project=release.pipeline_name,
                    environment=store.environment,
                    haute_version=release.haute_version,
                    release=release.id,
                    restore_point=config.restore_point,
                    sql_schema=store.sql_schema,
                    sign_in=store.sign_in,
                )
                click.echo(render_text(made))
                click.echo(
                    f"  ✓ The {store.environment} quote store holds the tables of release "
                    f"{release.id}'s form"
                )
                return
        if config.plan:
            click.echo(render_text(made))
        if config.sql:
            dialect = PROVIDER_DIALECTS[declared[config.environment].provider]
            click.echo(
                render_sql(
                    made,
                    form,
                    dialect,
                    project=release.pipeline_name,
                    environment=config.environment,
                    release=release.id,
                    haute_version=release.haute_version,
                    restore_point=config.restore_point,
                    sql_schema=sql_schema,
                )
            )


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


@app.command()
@click.option(
    "--environment",
    required=True,
    help="The hosted environment whose quote store is migrated, one the release declares.",
)
@click.option(
    "--release",
    "release_dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=RELEASE_DIR,
    show_default=True,
    help="The built release whose form's tables the store holds.",
)
@click.option("--plan", "show_plan", is_flag=True, help="Print the plan and change nothing.")
@click.option(
    "--sql",
    "show_sql",
    is_flag=True,
    help="Print the plan as the SQL a DBA runs, changing nothing.",
)
@click.option(
    "--base",
    "base_dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="A built release to plan from with no database: the change from its form to this one's.",
)
@click.option(
    "--restore-point",
    default=None,
    help="The backup or point in time the database can be returned to, which a plan only a "
    "restore reverses needs.",
)
def migrate(
    environment: str,
    release_dir: Path,
    show_plan: bool,
    show_sql: bool,
    base_dir: Path | None,
    restore_point: str | None,
) -> None:
    """Bring a hosted environment's quote store to the tables of a built release's form, before
    that release starts, or print the plan."""
    handle_app_migrate(
        AppMigrateConfig(
            environment=environment,
            release_dir=release_dir,
            plan=show_plan,
            sql=show_sql,
            base_dir=base_dir,
            restore_point=restore_point,
        )
    )
