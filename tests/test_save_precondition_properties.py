"""Property-based tests for pipeline save preconditions and revision invariants.

Editing/version-state family (ENG-T11): generated load/edit/save/external-write
sequences for two clients are driven through the real save and editor-document
routes in lockstep with a plain-Python model of base revisions, unsaved edits
and durable files. Stale writes never replace a newer accepted generation; a
test-only fault that omits the revision comparison is caught by the same
generator (the regression-sensitivity control for the ENG-T02 fix).
"""

from __future__ import annotations

import itertools
import os
from pathlib import Path
from typing import Any

import hypothesis
import pytest
from fastapi.testclient import TestClient
from hypothesis import example, given
from hypothesis import strategies as st

from haute.routes._helpers import invalidate_pipeline_index, pipeline_dir
from haute.routes._save_pipeline import SavePipelineService
from haute.server import app
from tests._property_budget import pr_budget

_case_counter = itertools.count(1)


# ---------------------------------------------------------------------------
# Helpers copied from tests/test_route_save_pipeline.py
# ---------------------------------------------------------------------------


def _snapshot_files(root: Path) -> dict[str, bytes]:
    """Capture every file under root excluding __pycache__."""
    files: dict[str, bytes] = {}
    for p in root.rglob("*"):
        if p.is_file() and "__pycache__" not in p.parts:
            files[p.relative_to(root).as_posix()] = p.read_bytes()
    return files


def _make_graph_payload(extra_node_label: str | None = None) -> dict[str, Any]:
    """Build a valid graph payload with an optional extra polars node."""
    nodes = [
        {
            "id": "base_node",
            "type": "pipelineNode",
            "position": {"x": 0, "y": 0},
            "data": {
                "label": "base_node",
                "nodeType": "polars",
                "config": {"code": ""},
            },
        }
    ]
    if extra_node_label is not None:
        nodes.append(
            {
                "id": f"node_{extra_node_label}",
                "type": "pipelineNode",
                "position": {"x": 100, "y": 0},
                "data": {
                    "label": extra_node_label,
                    "nodeType": "polars",
                    "config": {"code": ""},
                },
            }
        )
    return {"nodes": nodes, "edges": []}


# ---------------------------------------------------------------------------
# Reference Model (Plain Python, Spec Text)
# ---------------------------------------------------------------------------


