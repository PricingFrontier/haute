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
import json
import os
import shutil
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
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


def _record(root: Path, source_file: str) -> save_records.SaveRecord | None:
    """The one record of a save of *source_file* still to settle, or None."""
    records = save_records.records_of(root, source_file)
    assert len(records) <= 1, records
    return records[0] if records else None


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
            # The removal reached version history, even when the server died after it and
            # before its capture.
            ledger = _git(snapshot, "ls-tree", "-r", "--name-only", f"{WORKING}-save")
            assert OLD not in ledger.splitlines(), snapshot.name
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
        assert _record(project, "rating/main.py") is None
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
    published: list[dict[str, Any]] = []
    unsubscribe = default_bus.subscribe("pipeline.document.update", published.append)
    try:
        failed = client.post("/api/pipeline/save", json=request)
        assert failed.status_code == 500
        assert published == []

        repeat = client.post("/api/pipeline/save", json=request)
    finally:
        unsubscribe()

    assert repeat.status_code == 200, repeat.text
    assert repeat.json()["source_revision"] == _revision(project)
    assert repeat.json()["git_sha"] is not None
    assert not (project / OLD).exists()
    # The save the repeat's settle finished is sent to every other canvas, once.
    assert [update["document_fingerprint"] for update in published] == [
        repeat.json()["document_fingerprint"]
    ]


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
    assert _record(project, "rating/main.py") is None


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
    name = save_records._record_path(project, "rating/main.py").name
    path = project / ".haute" / "saves" / "records" / name
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
    name = save_records._receipt_path(project, "op-under-test-0001").name
    receipt = project / ".haute" / "saves" / "receipts" / name
    receipt.write_text("{ not json", encoding="utf-8")

    from haute.routes._save_pipeline import StaleDocumentRevisionError

    with pytest.raises(StaleDocumentRevisionError):
        _service(project).save(request)


