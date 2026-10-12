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
checked before a merge.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import click

#: Where ``build`` writes a release unless told otherwise, and ``serve`` always: the
#: clone's own state directory, which git ignores, model files and all.
RELEASE_DIR = Path(".haute") / "app" / "release"
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


def _built(project_dir: Path, release_dir: Path) -> None:
    from haute._workbench_app import WorkbenchAppError, build_release

    try:
        release = build_release(project_dir, release_dir)
    except (WorkbenchAppError, FileNotFoundError, ValueError) as exc:
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
    from haute._workbench_app import WorkbenchAppError, build_image

    release_dir = config.output.resolve()
    _built(Path.cwd(), release_dir)
    if config.image is None:
        return
    try:
        build_image(release_dir, config.image)
    except WorkbenchAppError as exc:
        raise click.ClickException(str(exc)) from exc
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
    release_dir = (project_dir / RELEASE_DIR).resolve()
    _built(project_dir, release_dir)
    application = create_app(release_dir, local_principal=principal, static_dir=config.static_dir)
    click.echo(f"  ✓ Signed in as {principal} (your git identity)")
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
def serve(port: int, static_dir: Path | None) -> None:
    """Build the project's release and serve it here, signed in as your git identity."""
    handle_app_serve(AppServeConfig(port=port, static_dir=static_dir))
