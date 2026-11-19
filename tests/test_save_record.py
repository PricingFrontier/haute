"""A save's progress and outcome are on disk (``haute/_save_records.py``).

A save is stopped before each change it makes to the project's files in turn, with the
files left as they were at that instant, as a server killed there leaves them, and the
server started again over them: each load is the save committed, answering a repeat of it
with its first answer, or a warning on a ready document naming the interrupted save's files,
and no staging file is left or reaches a Commit. Each step made to fail instead leaves the
earlier document or the save committed. A stale file whose removal fails leaves the save
answered as saved and the next save removes it; a save that raised after it committed is
answered from its receipt when repeated.
"""

from __future__ import annotations

import errno
import os
import shutil
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import haute._save_records as save_records
import haute.routes._save_pipeline as save_pipeline
from haute._event_bus import default_bus
from haute._git import commit_milestone, set_working_branch
from haute._pipeline_recovery import load_pipeline_editor_document
from haute._types import GraphEdge, GraphNode, NodeData
from haute.parser import parse_pipeline_file
from haute.routes._save_pipeline import SavePipelineService, settle_saves
from haute.schemas import PipelineEditorDocument, SavePipelineRequest, SavePipelineResponse
from tests._project import project_app, use_project

WORKING = "pricing"
PIPELINE = (
    'import haute\nimport polars as pl\n\npipeline = haute.Pipeline("main")\n\n\n'
    "@pipeline.polars\ndef source() -> pl.LazyFrame:\n"
    '    return pl.LazyFrame({"age": [20, 40]})\n'
)


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=True)
    return result.stdout.strip()


def _banding(boundary: str) -> dict[str, Any]:
    return {
        "factors": [
            {
                "banding": "breakpoints",
                "column": "age",
                "output_column": "age_band",
                "rules": [{"boundary": boundary, "label": "young"}],
                "default": None,
            }
        ]
    }


def _revision(root: Path) -> str | None:
    return _document(root).source_revision


def _document(root: Path) -> PipelineEditorDocument:
    return load_pipeline_editor_document(root / "rating" / "main.py", project_root=root)


def _request(
    root: Path, bands: dict[str, str], operation: str | None, *, base: str | None = None
) -> SavePipelineRequest:
    """The pipeline's source node and a banding node per entry of *bands*, its label to its
    boundary, saved over the revision on disk (or *base*)."""
    graph = parse_pipeline_file(root / "rating" / "main.py")
    graph.nodes = [node for node in graph.nodes if node.id == "source"]
    graph.edges = []
    for label, boundary in bands.items():
        graph.nodes.append(
            GraphNode(
                id=label, data=NodeData(label=label, nodeType="banding", config=_banding(boundary))
            )
        )
        graph.edges.append(GraphEdge(id=f"source-{label}", source="source", target=label))
    return SavePipelineRequest(
        graph=graph,
        name="main",
        description="",
        source_file="rating/main.py",
        base_revision=_revision(root) if base is None else base,
        operation=operation,
    )


def _service(root: Path) -> SavePipelineService:
    return SavePipelineService(project_root=root, pipeline_root=root / "rating", require_ready=True)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A versioned project whose pipeline, in its own folder, holds two banding nodes, Bands
    and Old, saved once: the save under test changes Bands and removes Old."""
    root = tmp_path / "project"
    root.mkdir()
    use_project(root)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.name", "Test User")
    _git(root, "config", "user.email", "test@example.com")
    (root / ".gitignore").write_text(".haute/\n", encoding="utf-8")
    (root / "haute.toml").write_text(
        '[project]\nname = "demo"\npipeline = "rating/main.py"\n', encoding="utf-8"
    )
    (root / "rating").mkdir()
    (root / "rating" / "main.py").write_text(PIPELINE, encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "Initial project")
    _git(root, "checkout", "-q", "-b", WORKING)
    set_working_branch(WORKING, root, cwd=root)
    _service(root).save(_request(root, {"Bands": "30", "Old": "50"}, "op-setup-0001"))
    return root


def _the_save(root: Path) -> SavePipelineRequest:
    return _request(root, {"Bands": "40"}, "op-under-test-0001")


OLD = "rating/config/banding/Old.json"


class _Steps:
    """Every change a save makes to the project's files, in turn: each atomic replace, each
    unlink, and the capture on the ledger. Before the change, the step either copies the
    project as it is then (what a server killed there leaves) or, at the chosen step, fails."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.count = 0
        self.snapshots: list[Path] = []
        self.snapshot_to: Path | None = None
        self.fail_at: int | None = None

    def before(self, path: Path) -> None:
        if not Path(path).resolve().is_relative_to(self.root):
            return
        self.count += 1
        if self.snapshot_to is not None:
            copy = self.snapshot_to / f"step-{self.count:02d}"
            shutil.copytree(self.root, copy, symlinks=True)
            self.snapshots.append(copy)
        if self.count == self.fail_at:
            raise OSError(errno.EIO, f"step {self.count} failed")


