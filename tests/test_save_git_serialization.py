"""Pipeline saves and repairs share the repository's Git critical section.

A save writes its Python, configuration and sidecars one file at a time, and a repair
rewrites a pipeline and its configuration the same way. A milestone, restore or switch
running between two of those writes would record or replace part of the write, so each
writer holds the repository's mutation lock through its whole transaction and a Git action
that arrives meanwhile waits for it. Each test pauses a real writer between two artifact
writes, starts a real Git action on another thread and checks what the action recorded.
"""

from __future__ import annotations

import json
import subprocess
import threading
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

import haute._git_core as git_core
import haute.routes._save_pipeline as save_pipeline
from haute._git import commit_milestone, commit_save, restore_version, set_working_branch
from haute._pipeline_recovery import load_pipeline_editor_document
from haute._types import GraphEdge, GraphNode, NodeData
from haute.parser import parse_pipeline_file
from haute.routes._helpers import commit_pipeline_graph
from haute.routes._save_pipeline import SavePipelineService
from haute.schemas import (
    PipelineNodeSaveRequest,
    PipelineRepairRecoverRequest,
    SavePipelineRequest,
)

WORKING = "pricing"
# How long a Git action is watched while the writer it must wait for is paused.
_WATCH_SECONDS = 1.0

BANDING = {
    "factors": [
        {
            "banding": "breakpoints",
            "column": "age",
            "outputColumn": "age_band",
            "rules": [{"boundary": "30", "label": "young"}],
        }
    ]
}

PIPELINE = (
    'import haute\nimport polars as pl\n\npipeline = haute.Pipeline("main")\n\n\n'
    "@pipeline.polars\ndef source() -> pl.LazyFrame:\n"
    '    return pl.LazyFrame({"age": [20, 40]})\n'
)

# A Data Input whose locator is empty: the document is degraded and the node scoped-editable.
INCOMPLETE_INPUT = {
    "inputType": "file",
    "format": "parquet",
    "mode": "scan",
    "path": "",
    "arguments": {},
    "code": "",
    "cacheMode": "snapshot",
}


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=True)
    return result.stdout.strip()