def _link(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:  # Windows withholds the symlink privilege by default (WinError 1314)
        pytest.skip("symlink creation is unavailable on this platform")


def test_a_record_naming_a_path_outside_the_project_removes_nothing_there(
    project: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "config.json").write_text("{}", encoding="utf-8")
    import hashlib

    digest = hashlib.sha256(b"{}").hexdigest()
    save_records.write_record(
        project,
        save_records.SaveRecord(
            source_file="rating/main.py",
            removes=(save_records.Removal("../outside/config.json", digest),),
            committed=True,
        ),
    )
    settle_saves(project)

    assert (outside / "config.json").is_file()
    # A record that names a path outside the project is not one a save wrote.
    assert _interruption(_document(project)) == [
        "A save stopped part-way, and its record cannot be read."
    ]


def test_a_removal_reached_through_a_link_out_of_the_project_is_left_alone(
    project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_unlink = Path.unlink

    def removal_held(self: Path, missing_ok: bool = False) -> None:
        if self.name == "Old.json":
            raise PermissionError(errno.EACCES, "held")
        real_unlink(self, missing_ok=missing_ok)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", removal_held)
        _service(project).save(_the_save(project))
    assert save_records.held_records(project, "rating/main.py") != []

    # The folder the removal names becomes a link to another folder holding the same bytes.
    outside = tmp_path / "outside-banding"
    shutil.copytree(project / "rating" / "config" / "banding", outside)
    shutil.rmtree(project / "rating" / "config" / "banding")
    _link(project / "rating" / "config" / "banding", outside)
    settle_saves(project)

    assert (outside / "Old.json").is_file()


def test_a_records_folder_reached_through_a_link_refuses_the_save(
    project: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "outside-saves"
    outside.mkdir()
    shutil.rmtree(project / ".haute" / "saves")
    _link(project / ".haute" / "saves", outside)
    before = (project / "rating" / "main.py").read_bytes()

    with pytest.raises(save_records.SaveRecordsUnavailableError, match="reached through a link"):
        _service(project).save(_the_save(project))

    assert list(outside.iterdir()) == []
    assert (project / "rating" / "main.py").read_bytes() == before


def test_a_staging_file_still_held_is_removed_once_free_and_no_commit_takes_it(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from haute._git_transactions import _pipeline_folder_new_files

    staged = project / "rating" / "config" / "banding" / "Bands.0123abcd.tmp"
    staged.write_text("{}", encoding="utf-8")
    save_records.write_record(
        project,
        save_records.SaveRecord(
            source_file="rating/main.py",
            writes=("rating/config/banding/Bands.json",),
        ),
    )
    held = [True]
    real_unlink = Path.unlink

    def staging_held(self: Path, missing_ok: bool = False) -> None:
        if held[0] and self.name == staged.name:
            raise PermissionError(errno.EACCES, "held by another program")
        real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", staging_held)
    settle_saves(project)
    _service(project).save(_request(project, {"Bands": "35", "Old": "50"}, "op-held-0001"))

    # The save landed, and the staging file it could not remove is still its to remove.
    assert staged.is_file()
    assert _interruption(_document(project)) == []
    assert "rating/config/banding/Bands.0123abcd.tmp" not in _pipeline_folder_new_files(
        "rating", cwd=project
    )

    # A save that removes the file beside it, and one after while the staging is still held:
    # each is captured, the file it no longer has among them.
    removed = _service(project).save(_request(project, {"Old": "55"}, "op-held-0002"))
    later = _service(project).save(_request(project, {"Old": "65"}, "op-held-0003"))
    assert not (project / "rating" / "config" / "banding" / "Bands.json").exists()
    for answer in (removed, later):
        assert answer.git_sha is not None and answer.uncaptured == [], answer.warnings
    assert staged.is_file()

    held[0] = False
    settle_saves(project)

    assert not staged.exists()
    assert _record(project, "rating/main.py") is None


def test_a_record_is_read_by_any_spelling_of_its_file(project: Path) -> None:
    save_records.write_record(
        project,
        save_records.SaveRecord(
            source_file="rating/main.py",
            writes=("rating/main.py",),
        ),
    )

    record = _record(project, "Rating/Main.py")

    assert record is not None and record.readable
    assert record.source_file == "rating/main.py"


def test_a_module_deletion_a_stopped_save_made_is_captured_by_the_next_save(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.test_submodel_persistence import _flat_graph, _managed_graph

    def service(root: Path) -> SavePipelineService:
        return SavePipelineService(project_root=root, pipeline_root=root, require_ready=True)

    def saving(root: Path, graph: Any, operation: str) -> SavePipelineRequest:
        return SavePipelineRequest(
            name="main",
            description="",
            graph=graph,
            preamble="",
            source_file="main.py",
            sources=["live"],
            active_source="live",
            preserved_blocks=[],
            base_revision=load_pipeline_editor_document(
                root / "main.py", project_root=root
            ).source_revision
            if (root / "main.py").exists()
            else None,
            operation=operation,
        )

    def ledger(root: Path) -> list[str]:
        return _git(root, "ls-tree", "-r", "--name-only", f"{WORKING}-save").splitlines()

    root = tmp_path / "flat"
    root.mkdir()
    use_project(root)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.name", "Test User")
    _git(root, "config", "user.email", "test@example.com")
    (root / ".gitignore").write_text(".haute/\n", encoding="utf-8")
    (root / "haute.toml").write_text('[project]\nname = "demo"\n', encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "Initial project")
    _git(root, "checkout", "-q", "-b", WORKING)
    set_working_branch(WORKING, root, cwd=root)
    service(root).save(saving(root, _flat_graph(), "op-flat-0001"))
    service(root).save(saving(root, _managed_graph(), "op-managed-0001"))
    module = ["modules/child.py", "modules/child.haute.json"]
    assert set(module) <= set(ledger(root))

    steps = _Steps(root)
    steps.snapshot_to = tmp_path / "killed"
    with _watched(monkeypatch, steps):
        service(root).save(saving(root, _flat_graph(), "op-dissolve-0001"))

    # Killed once the module and its sidecar were deleted, before the save committed.
    stopped = next(
        snapshot
        for snapshot in steps.snapshots
        if not any((snapshot / path).exists() for path in module)
        and (record := _record(snapshot, "main.py")) is not None
        and not record.committed
    )
    use_project(stopped)
    settle_saves(stopped)
    assert _interruption(load_pipeline_editor_document(stopped / "main.py", project_root=stopped))

    service(stopped).save(saving(stopped, _flat_graph(), "op-after-stop-0001"))

    assert not set(module) & set(ledger(stopped))


def test_a_file_a_stopped_save_never_created_does_not_stop_the_next_capture(
    project: Path,
) -> None:
    save_records.write_record(
        project,
        save_records.SaveRecord(
            source_file="rating/main.py",
            writes=("rating/main.py", "rating/config/banding/Never.json"),
        ),
    )

    answer = _service(project).save(_request(project, {"Bands": "35"}, "op-never-0001"))

    assert answer.git_sha is not None and answer.uncaptured == [], answer.warnings
    assert _interruption(_document(project)) == []


class _Stopped(BaseException):
    """A stop that is not an error, as a killed process makes: nothing catches it."""


def test_a_deletion_staged_before_the_capture_stopped_is_committed_by_the_settle(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import haute._git_transactions as git_transactions

    real_run_git = git_transactions._run_git

    def stopped_before_commit(*args: Any, **kwargs: Any) -> Any:
        if "commit" in args:
            raise _Stopped
        return real_run_git(*args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(git_transactions, "_run_git", stopped_before_commit)
        with pytest.raises(_Stopped):
            _service(project).save(_the_save(project))
    # The capture's `git add` staged the stale file's deletion; nothing was committed.
    assert not (project / OLD).exists()
    assert OLD in _git(project, "ls-tree", "-r", "--name-only", f"{WORKING}-save").splitlines()

    settle_saves(project)  # the server's start

    assert OLD not in _git(project, "ls-tree", "-r", "--name-only", f"{WORKING}-save").splitlines()
    assert _record(project, "rating/main.py") is None


def test_a_pipeline_file_keeps_its_newest_records_and_forgets_the_oldest_committed(
    project: Path,
) -> None:
    """Beyond the limit, the oldest committed records go (each a stale file left behind);
    one that never committed, the report of files that may hold two saves, stays."""
    stopped = save_records.SaveRecord(source_file="rating/main.py", writes=("rating/main.py",))
    save_records.write_record(project, stopped)
    committed = [
        save_records.SaveRecord(source_file="rating/main.py", committed=True)
        for _ in range(save_records.MAX_RECORDS_PER_FILE)
    ]
    for record in committed:
        save_records.write_record(project, record)

    kept = save_records.records_of(project, "rating/main.py")

    assert len(kept) == save_records.MAX_RECORDS_PER_FILE
    assert kept[0] == stopped
    assert committed[0] not in kept and kept[-1] == committed[-1]


def test_an_unreadable_record_beside_readable_ones_is_reported_and_they_are_kept(
    project: Path,
) -> None:
    kept = save_records.SaveRecord(source_file="rating/main.py", committed=True)
    save_records.write_record(project, kept)
    path = save_records._record_path(project, "rating/main.py")
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["saves"].append({"source_file": "rating/main.py", "writes": "not a list"})
    path.write_text(json.dumps(payload), encoding="utf-8")

    records = save_records.records_of(project, "rating/main.py")

    assert records[0] == kept
    assert not records[1].readable and records[1].writes == ()
    assert save_records.saved_records(project) == [kept]
    assert "A save stopped part-way" in _interruption(_document(project))[0]


def test_a_record_holding_a_file_outside_the_project_or_unreadable_is_not_the_checkout_s(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    here = save_records.file_digest(project / "rating" / "main.py")
    holding = save_records.SaveRecord(
        source_file="rating/main.py",
        holds=(save_records.Held("rating/main.py", (here,)),),
    )
    assert save_records.holds_here(project, holding)

    outside = replace(holding, holds=(save_records.Held("../outside.py", (None,)),))
    assert not save_records.holds_here(project, outside)

    def unreadable(_self: Path) -> bytes:
        raise PermissionError(errno.EACCES, "denied")

    monkeypatch.setattr(Path, "read_bytes", unreadable)
    assert not save_records.holds_here(project, holding)