@contextmanager
def _watched(monkeypatch: pytest.MonkeyPatch, steps: _Steps) -> Iterator[None]:
    real_replace, real_unlink = Path.replace, Path.unlink
    real_capture = save_pipeline.capture_save_in_ledger

    def replace(self: Path, target: Any) -> Path:
        steps.before(Path(target))
        return real_replace(self, target)

    def unlink(self: Path, missing_ok: bool = False) -> None:
        steps.before(self)
        real_unlink(self, missing_ok=missing_ok)

    def capture(root: Path, *args: Any, **kwargs: Any) -> Any:
        steps.before(root)
        return real_capture(root, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "replace", replace)
        patch.setattr(Path, "unlink", unlink)
        patch.setattr(save_pipeline, "capture_save_in_ledger", capture)
        yield


def _staging_files(root: Path) -> list[Path]:
    return [path for path in (root / "rating").rglob("*.tmp")]


def _interruption(document: PipelineEditorDocument) -> list[str]:
    return [d.message for d in document.diagnostics if d.code == "save_interrupted"]


def _without_sha(answer: SavePipelineResponse) -> dict[str, Any]:
    return answer.model_dump(exclude={"git_sha"})


def _milestone_paths(root: Path) -> set[str]:
    milestone = commit_milestone("Milestone", root, pipeline_folder="rating", cwd=root)
    return set(_git(root, "ls-tree", "-r", "--name-only", milestone.sha).splitlines())


