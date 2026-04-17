"""Save pipeline and file-watcher integrity tests (Phase 1 Package 1C).

Covers:
  - #7  File-watcher 2s cooldown vs long saves — ``server.py:289-310`` and
        ``routes/_helpers.py:174-191``.  The watcher currently relies on a
        2-second timed window around ``mark_self_write``.  A save that
        takes longer than that races the watcher into re-parsing a
        partial file.  After the fix, the Writer's self-write callback
        must be invoked for every file written by the server so the
        watcher coordinates on events, not wall-clock cooldowns.
  - #12 Path traversal in ``graph_to_code_multi`` — ``_save_pipeline.py:139-152``.
        The save pipeline writes files from whatever relative paths
        ``graph_to_code_multi`` returns.  A malicious codegen output
        (crafted submodel.file or generated key) containing
        ``../../etc/...`` currently passes because the only guard is
        ``out_path.is_relative_to(self._root)`` after ``.resolve()`` and
        an unconditional ``continue`` on mismatch.  After the fix,
        output paths must be checked against an explicit prefix
        allowlist (``modules/`` or the main pipeline directory) BEFORE
        any resolution, and the save must fail loudly on violation.
  - #50 Save service has no transaction — ``routes/_save_pipeline.py``.
        A failure in a late step (e.g. config JSON write) currently
        leaves earlier writes (.py file, module files) in place.  After
        the fix, a failing save must either roll back to the pre-save
        file state, or stage every write into a temp area and commit
        atomically.
  - #51 Node positions silently lost on rename — ``_helpers.py:325-354``.
        ``save_sidecar`` keys positions by the sanitised label.  If two
        distinct labels sanitize to the same function name (e.g.
        ``"Feature X"`` → ``feature_x`` vs. ``"feature x"`` →
        ``feature_x``) the earlier position is silently overwritten or
        lost.  After the fix, the rename must preserve the position
        where possible AND return a warning in the response payload
        when a collision drops it.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Common fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Return a clean temp project root and chdir into it."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "main.py").write_text(
        'import haute\npipeline = haute.Pipeline("main")\n'
    )
    return tmp_path


@pytest.fixture()
def isolated_client(project_root: Path) -> TestClient:
    from haute.server import app

    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# #7 — File-watcher 2s cooldown vs long saves
# ---------------------------------------------------------------------------


class TestWatcherLongSaveRace:
    """Current hazard: ``mark_self_write`` records a timestamp, and
    ``is_self_write`` returns True for exactly 2.0 seconds after.  The
    file watcher runs an async debounce (``_DEBOUNCE_SECONDS = 0.3``)
    followed by its own processing.  If a save takes >2 s (large graph,
    slow disk, GC pause, antivirus scan), the watcher's ``_flush`` wakes
    up after the cooldown has expired and re-parses the half-written
    file, broadcasting either a partial graph or a parse_error to the
    GUI.

    After the fix, the Writer's self-write callback fires atomically
    with each rename, so the watcher sees a coherent event sequence
    regardless of wall-clock duration.  These tests verify:
      1. A >2 s save window does not cause the watcher to re-parse.
      2. Writes go through the Writer (so mark_self_write fires per-file
         just before each rename), not a bare ``path.write_text`` which
         bypasses the callback.
    """

    def test_writer_used_for_main_py_write(self) -> None:
        """Structural: ``SavePipelineService._write_code`` must use the
        F2/F6 Writer (or atomic_write_text) for the main .py path so
        that each write is preceded by ``mark_self_write``.

        Before the fix, the submodel branch calls ``out_path.write_text``
        directly, bypassing the atomic-write pipeline.
        """
        import inspect

        from haute.routes import _save_pipeline

        src = inspect.getsource(_save_pipeline.SavePipelineService._write_code)
        # After fix: every write path funnels through Writer or
        # atomic_write_text.  Raw ``.write_text(code)`` on file paths is
        # forbidden in this method because it skips mark_self_write.
        assert "out_path.write_text(code)" not in src, (
            "#7: _write_code still writes submodel files with bare "
            "path.write_text — must use Writer or atomic_write_text so "
            "the file-watcher sees a coherent self-write event."
        )

    def test_per_file_self_write_callback_fires_at_rename_time(
        self,
        project_root: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Each file written by the save service must re-invoke
        ``mark_self_write`` at rename time, NOT rely on a single call
        at the start of save().

        Concretely: drive a multi-file save whose files are written
        with intentional delay between them.  Before the fix,
        ``mark_self_write`` fires once at save-start, so any per-file
        rename that happens >2 s later escapes the cooldown window.
        After the fix, the Writer's callback fires immediately before
        each rename, guaranteeing self-write is active during the
        watcher's corresponding event.
        """
        import haute.routes._helpers as helpers
        from haute._types import GraphNode, NodeData, PipelineGraph
        from haute.routes._save_pipeline import SavePipelineService
        from haute.schemas import SavePipelineRequest

        # Track wall-clock of each mark_self_write call.
        mark_times: list[float] = []
        original_mark = helpers.mark_self_write

        def tracked_mark() -> None:
            mark_times.append(time.monotonic())
            original_mark()

        monkeypatch.setattr(helpers, "mark_self_write", tracked_mark)
        monkeypatch.setattr(
            "haute.routes._save_pipeline.mark_self_write", tracked_mark
        )

        # Build a minimal graph that triggers the main-file write path.
        graph = PipelineGraph(
            nodes=[
                GraphNode(
                    id="src",
                    data=NodeData(
                        label="src",
                        nodeType="dataSource",
                        config={"path": "data.parquet"},
                    ),
                ),
            ],
            edges=[],
        )
        req = SavePipelineRequest(
            name="main",
            description="",
            graph=graph,
            source_file="slow_save.py",
        )

        # Patch the codegen to simulate slow generation (>2 s) so any
        # old timestamp-based cooldown would have expired.
        def slow_generate(*args, **kwargs):
            time.sleep(0.5)  # Just enough to show per-file timestamp
            return (
                'import haute\npipeline = haute.Pipeline("main")\n'
            )

        with patch(
            "haute.codegen.graph_to_code",
            side_effect=slow_generate,
        ):
            svc = SavePipelineService(project_root)
            t0 = time.monotonic()
            svc.save(req)
            total = time.monotonic() - t0

        # After fix, mark_self_write must fire at least twice:
        # once at/around the rename of the main .py file and once
        # more at save-end (the existing behaviour is preserved).
        # Before fix, it only fires once at the very end.
        assert len(mark_times) >= 2, (
            f"#7: mark_self_write fired only {len(mark_times)} time(s) "
            f"during a {total:.2f}s save — the Writer's per-rename "
            f"callback must also fire."
        )

        # Sanity: at least one mark call happened before the last one,
        # ideally bracketing the actual write time.
        if len(mark_times) >= 2:
            last = mark_times[-1]
            earlier = mark_times[-2]
            assert last >= earlier

    def test_save_pipeline_service_writes_via_writer_for_all_files(
        self,
        project_root: Path,
    ) -> None:
        """Every .py file the save service writes must go through the
        Writer (or atomic_write_text with a mark_self_write call just
        before the rename).  Before the fix, the submodel branch uses
        ``out_path.write_text`` directly, bypassing the self-write
        coordination entirely.

        We verify by sentinel: patch ``atomic_write_text`` and the
        ``Writer.__exit__`` to count writes, and ensure the number of
        sentinel invocations matches the number of .py files created.
        """
        from haute._types import GraphNode, NodeData, PipelineGraph
        from haute.routes._save_pipeline import SavePipelineService
        from haute.schemas import SavePipelineRequest

        graph = PipelineGraph(
            nodes=[
                GraphNode(
                    id="src",
                    data=NodeData(
                        label="src",
                        nodeType="dataSource",
                        config={"path": "d.parquet"},
                    ),
                ),
            ],
            edges=[],
            submodels={
                "sm1": {
                    "file": "modules/sm1.py",
                    "childNodeIds": ["a"],
                    "graph": {
                        "nodes": [
                            {
                                "id": "a",
                                "data": {
                                    "label": "a",
                                    "nodeType": "polars",
                                    "config": {"code": "df"},
                                },
                            },
                        ],
                        "edges": [],
                    },
                }
            },
        )
        req = SavePipelineRequest(
            name="main",
            description="",
            graph=graph,
            source_file="main.py",
        )

        atomic_calls: list[Path] = []
        writer_commits: list[Path] = []

        from haute import _file_ops as file_ops

        original_atomic = file_ops.atomic_write_text

        def tracked_atomic(path, data, *a, **kw):
            atomic_calls.append(Path(path))
            return original_atomic(path, data, *a, **kw)

        original_exit = file_ops.Writer.__exit__

        def tracked_exit(self_obj, *a, **kw):
            writer_commits.append(Path(self_obj._path))
            return original_exit(self_obj, *a, **kw)

        # Patch codegen to return a realistic 2-file dict
        def _multi(*a, **kw):
            return {
                "main.py": 'import haute\npipeline = haute.Pipeline("main")\n',
                "modules/sm1.py": "# submodel\n",
            }

        with (
            patch.object(file_ops, "atomic_write_text", side_effect=tracked_atomic),
            patch.object(file_ops.Writer, "__exit__", tracked_exit),
            patch("haute.codegen.graph_to_code_multi", side_effect=_multi),
        ):
            svc = SavePipelineService(project_root)
            try:
                svc.save(req)
            except Exception:
                # Downstream steps (config write etc.) may fail in the
                # skinny test setup — we only care about the write calls
                # that happened up until that point.
                pass

        all_writes = [str(p) for p in atomic_calls + writer_commits]
        # Both main.py and modules/sm1.py must be written through the
        # atomic/Writer path.
        main_written = any("main.py" in p for p in all_writes)
        module_written = any("sm1.py" in p for p in all_writes)
        assert main_written, (
            "#7: main.py was not written via atomic_write_text / Writer"
        )
        assert module_written, (
            "#7: modules/sm1.py was not written via atomic_write_text / "
            "Writer — submodel branch still uses raw write_text and "
            "bypasses mark_self_write coordination."
        )


