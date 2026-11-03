"""The project a test runs in.

Every test runs with its ``tmp_path`` installed as the project (the autouse
``_installed_project`` fixture in ``tests/conftest.py``), as the server, the CLI
and a standalone run install theirs; ``@pytest.mark.no_project`` runs one with
none. A test that works in another folder installs it with :func:`use_project`,
and one that needs the editor server builds it for the installed project with
:func:`project_app`. A test never changes directory itself: :func:`use_project` runs
it in its project's folder, as ``haute serve`` runs in its project's root.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from haute._project_context import _CURRENT_PROJECT, ProjectContext, current_project

if TYPE_CHECKING:
    from fastapi import FastAPI


def use_project(root: str | Path) -> ProjectContext:
    """Install *root* as the project for the rest of the test, and run in its folder.

    Haute's code reads the installed project; the user's pipeline code reads a
    relative path from the working directory, which ``haute serve`` makes its
    project's root, so the test runs there too (a folder the test has not made
    yet is entered when the test makes it, through a later call). The autouse
    fixture's teardown restores the project and the working directory the test
    started with.
    """
    context = ProjectContext.at(Path(root))
    _CURRENT_PROJECT.set(context)
    if context.root.is_dir():
        os.chdir(context.root)
    return context


def project_app() -> FastAPI:
    """The editor server built for the installed project."""
    from haute.server import create_app

    return create_app(current_project())


class FirstRequestProjectApp:
    """The editor server for the project installed when it first serves.

    A fixture may request a client before it installs the test's project; the app
    is built at the first request (or lifespan), when that project is installed,
    and serves it from then on, as ``haute serve`` serves the project it started in.
    """

    def __init__(self) -> None:
        self._app: FastAPI | None = None

    async def __call__(self, scope: object, receive: object, send: object) -> None:
        if self._app is None:
            self._app = project_app()
        await self._app(scope, receive, send)  # type: ignore[arg-type]


def make_haute_project(root: Path) -> Path:
    """Make *root* a Haute project: a ``haute.toml`` inside a git repository."""
    root.mkdir(parents=True, exist_ok=True)
    toml = root / "haute.toml"
    if not toml.exists():
        toml.write_text("[project]\n", encoding="utf-8")
    (root / ".git").mkdir(exist_ok=True)
    return root


def work_elsewhere(folder: Path) -> None:
    """Run the rest of the test in *folder*, keeping the installed project.

    For a test proving Haute reads the installed project, never the working
    directory. The autouse fixture's teardown restores the working directory.
    """
    os.chdir(folder)