class RevisionModel:
    """Plain-Python reference state model for the save precondition.

    disk: None (no file) or a generation token.
    clients A and B: base = None (never loaded) or a token.
    accepted: list of (client, label) generations.
    """

    def __init__(self) -> None:
        self.disk: int | None = None
        self.clients: dict[str, int | None] = {"A": None, "B": None}
        self.accepted: list[tuple[str, str]] = []
        self._token_counter: int = 0

    def save(self, client: str, label: str) -> bool:
        if self.clients[client] == self.disk:
            self._token_counter += 1
            self.disk = self._token_counter
            self.clients[client] = self._token_counter
            self.accepted.append((client, label))
            return True
        return False

    def external_edit(self) -> None:
        if self.disk is not None:
            self._token_counter += 1
            self.disk = self._token_counter

    def reload(self, client: str) -> None:
        self.clients[client] = self.disk


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_save_ops = st.tuples(
    st.just("save"),
    st.sampled_from(["A", "B"]),
    st.sampled_from(["b", "c", "d"]),
)
_ext_edit_ops = st.tuples(st.just("external_edit"))
_reload_ops = st.tuples(
    st.just("reload"),
    st.sampled_from(["A", "B"]),
)
_op_strategy = st.one_of(_save_ops, _ext_edit_ops, _reload_ops)
_seq_strategy = st.lists(_op_strategy, min_size=1, max_size=6)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pr_budget(25)
@example(ops=[("save", "A", "b"), ("save", "A", "b")])
@given(ops=_seq_strategy)
def test_saves_follow_the_revision_model_and_stale_writes_never_replace_accepted_generations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ops: list[tuple],
) -> None:
    case_dir = tmp_path / f"case_{next(_case_counter)}"
    case_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(case_dir)
    pipeline_dir.cache_clear()
    invalidate_pipeline_index()

    client = TestClient(app)
    pipeline_name = "seq_pipeline"
    source_file = f"{pipeline_name}.py"
    py_path = case_dir / source_file

    model = RevisionModel()
    actual_clients: dict[str, str | None] = {"A": None, "B": None}
    revision_by_content: dict[frozenset[tuple[str, bytes]], str] = {}
    stale_saves_after_last_accepted: list[tuple[str, str]] = []

    for op in ops:
        if op[0] == "save":
            _, client_id, label = op
            before = _snapshot_files(case_dir)
            model_accepted = model.save(client_id, label)
            resp = client.post(
                "/api/pipeline/save",
                json={
                    "name": pipeline_name,
                    "description": f"Save {label}",
                    "graph": _make_graph_payload(label),
                    "source_file": source_file,
                    "base_revision": actual_clients[client_id],
                },
            )
            expected_status = 200 if model_accepted else 409
            assert resp.status_code == expected_status
            if model_accepted:
                new_rev = resp.json()["source_revision"]
                assert new_rev is not None
                # Revisions are byte-true (specs/server-api, ENG-T02): the token
                # is a function of the durable artifacts, so a save that lands
                # on bytes seen earlier (an identical payload re-saved, or a
                # return to an earlier generation) reports that generation's
                # token, and new bytes always report a token never seen before.
                content = frozenset(_snapshot_files(case_dir).items())
                if content in revision_by_content:
                    assert new_rev == revision_by_content[content]
                else:
                    assert new_rev not in revision_by_content.values()
                    revision_by_content[content] = new_rev
                actual_clients[client_id] = new_rev
                on_disk = py_path.read_text(encoding="utf-8")
                assert f"def {label}(" in on_disk
                stale_saves_after_last_accepted.clear()
            else:
                assert resp.json()["detail"].startswith("stale_document_revision:")
                after = _snapshot_files(case_dir)
                assert after == before
                stale_saves_after_last_accepted.append((client_id, label))
        elif op[0] == "external_edit":
            model.external_edit()
            if py_path.is_file():
                py_path.write_text(
                    py_path.read_text(encoding="utf-8") + "\n# external edit\n",
                    encoding="utf-8",
                )
                invalidate_pipeline_index()
        elif op[0] == "reload":
            _, client_id = op
            model.reload(client_id)
            if py_path.is_file():
                resp = client.get(f"/api/pipeline/{pipeline_name}")
                assert resp.status_code == 200
                actual_clients[client_id] = resp.json()["source_revision"]
            else:
                actual_clients[client_id] = None

    if model.accepted:
        last_client, last_label = model.accepted[-1]
        on_disk = py_path.read_text(encoding="utf-8")
        assert f"def {last_label}(" in on_disk
        for _, rej_label in stale_saves_after_last_accepted:
            if rej_label != last_label:
                assert f"def {rej_label}(" not in on_disk

        # After an external edit a save by a client that has not reloaded
        # is rejected and a save after reload succeeds.
        py_path.write_text(
            py_path.read_text(encoding="utf-8") + "\n# external edit\n",
            encoding="utf-8",
        )
        invalidate_pipeline_index()
        stale_resp = client.post(
            "/api/pipeline/save",
            json={
                "name": pipeline_name,
                "description": "Unreloaded save",
                "graph": _make_graph_payload("b"),
                "source_file": source_file,
                "base_revision": actual_clients["A"],
            },
        )
        assert stale_resp.status_code == 409
        assert stale_resp.json()["detail"].startswith("stale_document_revision:")

        reload_resp = client.get(f"/api/pipeline/{pipeline_name}")
        assert reload_resp.status_code == 200
        actual_clients["A"] = reload_resp.json()["source_revision"]

        ok_resp = client.post(
            "/api/pipeline/save",
            json={
                "name": pipeline_name,
                "description": "Reloaded save",
                "graph": _make_graph_payload("b"),
                "source_file": source_file,
                "base_revision": actual_clients["A"],
            },
        )
        assert ok_resp.status_code == 200
    else:
        assert not py_path.is_file()