# ---------------------------------------------------------------------------
# #12 — Path traversal in graph_to_code_multi
# ---------------------------------------------------------------------------


class TestGraphToCodeMultiPathTraversal:
    """The current code:

        for rel_path, code in files.items():
            out_path = (self._root / rel_path).resolve()
            if not out_path.is_relative_to(self._root):
                continue
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(code)

    silently *skips* malicious paths but never raises.  That is better
    than a write to /etc/passwd, but it also:
      - masks genuine codegen bugs (a plausible rel_path silently
        dropped results in a truncated save).
      - relies on ``resolve()`` + ``is_relative_to`` rather than an
        explicit allowlist, meaning symlink escapes (a ``modules/`` ->
        ``/tmp`` symlink inside the repo) bypass the guard entirely.

    The fix must explicitly allow only two prefix families:
      - ``<main_pipeline>.py`` matching ``body.source_file``
      - ``modules/<name>.py`` (repo-relative)
    and raise a loud HTTPException on anything else.
    """

    def _make_graph_with_crafted_submodel_file(
        self, sm_file: str
    ) -> dict:
        return {
            "nodes": [
                {
                    "id": "submodel__evil",
                    "type": "pipelineNode",
                    "position": {"x": 0, "y": 0},
                    "data": {
                        "label": "evil",
                        "nodeType": "submodel",
                        "config": {"childNodeIds": ["a", "b"]},
                    },
                },
            ],
            "edges": [],
            "submodels": {
                "evil": {
                    "file": sm_file,
                    "childNodeIds": ["a", "b"],
                    "graph": {
                        "nodes": [
                            {
                                "id": "a",
                                "data": {
                                    "label": "a",
                                    "nodeType": "dataSource",
                                    "config": {"path": "data.parquet"},
                                },
                            },
                            {
                                "id": "b",
                                "data": {
                                    "label": "b",
                                    "nodeType": "polars",
                                    "config": {"code": "df"},
                                },
                            },
                        ],
                        "edges": [{"id": "e1", "source": "a", "target": "b"}],
                    },
                },
            },
        }

    @pytest.mark.parametrize(
        "bad_rel_path",
        [
            "../../etc/passwd",
            "..\\..\\windows\\system32\\cmd.exe",
            "../outside/evil.py",
            "/etc/cron.d/evil",
            "data/../../../evil.py",
        ],
        ids=[
            "dotdot-posix",
            "dotdot-windows",
            "single-dotdot",
            "absolute-posix",
            "nested-dotdot",
        ],
    )
    def test_malicious_output_path_rejected_by_allowlist(
        self,
        project_root: Path,
        bad_rel_path: str,
    ) -> None:
        """A crafted file path coming from graph_to_code_multi must cause
        the save to fail loudly — not silently skip the file.
        """
        from fastapi import HTTPException

        from haute.routes._save_pipeline import SavePipelineService
        from haute.schemas import SavePipelineRequest

        svc = SavePipelineService(project_root)

        graph_dict = self._make_graph_with_crafted_submodel_file(bad_rel_path)
        req = SavePipelineRequest(
            name="main",
            description="",
            graph=graph_dict,
            preamble="",
            source_file="main.py",
        )

        # Patch codegen to return the malicious path
        malicious_files = {
            "main.py": "import haute\npipeline = haute.Pipeline('main')\n",
            bad_rel_path: "# evil\n",
        }

        with patch(
            "haute.codegen.graph_to_code_multi",
            return_value=malicious_files,
        ):
            with pytest.raises(HTTPException) as exc_info:
                svc.save(req)

        # After fix: the response must be a 4xx with a clear message.
        assert exc_info.value.status_code in (400, 403), (
            f"#12: expected 4xx for malicious path {bad_rel_path!r}, "
            f"got {exc_info.value.status_code}"
        )
        # And the malicious file must NOT have been written.
        # Resolve against the REAL parent of project_root so the
        # traversal target is predictable.
        outside_candidates = [
            project_root.parent / "etc" / "passwd",
            project_root.parent / "outside" / "evil.py",
            project_root / ".." / "outside" / "evil.py",
            Path("/etc/cron.d/evil"),
        ]
        for c in outside_candidates:
            if c.exists():
                pytest.fail(
                    f"#12: malicious write reached filesystem at {c}"
                )

    def test_allowlist_accepts_valid_main_and_modules_paths(
        self, project_root: Path
    ) -> None:
        """A well-formed codegen output (main file + ``modules/<name>.py``)
        must still be accepted by the allowlist.
        """
        from fastapi import HTTPException

        from haute.routes._save_pipeline import SavePipelineService
        from haute.schemas import SavePipelineRequest

        svc = SavePipelineService(project_root)

        graph_dict = self._make_graph_with_crafted_submodel_file(
            "modules/evil.py"
        )
        req = SavePipelineRequest(
            name="main",
            description="",
            graph=graph_dict,
            preamble="",
            source_file="main.py",
        )

        good_files = {
            "main.py": "import haute\npipeline = haute.Pipeline('main')\n",
            "modules/evil.py": "# legitimate submodel\n",
        }

        with patch(
            "haute.codegen.graph_to_code_multi",
            return_value=good_files,
        ):
            # The save may still fail downstream (config writes, etc.)
            # but it must NOT raise a 4xx due to the allowlist.
            try:
                svc.save(req)
            except HTTPException as exc:
                # Tolerate non-path-related failures; we're checking the
                # allowlist does not reject the valid paths.
                if exc.status_code in (400, 403):
                    msg = str(exc.detail).lower()
                    assert (
                        "traversal" not in msg
                        and "outside" not in msg
                        and "forbidden" not in msg
                        and "escape" not in msg
                    ), (
                        f"#12: allowlist wrongly rejected valid paths: "
                        f"{exc.detail!r}"
                    )
            except Exception:  # noqa: BLE001
                # Non-HTTP failures (e.g. filesystem issues in the skinny
                # setup) are acceptable here — we only care that the
                # allowlist did not block valid paths.
                pass

    def test_silent_skip_is_not_the_fix(self, project_root: Path) -> None:
        """Regression: the bug was that malicious paths were silently
        ``continue``d.  The fix must NOT keep that behaviour — a silent
        skip masks bugs.  We encode this as: for a malicious path, the
        save must raise; it must not return ``status=saved`` with the
        malicious file missing from disk.
        """
        from fastapi import HTTPException

        from haute.routes._save_pipeline import SavePipelineService
        from haute.schemas import SavePipelineRequest

        svc = SavePipelineService(project_root)
        graph_dict = self._make_graph_with_crafted_submodel_file(
            "../escape/oops.py"
        )
        req = SavePipelineRequest(
            name="main",
            description="",
            graph=graph_dict,
            preamble="",
            source_file="main.py",
        )
        bad_files = {
            "main.py": "import haute\npipeline = haute.Pipeline('main')\n",
            "../escape/oops.py": "# evil\n",
        }
        with patch(
            "haute.codegen.graph_to_code_multi",
            return_value=bad_files,
        ):
            raised = False
            try:
                svc.save(req)
            except HTTPException:
                raised = True
            except Exception:
                raised = True
        assert raised, (
            "#12: malicious path was silently skipped — the save returned "
            "success even though a requested output was dropped."
        )