def test_a_server_killed_at_each_step_of_a_save_leaves_it_committed_or_reported(
    project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = _revision(project)
    request = _the_save(project)
    steps = _Steps(project)
    steps.snapshot_to = tmp_path / "killed"
    with _watched(monkeypatch, steps):
        answer = _service(project).save(request)
    assert answer.source_revision != before
    # The record and its commit mark, the files, the removal, the capture, the receipt and
    # the record's removal: a server can die before each.
    assert len(steps.snapshots) >= 8

    seen: set[str] = set()
    for snapshot in steps.snapshots:
        use_project(snapshot)
        settle_saves(snapshot)  # the server's start
        document = _document(snapshot)
        interrupted = _interruption(document)
        assert _staging_files(snapshot) == [], snapshot.name
        if document.source_revision == answer.source_revision and not interrupted:
            seen.add("committed")
            assert not (snapshot / OLD).exists(), snapshot.name
            repeat = _service(snapshot)
            assert _without_sha(repeat.save(request)) == _without_sha(answer)
            assert repeat.replayed, snapshot.name
        elif interrupted:
            seen.add("interrupted")
            assert document.load_status == "ready", snapshot.name
            assert "rating/main.py" in interrupted[0], snapshot.name
            # Saved as it loads, the files are one save again and the report goes; the node
            # the stopped save removed loses its settings file once the pipeline has no node
            # for it.
            graph = parse_pipeline_file(snapshot / "rating" / "main.py")
            bands = {node.id: "60" for node in graph.nodes if node.data.nodeType == "banding"}
            _service(snapshot).save(_request(snapshot, bands, f"op-after-{snapshot.name}"))
            assert _interruption(_document(snapshot)) == [], snapshot.name
            assert (snapshot / OLD).exists() == ("Old" in bands), snapshot.name
            seen.add("interrupted, its removal carried" if "Old" not in bands else "interrupted")
        else:
            seen.add("untouched")  # stopped before the record was written: nothing changed
            assert document.source_revision == before, snapshot.name
        assert not any(path.endswith(".tmp") for path in _milestone_paths(snapshot))
    assert seen == {"untouched", "interrupted", "interrupted, its removal carried", "committed"}


def test_a_step_of_a_save_that_fails_leaves_the_earlier_document_or_the_save_committed(
    project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = tmp_path / "base"
    shutil.copytree(project, base, symlinks=True)
    counting = _Steps(project)
    with _watched(monkeypatch, counting):
        answer = _service(project).save(_the_save(project))

    removal_failed: set[int] = set()
    for step in range(1, counting.count + 1):
        root = tmp_path / f"failed-{step:02d}"
        shutil.copytree(base, root, symlinks=True)
        use_project(root)
        before = _revision(root)
        failing = _Steps(root)
        failing.fail_at = step
        with _watched(monkeypatch, failing):
            try:
                saved: SavePipelineResponse | None = _service(root).save(_the_save(root))
            except OSError:
                saved = None
        if saved is not None and (root / OLD).exists():
            # The removal failed: the save answered as saved, naming the file.
            removal_failed.add(step)
            assert any(OLD in warning for warning in saved.warnings), step
        settle_saves(root)  # the next save's, or the server's start
        document = _document(root)
        assert _interruption(document) == [], step
        assert _staging_files(root) == [], step
        if document.source_revision == before:
            assert saved is None, step
            assert (root / OLD).is_file(), step
        else:
            assert document.source_revision == answer.source_revision, step
            assert not (root / OLD).exists(), step
    assert len(removal_failed) == 1


def test_a_stale_file_that_cannot_be_removed_leaves_the_save_saved_and_the_next_removes_it(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = TestClient(project_app(), raise_server_exceptions=False)
    published: list[dict[str, Any]] = []
    unsubscribe = default_bus.subscribe("pipeline.document.update", published.append)
    held = [True]
    real_unlink = Path.unlink

    def unlink(self: Path, missing_ok: bool = False) -> None:
        if held[0] and self.name == "Old.json":
            raise PermissionError(errno.EACCES, "The file is held by another program")
        real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", unlink)
    try:
        request = _the_save(project).model_dump(mode="json")
        saved = client.post("/api/pipeline/save", json=request)
        assert saved.status_code == 200, saved.text
        answer = saved.json()
        assert answer["source_revision"] == _revision(project)
        assert any(
            OLD in warning and "The next save removes it" in warning
            for warning in answer["warnings"]
        )
        assert (project / OLD).is_file()
        assert len(published) == 1
        # Committed: its repeat is answered with what it did.
        assert client.post("/api/pipeline/save", json=request).json() == answer

        held[0] = False
        again = _request(project, {"Bands": "45"}, "op-next-save-0001").model_dump(mode="json")
        next_save = client.post("/api/pipeline/save", json=again)
        assert next_save.status_code == 200, next_save.text
        assert not (project / OLD).exists()
        # Captured on the ledger since the save that could not remove it.
        captured = _git(
            project,
            "log",
            "--name-status",
            "--format=",
            f"{answer['git_sha']}..{next_save.json()['git_sha']}",
        )
        assert f"D\t{OLD}" in captured.splitlines()
        assert save_records.read_record(project, "rating/main.py") is None
    finally:
        unsubscribe()


def test_a_save_that_raised_after_it_committed_is_answered_from_its_receipt(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = TestClient(project_app(), raise_server_exceptions=False)
    real_capture = save_pipeline.capture_save_in_ledger
    calls = [0]

    def capture(*args: Any, **kwargs: Any) -> Any:
        calls[0] += 1
        if calls[0] == 1:
            raise RuntimeError("the capture broke")
        return real_capture(*args, **kwargs)

    monkeypatch.setattr(save_pipeline, "capture_save_in_ledger", capture)
    request = _the_save(project).model_dump(mode="json")

    failed = client.post("/api/pipeline/save", json=request)
    repeat = client.post("/api/pipeline/save", json=request)

    assert failed.status_code == 500
    assert repeat.status_code == 200, repeat.text
    assert repeat.json()["source_revision"] == _revision(project)
    assert repeat.json()["git_sha"] is not None
    assert not (project / OLD).exists()


def test_a_ready_document_reporting_an_interrupted_save_can_be_saved_and_the_report_goes(
    project: Path,
) -> None:
    record = save_records.SaveRecord(
        source_file="rating/main.py",
        writes=("rating/main.py", "rating/config/banding/Bands.json"),
    )
    save_records.write_record(project, record)
    document = _document(project)
    assert document.load_status == "ready"
    assert _interruption(document) == [
        "A save stopped part-way, so these files may each hold a different save: "
        "rating/main.py, rating/config/banding/Bands.json."
    ]
    assert document.diagnostics[0].severity == "warning"

    _service(project).save(_request(project, {"Bands": "35"}, "op-after-report-0001"))

    assert _interruption(_document(project)) == []
    assert save_records.read_record(project, "rating/main.py") is None


def test_receipts_beyond_the_limit_forget_the_oldest(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(save_records, "MAX_SAVE_RECEIPTS", 2)
    answer = SavePipelineResponse(
        file="rating/main.py", pipeline_name="main", source_revision="r", document_fingerprint="f"
    )
    folder = project / ".haute" / "saves" / "receipts"
    shutil.rmtree(folder)
    for age, key in enumerate(("op-one-0001", "op-two-0001", "op-three-0001")):
        save_records.keep_receipt(project, key, "digest", answer)
        _age_receipt(project, key, age)

    assert save_records.replay_receipt(project, "op-one-0001", "digest") is None
    assert save_records.replay_receipt(project, "op-three-0001", "digest") == answer
    with pytest.raises(save_records.SaveOperationReusedError):
        save_records.replay_receipt(project, "op-three-0001", "another digest")


def _age_receipt(root: Path, operation: str, age: int) -> None:
    """Date *operation*'s receipt *age* seconds after the epoch, so the order is certain."""
    path = save_records._receipt_path(root, operation)
    if path.exists():
        os.utime(path, ns=(age * 1_000_000_000, age * 1_000_000_000))


def test_a_rollback_that_fails_leaves_the_save_reported(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_write = save_pipeline.atomic_write_bytes

    def config_write_fails(self: SavePipelineService, config_files: Any, touched: Any) -> None:
        raise OSError(errno.EIO, "the configuration folder is full")

    def restore_fails(target: Path, payload: bytes) -> None:
        if target.name == "main.py":
            raise PermissionError(errno.EACCES, "main.py is held by another program")
        real_write(target, payload)

    monkeypatch.setattr(SavePipelineService, "_write_config_files", config_write_fails)
    monkeypatch.setattr(save_pipeline, "atomic_write_bytes", restore_fails)

    with pytest.raises(OSError, match="configuration folder is full"):
        _service(project).save(_the_save(project))

    # The new pipeline file stayed beside the old settings: the save is reported.
    assert _interruption(_document(project)) == [
        "A save stopped part-way, so these files may each hold a different save: "
        "rating/main.py, rating/config/banding/Bands.json, rating/main.haute.json."
    ]


def test_an_unreadable_record_is_reported_and_the_next_save_replaces_it(project: Path) -> None:
    path = save_records._record_path(project, "rating/main.py")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ not json", encoding="utf-8")
    settle_saves(project)

    assert _interruption(_document(project)) == [
        "A save stopped part-way, and its record cannot be read."
    ]

    _service(project).save(_request(project, {"Bands": "35"}, "op-after-unreadable-0001"))

    assert _interruption(_document(project)) == []
    assert not path.exists()


def test_an_unreadable_receipt_answers_nothing_and_the_repeat_is_saved_against_its_base(
    project: Path,
) -> None:
    request = _the_save(project)
    _service(project).save(request)
    receipt = save_records._receipt_path(project, "op-under-test-0001")
    receipt.write_text("{ not json", encoding="utf-8")

    from haute.routes._save_pipeline import StaleDocumentRevisionError

    with pytest.raises(StaleDocumentRevisionError):
        _service(project).save(request)
