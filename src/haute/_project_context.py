"""The project a call works in: resolved once, installed for the call.

A process resolves its project once, where it starts (the CLI, the server, a
standalone run), with :func:`resolve_project`. Every call then reads it from
:func:`current_project`, which never consults the working directory or a
process global. The server installs its project for each request, a job
thread runs in the project of the request that started it, and every worker
request carries the context so a warm worker installs it for that request
alone.
"""

from __future__ import annotations

import contextvars
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from haute.errors import HauteError

if TYPE_CHECKING:
    from haute._types import PipelineGraph

__all__ = [
    "NoProjectContextError",
    "ProjectContext",
    "ProjectContextMiddleware",
    "context_thread",
    "current_project",
    "graph_pipeline_dir",
    "project_holding",
    "project_scope",
    "resolve_project",
    "standalone_project_scope",
]


class NoProjectContextError(HauteError):
    """A call that works in a project was made with no project installed."""


@dataclass(frozen=True, slots=True)
class ProjectContext:
    """The project a call works in: its root, the folder holding ``haute.toml``.

    The root is absolute and resolved. The value is small and picklable, so a
    worker request carries it. What the project's ``haute.toml`` says is read
    from the root when used (:attr:`pipeline_dir`), so an edit to the file
    reaches the next call, a worker's included.
    """

    root: Path

    def __post_init__(self) -> None:
        if not self.root.is_absolute():
            raise ValueError(f"A project context takes an absolute root, not {self.root!s}.")

    @classmethod
    def at(cls, root: Path) -> ProjectContext:
        """The context rooted at *root* itself, without looking above it.

        A standalone script in no project runs in its own folder this way, and
        deploy installs its validated root with it.
        """
        return cls(root.resolve())

    @property
    def pipeline_dir(self) -> Path:
        """The folder of the pipeline ``haute.toml`` names in ``[project].pipeline``.

        The root when there is no ``haute.toml`` or it names none: the folder the
        project's pipeline-relative files (its ``utility`` package, its JSON
        caches) are found in. A malformed ``haute.toml``, or a pipeline it names
        outside the root, raises :class:`~haute.errors.ConfigError`.
        """
        from haute._project import _toml_configured_pipeline

        configured = _toml_configured_pipeline(self.root)
        return self.root if configured is None else configured.resolve().parent


def resolve_project(start: Path) -> ProjectContext:
    """Resolve the Haute project that holds *start*: the one project resolver.

    The root is the first folder at or above *start* holding a ``haute.toml``
    inside a git repository; anywhere else raises
    :class:`~haute.errors.ConfigError`.
    """
    from haute._project import get_project_root

    return ProjectContext.at(get_project_root(start))


_CURRENT_PROJECT: ContextVar[ProjectContext | None] = ContextVar(
    "haute_project_context", default=None
)


@contextmanager
def project_scope(context: ProjectContext) -> Iterator[ProjectContext]:
    """Install *context* as the current project for the block."""
    token = _CURRENT_PROJECT.set(context)
    try:
        yield context
    finally:
        _CURRENT_PROJECT.reset(token)


def current_project() -> ProjectContext:
    """The project installed for this call.

    Raises :class:`NoProjectContextError` when none is: a project is never
    taken from the working directory.
    """
    context = _CURRENT_PROJECT.get()
    if context is None:
        raise NoProjectContextError(
            "No Haute project is installed for this call. Run it through the "
            "Haute CLI or server, or install one with project_scope()."
        )
    return context


def installed_project() -> ProjectContext | None:
    """The installed project, or ``None``: for an entry point that installs its own."""
    return _CURRENT_PROJECT.get()


def graph_pipeline_dir(graph: PipelineGraph) -> Path | None:
    """The folder of the pipeline file *graph* was parsed from, or ``None`` for none.

    A relative ``source_file`` names a file under the current project's root.
    """
    source_file = graph.source_file
    if not source_file:
        return None
    path = Path(source_file)
    if not path.is_absolute():
        path = current_project().root / path
    return path.resolve().parent


@contextmanager
def standalone_project_scope(source_file: str | Path | None) -> Iterator[ProjectContext]:
    """The project a standalone call runs in, installed for the block.

    ``Pipeline.run()``, ``Pipeline.score()`` and a configured node called
    directly run outside any Haute entry point. A project already installed
    wins (deploy's validated one). Otherwise the call runs in the Haute project
    holding its pipeline file, or in the file's own folder when no project
    holds it; a pipeline with no defining file (one built in an interactive
    session) runs in the project holding the session's working directory, or
    in that directory: like the CLI, the session is the entry point.
    """
    active = _CURRENT_PROJECT.get()
    if active is not None:
        yield active
        return
    folder = Path.cwd() if source_file is None else Path(source_file).resolve().parent
    with project_scope(project_holding(folder)) as context:
        yield context


def project_holding(folder: Path) -> ProjectContext:
    """The Haute project holding *folder*, or *folder* itself when no project does.

    What a pipeline file given to a standalone run or a CLI command runs in.
    """
    from haute._project import get_project_root
    from haute.errors import ConfigError

    try:
        return ProjectContext.at(get_project_root(folder))
    except ConfigError:
        return ProjectContext.at(folder)


class ProjectContextMiddleware:
    """ASGI middleware installing one project for everything an app serves.

    Every HTTP request, websocket and the lifespan run with the app's project
    installed, whatever context the server or a test client calls the app
    from, so an app built for a project serves that project alone.
    """

    def __init__(self, app: Any, *, context: ProjectContext) -> None:
        self.app = app
        self.context = context

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        with project_scope(self.context):
            await self.app(scope, receive, send)


def context_thread(
    target: Callable[..., object],
    *,
    args: tuple[Any, ...] = (),
    kwargs: dict[str, Any] | None = None,
    name: str | None = None,
    daemon: bool | None = None,
) -> threading.Thread:
    """A thread running *target* in a copy of the starting caller's context.

    A new thread starts with an empty context, so a job thread started this
    way runs in the project (and with the log fields) of the request that
    started it.
    """
    context = contextvars.copy_context()
    return threading.Thread(
        target=context.run,
        args=(target, *args),
        kwargs=kwargs or {},
        name=name,
        daemon=daemon,
    )