def test_omitted_revision_comparison_is_detected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    counter = itertools.count(1)

    def predicate(ops: list[tuple]) -> bool:
        case_dir = tmp_path / f"case_{next(counter)}"
        case_dir.mkdir(parents=True, exist_ok=True)
        orig_cwd = os.getcwd()
        try:
            os.chdir(case_dir)
            pipeline_dir.cache_clear()
            invalidate_pipeline_index()
            model = RevisionModel()
            actual_clients: dict[str, str | None] = {"A": None, "B": None}
            pipeline_name = "seq_pipeline"
            source_file = f"{pipeline_name}.py"
            py_path = case_dir / source_file

            with monkeypatch.context() as m:
                m.setattr(
                    SavePipelineService,
                    "_require_base_revision",
                    lambda self, py_path, base_revision: None,
                )
                client = TestClient(app)

                for op in ops:
                    if op[0] == "save":
                        _, c, label = op
                        last_label = model.accepted[-1][1] if model.accepted else None
                        model_accepted = model.save(c, label)
                        base_rev = actual_clients[c]
                        resp = client.post(
                            "/api/pipeline/save",
                            json={
                                "name": pipeline_name,
                                "description": f"Save {label}",
                                "graph": _make_graph_payload(label),
                                "source_file": source_file,
                                "base_revision": base_rev,
                            },
                        )
                        if not model_accepted:
                            # Stale write according to reference model
                            if resp.status_code == 200:
                                # Overwrite occurred under patched service
                                if py_path.is_file() and last_label is not None:
                                    content = py_path.read_text(encoding="utf-8")
                                    if f"def {last_label}(" not in content:
                                        return True
                        elif resp.status_code == 200:
                            actual_clients[c] = resp.json().get("source_revision")
                    elif op[0] == "external_edit":
                        model.external_edit()
                        if py_path.is_file():
                            py_path.write_text(
                                py_path.read_text(encoding="utf-8") + "\n# external edit\n",
                                encoding="utf-8",
                            )
                            invalidate_pipeline_index()
                    elif op[0] == "reload":
                        _, c = op
                        model.reload(c)
                        if py_path.is_file():
                            resp = client.get(f"/api/pipeline/{pipeline_name}")
                            if resp.status_code == 200:
                                actual_clients[c] = resp.json().get("source_revision")
            return False
        finally:
            os.chdir(orig_cwd)

    found = hypothesis.find(
        _seq_strategy,
        predicate,
        settings=pr_budget(20),
    )
    assert len(found) >= 2
    check_model = RevisionModel()
    has_stale = False
    for op in found:
        if op[0] == "save":
            if not check_model.save(op[1], op[2]):
                has_stale = True
                break
        elif op[0] == "external_edit":
            check_model.external_edit()
        elif op[0] == "reload":
            check_model.reload(op[1])
    assert has_stale


@pr_budget(20)
@given(
    non_null_base=st.text(
        alphabet="abcdefghijklmnopqrstuvwxyz0123456789_:-",
        min_size=1,
        max_size=32,
    ),
    label=st.sampled_from(["b", "c", "d"]),
)
def test_creation_requires_a_null_base_and_an_existing_file_requires_its_revision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    non_null_base: str,
    label: str,
) -> None:
    case_dir = tmp_path / f"case_{next(_case_counter)}"
    case_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(case_dir)
    pipeline_dir.cache_clear()
    invalidate_pipeline_index()

    client = TestClient(app)
    pipeline_name = "create_pipeline"
    source_file = f"{pipeline_name}.py"

    # Part 1: First request with non-null base against missing file
    snap_before_1 = _snapshot_files(case_dir)
    resp1 = client.post(
        "/api/pipeline/save",
        json={
            "name": pipeline_name,
            "description": "First save with non-null base",
            "graph": _make_graph_payload(label),
            "source_file": source_file,
            "base_revision": non_null_base,
        },
    )
    assert resp1.status_code == 409
    assert resp1.json()["detail"].startswith("stale_document_revision:")
    assert _snapshot_files(case_dir) == snap_before_1

    # Create the file legally with base_revision=None
    resp_create = client.post(
        "/api/pipeline/save",
        json={
            "name": pipeline_name,
            "description": "Initial create",
            "graph": _make_graph_payload(label),
            "source_file": source_file,
            "base_revision": None,
        },
    )
    assert resp_create.status_code == 200

    # Part 2: Request with null base against existing file
    snap_before_2 = _snapshot_files(case_dir)
    resp2 = client.post(
        "/api/pipeline/save",
        json={
            "name": pipeline_name,
            "description": "Save with null base against existing file",
            "graph": _make_graph_payload("b" if label != "b" else "c"),
            "source_file": source_file,
            "base_revision": None,
        },
    )
    assert resp2.status_code == 409
    assert resp2.json()["detail"].startswith("stale_document_revision:")
    assert _snapshot_files(case_dir) == snap_before_2