def _repository(root: Path, files: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> Path:
    """A committed project on its own working branch, HEAD on the save ledger.

    The working directory moves into it: Git actions called without ``cwd`` run there."""
    root.mkdir()
    monkeypatch.chdir(root)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.name", "Test User")
    _git(root, "config", "user.email", "test@example.com")
    for name, text in {".gitignore": ".haute/\n", **files}.items():
        (root / name).write_text(text, encoding="utf-8", newline="\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "Initial project")
    _git(root, "checkout", "-q", "-b", WORKING)
    set_working_branch(WORKING, root, cwd=root)
    return root


def _revision(root: Path) -> str | None:
    return load_pipeline_editor_document(root / "main.py", project_root=root).source_revision


def _save_request(root: Path, *, banding: bool) -> SavePipelineRequest:
    graph = parse_pipeline_file(root / "main.py")
    if banding:
        graph.nodes.append(
            GraphNode(id="Bands", data=NodeData(label="Bands", nodeType="banding", config=BANDING))
        )
        graph.edges.append(GraphEdge(id="source-Bands", source="source", target="Bands"))
    return SavePipelineRequest(
        graph=graph,
        name="main",
        description="",
        source_file="main.py",
        base_revision=_revision(root),
    )


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A pipeline saved once through the service, so its ledger holds a save to commit."""
    root = _repository(
        tmp_path / "project", {"haute.toml": '[project]\nname = "demo"\n'}, monkeypatch
    )
    (root / "main.py").write_text(PIPELINE, encoding="utf-8")
    SavePipelineService(project_root=root).save(_save_request(root, banding=False))
    return root


class _Pause:
    """Holds a writer at one step until released, remembering the writer's thread."""

    def __init__(self) -> None:
        self.reached = threading.Event()
        self.release = threading.Event()
        self.thread: int | None = None

    def hold(self) -> None:
        self.thread = threading.get_ident()
        self.reached.set()
        assert self.release.wait(10), "the paused writer was never released"


def _watch_git_actions(monkeypatch: pytest.MonkeyPatch, pause: _Pause) -> threading.Event:
    """Set once a thread other than the paused writer's holds the repository lock."""
    entered = threading.Event()
    real = git_core.repository_mutation

    @contextmanager
    def watched(path: Path) -> Iterator[None]:
        with real(path):
            if threading.get_ident() != pause.thread:
                entered.set()
            yield

    monkeypatch.setattr(git_core, "repository_mutation", watched)
    return entered


def _race(
    write: Callable[[], Any],
    act: Callable[[], Any],
    pause: _Pause,
    entered: threading.Event,
    *,
    while_paused: Callable[[], None] = lambda: None,
) -> tuple[Callable[[], Any], Callable[[], Any]]:
    """Pause *write* at its hold, start *act*, and prove *act* waited for the writer.

    Returns the two outcomes as calls that return the result or raise its exception."""
    with ThreadPoolExecutor(max_workers=2) as pool:
        written = pool.submit(write)
        assert pause.reached.wait(10), "the writer never reached its pause"
        while_paused()
        acted = pool.submit(act)
        try:
            assert not entered.wait(_WATCH_SECONDS), "the Git action ran inside the write"
        finally:
            pause.release.set()
        written.exception(10)
        acted.exception(10)
    return (lambda: written.result()), (lambda: acted.result())


def _hold_after_writes(monkeypatch: pytest.MonkeyPatch, pause: _Pause, *, fail: bool) -> None:
    """Pause a repair after its first staged write; with *fail*, its next write then fails."""
    real = save_pipeline._stage_artifact_write_bytes
    writes = 0

    def staged(path: Path, payload: bytes, touched: list[Any]) -> None:
        nonlocal writes
        if writes == 1 and fail:
            raise PermissionError("the second artifact is held by another program")
        real(path, payload, touched)
        writes += 1
        if writes == 1:
            pause.hold()

    monkeypatch.setattr(save_pipeline, "_stage_artifact_write_bytes", staged)


def _tree(root: Path, commit: str) -> str:
    return _git(root, "rev-parse", f"{commit}^{{tree}}")


def _blob(root: Path, commit: str, path: str) -> str:
    return _git(root, "show", f"{commit}:{path}")


def _paths(root: Path, commit: str) -> set[str]:
    return set(_git(root, "ls-tree", "-r", "--name-only", commit).splitlines())


# ---------------------------------------------------------------------------
# The full save transaction
# ---------------------------------------------------------------------------


def _hold_before_configs(monkeypatch: pytest.MonkeyPatch, pause: _Pause, *, fail: bool) -> None:
    """Pause a save once its Python is written and before its configuration is."""
    real = SavePipelineService._write_config_files

    def configs(self: SavePipelineService, graph: Any, touched: list[Any]) -> Any:
        pause.hold()
        if fail:
            raise OSError("the configuration folder is full")
        return real(self, graph, touched)

    monkeypatch.setattr(SavePipelineService, "_write_config_files", configs)


def _python_written_config_not(root: Path) -> None:
    assert "config/banding/Bands.json" in (root / "main.py").read_text(encoding="utf-8")
    assert not (root / "config" / "banding" / "Bands.json").exists()


def test_a_milestone_waits_for_a_save_and_records_its_whole_artifact_set(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pause = _Pause()
    _hold_before_configs(monkeypatch, pause, fail=False)
    entered = _watch_git_actions(monkeypatch, pause)
    request = _save_request(project, banding=True)

    saved, committed = _race(
        lambda: SavePipelineService(project_root=project).save(request),
        lambda: commit_milestone("Add bands", project),
        pause,
        entered,
        while_paused=lambda: _python_written_config_not(project),
    )

    response = saved()
    milestone = committed()
    assert _tree(project, milestone.sha) == _tree(project, response.git_sha)
    assert {"main.py", "config/banding/Bands.json"} <= _paths(project, milestone.sha)
    historical = commit_pipeline_graph(milestone.sha, "main.py")
    on_disk = parse_pipeline_file(project / "main.py")
    assert [(n.id, n.data.nodeType, n.data.config) for n in historical.nodes] == [
        (n.id, n.data.nodeType, n.data.config) for n in on_disk.nodes
    ]
    assert historical.node_map["Bands"].data.config["factors"] == BANDING["factors"]


def test_a_failed_save_rolls_back_before_the_waiting_milestone_records_anything(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = (project / "main.py").read_text(encoding="utf-8")
    pause = _Pause()
    _hold_before_configs(monkeypatch, pause, fail=True)
    entered = _watch_git_actions(monkeypatch, pause)
    request = _save_request(project, banding=True)

    saved, committed = _race(
        lambda: SavePipelineService(project_root=project).save(request),
        lambda: commit_milestone("After the failed save", project),
        pause,
        entered,
        while_paused=lambda: _python_written_config_not(project),
    )

    with pytest.raises(OSError, match="configuration folder is full"):
        saved()
    milestone = committed()
    assert (project / "main.py").read_text(encoding="utf-8") == before
    assert _blob(project, milestone.sha, "main.py") == before.rstrip("\n")
    assert "config/banding/Bands.json" not in _paths(project, milestone.sha)


def test_a_restore_waits_for_a_save_so_the_save_is_recorded_before_it(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    initial = _git(project, "rev-parse", "main")
    pause = _Pause()
    _hold_before_configs(monkeypatch, pause, fail=False)
    entered = _watch_git_actions(monkeypatch, pause)
    request = _save_request(project, banding=True)

    saved, restored = _race(
        lambda: SavePipelineService(project_root=project).save(request),
        lambda: restore_version(initial, project),
        pause,
        entered,
    )

    response = saved()
    restore = restored()
    # The save was captured whole, then the restore went back from it as one more save.
    assert _git(project, "rev-parse", f"{restore.sha}^") == response.git_sha
    assert {"main.py", "config/banding/Bands.json"} <= _paths(project, response.git_sha)
    assert _tree(project, restore.sha) == _tree(project, initial)
    assert not (project / "config" / "banding" / "Bands.json").exists()


# ---------------------------------------------------------------------------
# The node-scoped and repair transaction
# ---------------------------------------------------------------------------


def _scoped_project(root: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Two Data Inputs without a locator; the second recovered, so it saves in isolation."""
    from haute._pipeline_repair import apply_recover_unavailable_node_plan

    project = _repository(
        root,
        {
            "haute.toml": '[project]\nname = "demo"\n',
            "main.py": (
                "import haute\nimport polars as pl\n"
                'pipeline = haute.Pipeline("demo")\n'
                '@pipeline.data_input(config="a.json")\ndef source_a(): ...\n'
                '@pipeline.data_input(config="b.json")\ndef source_b(): ...\n'
            ),
            "a.json": json.dumps(INCOMPLETE_INPUT),
            "b.json": json.dumps(INCOMPLETE_INPUT),
        },
        monkeypatch,
    )
    document = load_pipeline_editor_document(project / "main.py", project_root=project)
    target = next(node for node in document.nodes if node.authored_id == "source_b")
    apply_recover_unavailable_node_plan(
        project_root=project,
        request=PipelineRepairRecoverRequest(
            source_file=document.source_file,
            source_revision=document.source_revision,
            target_source_file=target.source_file,
            target_recovery_id=target.recovery_id,
            action="recover",
        ),
    )
    _git(project, "add", "-A")
    _git(project, "commit", "-q", "-m", "Recover source_b")
    return project


def _broken_constant_project(root: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A constant whose configuration is malformed: its reset rewrites main.py and custom.json."""
    return _repository(
        root,
        {
            "haute.toml": '[project]\nname = "demo"\n',
            "custom.json": "{broken json",
            "main.py": (
                "import haute\nimport polars as pl\n"
                'pipeline = haute.Pipeline("demo")\n'
                '@pipeline.constant(config="custom.json")\ndef source():\n    pass\n'
            ),
        },
        monkeypatch,
    )


def _reset_request(root: Path) -> PipelineRepairRecoverRequest:
    document = load_pipeline_editor_document(root / "main.py", project_root=root)
    target = next(node for node in document.nodes if node.authored_id == "source")
    return PipelineRepairRecoverRequest(
        source_file=document.source_file,
        source_revision=document.source_revision,
        target_source_file=target.source_file,
        target_recovery_id=target.recovery_id,
        action="reset",
    )


def _pending_note(root: Path) -> None:
    """A save the next milestone has to commit, whatever the repair leaves."""
    (root / "notes.md").write_text("Reviewed.\n", encoding="utf-8")
    assert commit_save(["notes.md"], WORKING, cwd=root, message="Notes") is not None


def test_a_milestone_waits_for_a_node_scoped_config_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from haute._pipeline_repair_actions import apply_scoped_node_save

    project = _scoped_project(tmp_path / "project", monkeypatch)
    document = load_pipeline_editor_document(project / "main.py", project_root=project)
    target = next(node for node in document.nodes if node.authored_id == "source_b")
    request = PipelineNodeSaveRequest(
        source_file=document.source_file,
        source_revision=document.source_revision,
        target_source_file=target.source_file,
        target_recovery_id=target.recovery_id,
        config={**target.config, "path": "data/quotes.parquet"},
    )
    pause = _Pause()
    _hold_after_writes(monkeypatch, pause, fail=False)
    entered = _watch_git_actions(monkeypatch, pause)

    saved, committed = _race(
        lambda: apply_scoped_node_save(project_root=project, request=request),
        lambda: commit_milestone("Point source_b at the quotes", project),
        pause,
        entered,
    )

    saved()
    milestone = committed()
    assert json.loads(_blob(project, milestone.sha, "b.json"))["path"] == "data/quotes.parquet"
    assert _git(project, "status", "--porcelain", "--untracked-files=no") == ""


def test_a_milestone_waits_for_a_repair_rewriting_several_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from haute._pipeline_repair import apply_recover_unavailable_node_plan

    project = _broken_constant_project(tmp_path / "project", monkeypatch)
    request = _reset_request(project)
    pause = _Pause()
    _hold_after_writes(monkeypatch, pause, fail=False)
    entered = _watch_git_actions(monkeypatch, pause)

    repaired, committed = _race(
        lambda: apply_recover_unavailable_node_plan(project_root=project, request=request),
        lambda: commit_milestone("Reset the constant", project),
        pause,
        entered,
    )

    repaired()
    milestone = committed()
    for path in ("main.py", "custom.json"):
        assert _blob(project, milestone.sha, path) == (project / path).read_text(
            encoding="utf-8"
        ).rstrip("\n")
    assert _blob(project, milestone.sha, "custom.json") != "{broken json"


def test_a_failed_repair_rolls_back_before_the_waiting_milestone_records_anything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from haute._pipeline_repair import PipelineRepairError, apply_recover_unavailable_node_plan

    project = _broken_constant_project(tmp_path / "project", monkeypatch)
    _pending_note(project)
    before = {
        path: (project / path).read_text(encoding="utf-8") for path in ("main.py", "custom.json")
    }
    request = _reset_request(project)
    pause = _Pause()
    _hold_after_writes(monkeypatch, pause, fail=True)
    entered = _watch_git_actions(monkeypatch, pause)

    repaired, committed = _race(
        lambda: apply_recover_unavailable_node_plan(project_root=project, request=request),
        lambda: commit_milestone("Notes only", project),
        pause,
        entered,
    )

    with pytest.raises((PermissionError, PipelineRepairError)):
        repaired()
    milestone = committed()
    for path, text in before.items():
        assert (project / path).read_text(encoding="utf-8") == text
        assert _blob(project, milestone.sha, path) == text.rstrip("\n")
    assert _blob(project, milestone.sha, "notes.md") == "Reviewed."