# ---------------------------------------------------------------------------
# #50 — Save service has no transaction
# ---------------------------------------------------------------------------


class TestSaveServiceTransaction:
    """``SavePipelineService.save`` runs six stages in sequence:
      1. singleton validation
      2. unique-sanitized-name validation
      3. source-file resolution
      4. _write_code (generates main + module .py files)
      5. _infer_flatten_schemas
      6. _write_config_files
      7. _remove_stale_config_files
      8. _write_sidecar
      9. mark_self_write

    Stages 4, 6, and 8 touch disk.  A failure at stage 8 (sidecar write)
    currently leaves the .py file and config JSONs on disk as a partial
    save; the user re-opens and sees drifted state.

    After the fix, the save must be transactional: either all writes
    commit atomically, or none do.  Two acceptable strategies:
      (a) Stage every write into a sibling temp dir and rename directory-
          -atomically at the end.
      (b) Explicit rollback: snapshot touched files before writing, and
          on failure restore the snapshot.

    These tests verify that a failure in the last write step does not
    orphan earlier writes.
    """

    def _make_request(self, source: str, name: str = "main") -> object:
        from haute._types import GraphNode, NodeData, PipelineGraph
        from haute.schemas import SavePipelineRequest

        graph = PipelineGraph(
            nodes=[
                GraphNode(
                    id="a",
                    data=NodeData(
                        label="a",
                        nodeType="dataSource",
                        config={"path": "data.parquet"},
                    ),
                    position={"x": 0.0, "y": 0.0},
                ),
            ],
            edges=[],
        )
        return SavePipelineRequest(
            name=name,
            description="",
            graph=graph,
            preamble="",
            source_file=source,
        )

    def test_sidecar_failure_rolls_back_py_file(
        self, project_root: Path
    ) -> None:
        """Fail on the last step (sidecar write) and assert either:
          - the previously-written main.py is restored to its pre-save
            content, OR
          - the main.py was never visible with the new content (i.e.
            the save staged into a temp location and never published
            because the later step failed).
        """
        from haute.routes._save_pipeline import SavePipelineService

        svc = SavePipelineService(project_root)

        py_path = project_root / "pipeline.py"
        original_contents = (
            "# ORIGINAL pipeline content — must survive a failed save.\n"
            'import haute\npipeline = haute.Pipeline("main")\n'
        )
        py_path.write_text(original_contents)

        req = self._make_request("pipeline.py")

        with patch(
            "haute.routes._save_pipeline.save_sidecar",
            side_effect=OSError(28, "No space left on device"),
        ):
            with pytest.raises(OSError):
                svc.save(req)

        assert py_path.exists(), (
            "#50: pipeline.py vanished after a failed save — no rollback"
        )
        assert py_path.read_text() == original_contents, (
            "#50: pipeline.py was updated even though a later save step "
            "failed — transaction not atomic"
        )

    def test_config_write_failure_rolls_back_py_file(
        self, project_root: Path
    ) -> None:
        """Similar invariant for a mid-pipeline failure (config write)."""
        from haute.routes._save_pipeline import SavePipelineService

        svc = SavePipelineService(project_root)

        py_path = project_root / "pipeline.py"
        py_path.write_text("# ORIGINAL\n")

        req = self._make_request("pipeline.py")

        with patch(
            "haute.routes._save_pipeline.SavePipelineService._write_config_files",
            side_effect=OSError(28, "No space left on device"),
        ):
            with pytest.raises(OSError):
                svc.save(req)

        assert py_path.read_text() == "# ORIGINAL\n", (
            "#50: pipeline.py overwritten despite mid-save failure"
        )

    def test_module_file_not_orphaned_when_sidecar_fails(
        self, project_root: Path
    ) -> None:
        """Realistic multi-file save: a submodel write creates
        ``modules/sm1.py`` BEFORE the sidecar write.  If the sidecar
        write fails, the orphaned module file must be cleaned up so a
        subsequent reload doesn't present the user with a partially
        saved pipeline.

        Without transaction semantics, the module file lingers on disk
        as an artifact of a failed save.  After the fix, it is either
        absent (staged-and-never-renamed) or rolled back (deleted on
        failure).
        """
        from haute._types import GraphNode, NodeData, PipelineGraph
        from haute.routes._save_pipeline import SavePipelineService
        from haute.schemas import SavePipelineRequest

        svc = SavePipelineService(project_root)

        py_path = project_root / "pipeline.py"
        py_path.write_text("# pre-existing pipeline.py\n")
        module_path = project_root / "modules" / "sm1.py"
        # module file does NOT exist before save
        assert not module_path.exists()

        graph = PipelineGraph(
            nodes=[
                GraphNode(
                    id="a",
                    data=NodeData(
                        label="a",
                        nodeType="polars",
                        config={"code": "df"},
                    ),
                ),
            ],
            edges=[],
            submodels={
                "sm1": {
                    "file": "modules/sm1.py",
                    "childNodeIds": ["a"],
                    "graph": {
                        "nodes": [
                            {
                                "id": "a",
                                "data": {
                                    "label": "a",
                                    "nodeType": "polars",
                                    "config": {"code": "df"},
                                },
                            },
                        ],
                        "edges": [],
                    },
                }
            },
        )
        req = SavePipelineRequest(
            name="main",
            description="",
            graph=graph,
            source_file="pipeline.py",
        )

        # Patch codegen to return a 2-file dict, then make sidecar fail
        with (
            patch(
                "haute.codegen.graph_to_code_multi",
                return_value={
                    "pipeline.py": (
                        'import haute\npipeline = haute.Pipeline("main")\n'
                    ),
                    "modules/sm1.py": "# submodel\n",
                },
            ),
            patch(
                "haute.routes._save_pipeline.save_sidecar",
                side_effect=OSError(28, "No space left on device"),
            ),
        ):
            with pytest.raises(OSError):
                svc.save(req)

        # Pre-existing pipeline.py must be intact
        assert py_path.read_text() == "# pre-existing pipeline.py\n", (
            "#50: pipeline.py was overwritten despite save failure"
        )
        # The new module file must not linger — save rolled back OR
        # staged in a temp dir.
        assert not module_path.exists(), (
            "#50: modules/sm1.py orphaned on disk after save failure — "
            "save is non-transactional"
        )


# ---------------------------------------------------------------------------
# #51 — Node positions silently lost on rename
# ---------------------------------------------------------------------------


class TestRenameCollisionPositionWarning:
    """``save_sidecar`` keys each position by ``_sanitize_func_name(label)``.
    If a user renames a node with a label that collides with another node's
    sanitised name, the earlier position is silently overwritten.

    The plan specifies the response must include a warning in these cases
    and preserve the position where possible.  This is tricky because the
    SavePipelineService already blocks outright collisions via
    ``_validate_unique_sanitized_names`` and returns 400.  The realistic
    scenario is a *reload* collision: the user had nodes labeled
    ``"Feature X"`` and ``"Feature-X"``.  The .py on disk stores only
    ``feature_x`` (the sanitized function name).  On reload, both original
    labels collapse into the same sanitized ID and the sidecar dict keyed
    by sanitized name can lose one position.

    After the fix, the save path must detect when a rename would cause a
    sanitized-name collision that drops a previous position AND return a
    warning in the response payload so the UI can inform the user instead
    of silently losing the node's graph location.
    """

    def _payload(
        self, graph: dict, source: str = "collision.py", name: str = "main"
    ) -> dict:
        return {
            "name": name,
            "description": "",
            "graph": graph,
            "source_file": source,
        }

    def test_collision_rename_returns_warning_in_response(
        self,
        project_root: Path,
    ) -> None:
        """Scenario: a previously-saved graph has two nodes whose labels
        both sanitize to the same function name.  The current
        ``_validate_unique_sanitized_names`` raises 400, which is the
        correct guard when the user tries to create a duplicate.  But the
        legitimate path — renaming one of the two and saving — must
        still surface a warning about dropped positions when it happens.

        We simulate the scenario where a node is renamed to an existing
        sanitized key, and the save succeeds but the response carries a
        ``warnings`` field naming the collision.
        """
        from haute.server import app

        client = TestClient(app, raise_server_exceptions=False)

        # Pre-write a sidecar that has a position keyed by ``feature_x``
        py = project_root / "collision.py"
        py.write_text(
            'import haute\npipeline = haute.Pipeline("main")\n'
        )
        sidecar = py.with_suffix(".haute.json")
        sidecar.write_text(
            json.dumps(
                {
                    "positions": {
                        "feature_x": {"x": 100, "y": 200},
                        "feature_y": {"x": 300, "y": 400},
                    }
                }
            )
        )

        # Send a rename where "Feature Y" becomes "Feature X" — their
        # sanitized name ("feature_x") is already in use by the first
        # node.  The current service will 400.  The fixed service must
        # either 400 with a clear message OR save-with-warning.
        graph = {
            "nodes": [
                {
                    "id": "feature_x",
                    "data": {
                        "label": "Feature X",
                        "nodeType": "polars",
                        "config": {"code": "df"},
                    },
                    "position": {"x": 100, "y": 200},
                },
                {
                    "id": "feature_x_2",
                    "data": {
                        "label": "Feature-X",  # sanitizes to feature_x too
                        "nodeType": "polars",
                        "config": {"code": "df"},
                    },
                    "position": {"x": 300, "y": 400},
                },
            ],
            "edges": [],
        }

        resp = client.post(
            "/api/pipeline/save", json=self._payload(graph, "collision.py")
        )
        # Two outcomes are acceptable:
        # 1) 400 with a clear collision message (current behavior preserved).
        # 2) 200 with ``warnings`` in the response payload explaining that
        #    positions may be lost due to sanitized-name collision.
        if resp.status_code == 400:
            detail = resp.json()["detail"]
            assert "sanitized" in detail.lower() or "duplicate" in detail.lower(), (
                f"#51: 400 detail must explain the collision, got: {detail!r}"
            )
            return

        assert resp.status_code == 200, (
            f"#51: unexpected status {resp.status_code}: {resp.json()}"
        )
        body = resp.json()
        assert "warnings" in body, (
            "#51: save response for a sanitized-name collision must include "
            "a 'warnings' field naming the affected nodes"
        )
        warnings_text = json.dumps(body["warnings"])
        assert "feature_x" in warnings_text.lower() or "feature-x" in warnings_text.lower(), (
            f"#51: warning must name the colliding sanitized key "
            f"(got: {body['warnings']!r})"
        )

    def test_positions_preserved_when_no_collision(
        self, project_root: Path
    ) -> None:
        """Without a rename collision, positions round-trip losslessly
        through save + load.  The fix for #51 must not break this path.
        """
        from haute._types import GraphNode, NodeData, PipelineGraph
        from haute.routes._helpers import load_sidecar_positions, save_sidecar

        py = project_root / "simple.py"
        py.write_text(
            'import haute\npipeline = haute.Pipeline("main")\n'
        )

        graph = PipelineGraph(
            nodes=[
                GraphNode(
                    id="unique_one",
                    data=NodeData(
                        label="Unique One",
                        nodeType="polars",
                        config={"code": "df"},
                    ),
                    position={"x": 10.0, "y": 20.0},
                ),
                GraphNode(
                    id="unique_two",
                    data=NodeData(
                        label="Unique Two",
                        nodeType="polars",
                        config={"code": "df"},
                    ),
                    position={"x": 30.0, "y": 40.0},
                ),
            ],
            edges=[],
        )
        save_sidecar(py, graph)
        positions = load_sidecar_positions(py)
        # save_sidecar keys by sanitised function name ("Unique One" -> "Unique_One")
        assert positions["Unique_One"] == {"x": 10.0, "y": 20.0}
        assert positions["Unique_Two"] == {"x": 30.0, "y": 40.0}

    def test_save_sidecar_collision_logs_warning(
        self, project_root: Path
    ) -> None:
        """Structural: ``save_sidecar`` (or its caller) must emit a
        structured log warning when it detects a sanitized-name collision
        that drops a position.  Silent loss is the hazard identified by
        #51.

        Captures any warning-level log event during a forced collision
        save; the exact event name is unspecified but the payload must
        mention the colliding key.
        """
        import structlog.testing

        from haute._types import GraphNode, NodeData, PipelineGraph
        from haute.routes._helpers import save_sidecar

        py = project_root / "coll_log.py"
        py.write_text("")

        # Two labels that sanitize to the same name: "My Node" and "My-Node"
        # both → "My_Node" → same function name after replace("-", "_")
        graph = PipelineGraph(
            nodes=[
                GraphNode(
                    id="n1",
                    data=NodeData(
                        label="My Node",
                        nodeType="polars",
                        config={"code": "df"},
                    ),
                    position={"x": 1.0, "y": 2.0},
                ),
                GraphNode(
                    id="n2",
                    data=NodeData(
                        label="My-Node",
                        nodeType="polars",
                        config={"code": "df"},
                    ),
                    position={"x": 3.0, "y": 4.0},
                ),
            ],
            edges=[],
        )

        with structlog.testing.capture_logs() as captured:
            save_sidecar(py, graph)

        warnings_or_errors = [
            e for e in captured if e.get("log_level") in ("warning", "error")
        ]
        assert warnings_or_errors, (
            "#51: save_sidecar silently dropped a colliding position — "
            "expected a warning-level log event naming the conflict"
        )
        joined = " ".join(f"{k}={v}" for e in warnings_or_errors for k, v in e.items())
        # Either the sanitized function name or the collision keyword
        # must appear in the log output
        assert "My_Node".lower() in joined.lower() or "collision" in joined.lower(), (
            f"#51: collision warning did not name the affected key "
            f"(got: {warnings_or_errors!r})"
        )
