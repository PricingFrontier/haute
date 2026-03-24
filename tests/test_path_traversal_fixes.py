"""Tests for path traversal fixes in optimiser and submodel routes (S1, S2, S3).

S1: optimiser.py save_result — replaced str.startswith with validate_safe_path
S2: submodel.py get_submodel — added validate_safe_path for name parameter
S3: submodel.py dissolve_submodel — added validate_safe_path for source_file
S3b: submodel.py create_submodel — added validate_safe_path for source_file
S3c: submodel.py dissolve_submodel — sm_file traversal via validate_safe_path
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException


@pytest.fixture(autouse=True)
def _isolated_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run every test in a temporary directory."""
    monkeypatch.chdir(tmp_path)


@pytest.fixture()
def clean_job_store():
    """Snapshot and restore the optimiser job store after each test."""
    from haute.routes.optimiser import _store

    snapshot = dict(_store.jobs)
    yield _store
    _store.jobs.clear()
    _store.jobs.update(snapshot)


def _make_completed_job(tmp_path: Path) -> dict:
    """Create a fake completed optimiser job with a solve result."""
    return {
        "status": "completed",
        "solve_result": SimpleNamespace(
            lambdas={"lambda_1": 0.5},
            total_objective=100.0,
            total_constraints={"volume": 0.9},
            converged=True,
            baseline_objective=90.0,
            baseline_constraints={"volume": 0.85},
            dataframe=MagicMock(),
            iterations=10,
            cd_iterations=5,
        ),
        "solver": MagicMock(),
        "config": {},
        "node_label": "test_opt",
        "created_at": time.time(),
    }


def _graph_with_submodel() -> dict:
    """A graph dict that contains a submodel."""
    return {
        "nodes": [
            {
                "id": "load",
                "data": {"label": "load", "nodeType": "dataSource", "config": {"path": "d.csv"}},
            },
            {
                "id": "submodel__pricing",
                "type": "submodel",
                "data": {
                    "label": "pricing",
                    "nodeType": "submodel",
                    "config": {
                        "file": "modules/pricing.py",
                        "childNodeIds": ["base_rate"],
                        "inputPorts": [],
                        "outputPorts": [],
                    },
                },
            },
        ],
        "edges": [],
        "submodels": {
            "pricing": {
                "file": "modules/pricing.py",
                "childNodeIds": ["base_rate"],
                "inputPorts": [],
                "outputPorts": [],
                "graph": {
                    "nodes": [
                        {
                            "id": "base_rate",
                            "data": {
                                "label": "base_rate",
                                "nodeType": "polars",
                                "config": {"code": "return df"},
                            },
                        },
                    ],
                    "edges": [],
                    "pipeline_name": "pricing",
                },
            },
        },
    }


# =========================================================================
# S1: optimiser save_result — validate_safe_path replaces str.startswith
# =========================================================================


class TestOptimiserSavePathTraversal:
    """S1: The old str.startswith check was subtly broken for prefix attacks.

    If base = /tmp/pytest-xxx/project and the user sends
    output_path = "../project2/evil.json", the resolved path
    /tmp/pytest-xxx/project2/evil.json starts with /tmp/pytest-xxx/project
    but escapes the project root.  validate_safe_path (via is_relative_to)
    correctly catches this.
    """

    def test_valid_relative_path(self, client, clean_job_store, tmp_path):
        """A normal relative path within the project root should succeed."""
        from unittest.mock import patch

        from haute._sandbox import set_project_root

        set_project_root(tmp_path)
        job_id = "save_ok"
        clean_job_store.jobs[job_id] = _make_completed_job(tmp_path)

        with patch("haute.routes._helpers.pipeline_dir", return_value=tmp_path):
            resp = client.post(
                "/api/optimiser/save",
                json={"job_id": job_id, "output_path": "output/result.json"},
            )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        assert (tmp_path / "output" / "result.json").exists()

    def test_valid_nested_path(self, client, clean_job_store, tmp_path):
        """Deeply nested relative paths within the project root should succeed."""
        from unittest.mock import patch

        from haute._sandbox import set_project_root

        set_project_root(tmp_path)
        job_id = "save_nested"
        clean_job_store.jobs[job_id] = _make_completed_job(tmp_path)

        with patch("haute.routes._helpers.pipeline_dir", return_value=tmp_path):
            resp = client.post(
                "/api/optimiser/save",
                json={"job_id": job_id, "output_path": "a/b/c/result.json"},
            )
        assert resp.status_code == 200
        assert (tmp_path / "a" / "b" / "c" / "result.json").exists()

    def test_traversal_dotdot_blocked(self, client, clean_job_store, tmp_path):
        """../../../etc/passwd style traversal must be rejected."""
        from haute._sandbox import set_project_root

        set_project_root(tmp_path)
        job_id = "save_traversal"
        clean_job_store.jobs[job_id] = _make_completed_job(tmp_path)

        resp = client.post(
            "/api/optimiser/save",
            json={"job_id": job_id, "output_path": "../../etc/passwd"},
        )
        assert resp.status_code == 403
        assert "outside" in resp.json()["detail"].lower()

    def test_traversal_prefix_trick_blocked(self, client, clean_job_store, tmp_path):
        """The key bug: ../project2/evil.json passes str.startswith but not is_relative_to.

        If base = /tmp/.../test_xxx then ../test_xxx_evil/x.json resolves to
        /tmp/.../test_xxx_evil/x.json which starts with /tmp/.../test_xxx
        (the old broken check) but is NOT relative to it.
        """
        from haute._sandbox import set_project_root

        # Create a sibling directory whose name starts with the same prefix
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        sibling_dir = tmp_path / "project_evil"
        sibling_dir.mkdir()

        set_project_root(project_dir)
        job_id = "save_prefix"
        clean_job_store.jobs[job_id] = _make_completed_job(tmp_path)

        # This would have passed the old str.startswith check
        resp = client.post(
            "/api/optimiser/save",
            json={"job_id": job_id, "output_path": "../project_evil/stolen.json"},
        )
        assert resp.status_code == 403
        assert "outside" in resp.json()["detail"].lower()

        # Verify the file was NOT created
        assert not (sibling_dir / "stolen.json").exists()

    def test_absolute_path_outside_root_blocked(self, client, clean_job_store, tmp_path):
        """An absolute path that escapes the project root should be rejected."""
        from haute._sandbox import set_project_root

        set_project_root(tmp_path)
        job_id = "save_abs"
        clean_job_store.jobs[job_id] = _make_completed_job(tmp_path)

        resp = client.post(
            "/api/optimiser/save",
            json={"job_id": job_id, "output_path": "/tmp/evil.json"},
        )
        assert resp.status_code == 403

    def test_dotdot_in_middle_blocked(self, client, clean_job_store, tmp_path):
        """Paths like sub/../../../etc/passwd must be blocked."""
        from haute._sandbox import set_project_root

        set_project_root(tmp_path)
        job_id = "save_mid_traversal"
        clean_job_store.jobs[job_id] = _make_completed_job(tmp_path)

        resp = client.post(
            "/api/optimiser/save",
            json={"job_id": job_id, "output_path": "sub/../../../etc/passwd"},
        )
        assert resp.status_code == 403

    def test_dotdot_that_stays_within_root_allowed(self, client, clean_job_store, tmp_path):
        """A ../ that resolves back inside the root should be allowed."""
        from unittest.mock import patch

        from haute._sandbox import set_project_root

        set_project_root(tmp_path)
        job_id = "save_dotdot_ok"
        clean_job_store.jobs[job_id] = _make_completed_job(tmp_path)

        # sub/../result.json resolves to result.json — still within root
        with patch("haute.routes._helpers.pipeline_dir", return_value=tmp_path):
            resp = client.post(
                "/api/optimiser/save",
                json={"job_id": job_id, "output_path": "sub/../result.json"},
            )
        assert resp.status_code == 200
        assert (tmp_path / "result.json").exists()


# =========================================================================
# S2: submodel get_submodel — validate_safe_path for name parameter
# =========================================================================


class TestGetSubmodelPathTraversal:
    """S2: The name URL parameter was used unsanitised in path construction.

    GET /api/submodel/../../etc/passwd would resolve to an arbitrary file.
    """

    def test_valid_submodel_name(self, client, tmp_path):
        """A normal submodel name that exists should return 200."""
        modules_dir = tmp_path / "modules"
        modules_dir.mkdir()
        sm_file = modules_dir / "pricing.py"
        sm_file.write_text("""\
import polars as pl
import haute

submodel = haute.Submodel("pricing", description="Test submodel")

@submodel.data_source
def base_rate(df: pl.LazyFrame) -> pl.LazyFrame:
    return df
""")
        resp = client.get("/api/submodel/pricing")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_nonexistent_submodel_returns_404(self, client, tmp_path):
        """A name that doesn't exist should return 404, not 403."""
        (tmp_path / "modules").mkdir()
        resp = client.get("/api/submodel/nonexistent")
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()

    def test_traversal_to_etc_passwd_hits_spa_not_endpoint(self, client, tmp_path):
        """../../etc/passwd in the URL path gets routed away from the endpoint by FastAPI.

        URLs with slashes in the path don't match /{name} — they match other
        routes (e.g. the SPA catch-all).  This is a first layer of defense.
        The validate_safe_path in the endpoint is defense-in-depth.
        """
        (tmp_path / "modules").mkdir()
        resp = client.get("/api/submodel/../../etc/passwd")
        # Does NOT reach the get_submodel endpoint — routed elsewhere
        assert resp.status_code == 200  # SPA catch-all

    def test_traversal_dotdot_in_name_hits_spa(self, client, tmp_path):
        """Direct '..' in URL path also hits the SPA catch-all, not the endpoint."""
        (tmp_path / "modules").mkdir()
        resp = client.get("/api/submodel/..")
        assert resp.status_code == 200  # SPA catch-all

    def test_validate_safe_path_blocks_traversal_directly(self, tmp_path):
        """Defense-in-depth: if a name with '..' somehow reaches the endpoint,
        validate_safe_path blocks it at the function level."""
        from fastapi import HTTPException

        from haute.routes._helpers import validate_safe_path

        modules_dir = tmp_path / "modules"
        modules_dir.mkdir()

        # A name like '../../etc/passwd' — validate_safe_path catches it
        with pytest.raises(HTTPException) as exc_info:
            validate_safe_path(modules_dir, "../../etc/passwd.py")
        assert exc_info.value.status_code == 403

    def test_name_with_path_separator_hits_spa(self, client, tmp_path):
        """Names with encoded slashes also get routed away from the endpoint."""
        (tmp_path / "modules").mkdir()
        resp = client.get("/api/submodel/sub%2Fmodel")
        assert resp.status_code == 200  # SPA catch-all

    def test_valid_name_with_underscores(self, client, tmp_path):
        """Names with underscores and numbers are valid — should not be blocked."""
        modules_dir = tmp_path / "modules"
        modules_dir.mkdir()
        sm_file = modules_dir / "my_model_v2.py"
        sm_file.write_text("""\
import polars as pl
import haute

submodel = haute.Submodel("my_model_v2", description="Test")

@submodel.data_source
def step(df: pl.LazyFrame) -> pl.LazyFrame:
    return df
""")
        resp = client.get("/api/submodel/my_model_v2")
        assert resp.status_code == 200

    def test_name_with_dots_no_traversal(self, client, tmp_path):
        """A name like '..something' that doesn't actually escape should return 404."""
        (tmp_path / "modules").mkdir()
        resp = client.get("/api/submodel/..something")
        # '..something.py' resolves within modules/ — should be 404 (file not found)
        assert resp.status_code == 404


# =========================================================================
# S3: submodel dissolve_submodel — validate_safe_path for source_file
# =========================================================================


class TestDissolveSubmodelPathTraversal:
    """S3: dissolve_submodel resolved body.source_file without path validation.

    A crafted source_file like '../../etc/cron.d/evil' could write arbitrary
    code to the filesystem.
    """

    def test_valid_source_file(self, client, tmp_path):
        """A normal source_file within cwd should succeed."""
        from haute.graph_utils import PipelineGraph

        modules_dir = tmp_path / "modules"
        modules_dir.mkdir()
        sm_file = modules_dir / "pricing.py"
        sm_file.write_text("# submodel code\n")

        pipeline_file = tmp_path / "pipeline.py"
        pipeline_file.write_text("# main pipeline\n")

        flat_graph = PipelineGraph(pipeline_name="main")

        with patch("haute._flatten.flatten_graph", return_value=flat_graph):
            with patch("haute.codegen.graph_to_code", return_value="# code\n"):
                body = {
                    "submodel_name": "pricing",
                    "graph": _graph_with_submodel(),
                    "source_file": "pipeline.py",
                    "pipeline_name": "main",
                }
                resp = client.post("/api/submodel/dissolve", json=body)

        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_traversal_source_file_blocked(self, client, tmp_path):
        """source_file = '../../etc/cron.d/evil' must be rejected with 403."""
        from haute.graph_utils import PipelineGraph

        flat_graph = PipelineGraph(pipeline_name="main")

        with patch("haute._flatten.flatten_graph", return_value=flat_graph):
            body = {
                "submodel_name": "pricing",
                "graph": _graph_with_submodel(),
                "source_file": "../../etc/cron.d/evil",
                "pipeline_name": "main",
            }
            resp = client.post("/api/submodel/dissolve", json=body)

        assert resp.status_code == 403
        assert "outside" in resp.json()["detail"].lower()

    def test_absolute_source_file_blocked(self, client, tmp_path):
        """An absolute path as source_file must be rejected."""
        from haute.graph_utils import PipelineGraph

        flat_graph = PipelineGraph(pipeline_name="main")

        with patch("haute._flatten.flatten_graph", return_value=flat_graph):
            body = {
                "submodel_name": "pricing",
                "graph": _graph_with_submodel(),
                "source_file": "/etc/passwd",
                "pipeline_name": "main",
            }
            resp = client.post("/api/submodel/dissolve", json=body)

        assert resp.status_code == 403

    def test_source_file_prefix_trick_blocked(self, client, tmp_path):
        """The prefix trick: ../project_evil/pipeline.py must be rejected."""
        from haute.graph_utils import PipelineGraph

        project_dir = tmp_path
        sibling = project_dir.parent / (project_dir.name + "_evil")
        sibling.mkdir(exist_ok=True)

        flat_graph = PipelineGraph(pipeline_name="main")

        with patch("haute._flatten.flatten_graph", return_value=flat_graph):
            body = {
                "submodel_name": "pricing",
                "graph": _graph_with_submodel(),
                "source_file": f"../{project_dir.name}_evil/pipeline.py",
                "pipeline_name": "main",
            }
            resp = client.post("/api/submodel/dissolve", json=body)

        assert resp.status_code == 403

        # Verify the file was NOT created outside the project
        assert not (sibling / "pipeline.py").exists()

    def test_source_file_dotdot_within_root_allowed(self, client, tmp_path):
        """A ../ that resolves inside the root (e.g. sub/../pipeline.py) should be allowed."""
        from haute.graph_utils import PipelineGraph

        pipeline_file = tmp_path / "pipeline.py"
        pipeline_file.write_text("# main pipeline\n")

        modules_dir = tmp_path / "modules"
        modules_dir.mkdir()
        sm_file = modules_dir / "pricing.py"
        sm_file.write_text("# submodel code\n")

        flat_graph = PipelineGraph(pipeline_name="main")

        with patch("haute._flatten.flatten_graph", return_value=flat_graph):
            with patch("haute.codegen.graph_to_code", return_value="# code\n"):
                body = {
                    "submodel_name": "pricing",
                    "graph": _graph_with_submodel(),
                    "source_file": "sub/../pipeline.py",
                    "pipeline_name": "main",
                }
                resp = client.post("/api/submodel/dissolve", json=body)

        assert resp.status_code == 200

    def test_empty_source_file_returns_400(self, client, tmp_path):
        """An empty source_file should return 400 (existing validation)."""
        body = {
            "submodel_name": "pricing",
            "graph": _graph_with_submodel(),
            "source_file": "",
            "pipeline_name": "main",
        }
        resp = client.post("/api/submodel/dissolve", json=body)
        assert resp.status_code == 400
        assert "source_file" in resp.json()["detail"]


# =========================================================================
# validate_safe_path unit tests (for completeness)
# =========================================================================


class TestValidateSafePath:
    """Direct unit tests for the validate_safe_path helper."""

    def test_valid_relative(self, tmp_path):
        from haute.routes._helpers import validate_safe_path

        result = validate_safe_path(tmp_path, "subdir/file.txt")
        assert result == (tmp_path / "subdir" / "file.txt").resolve()

    def test_valid_just_filename(self, tmp_path):
        from haute.routes._helpers import validate_safe_path

        result = validate_safe_path(tmp_path, "file.txt")
        assert result == (tmp_path / "file.txt").resolve()

    def test_traversal_blocked(self, tmp_path):
        from fastapi import HTTPException

        from haute.routes._helpers import validate_safe_path

        with pytest.raises(HTTPException) as exc_info:
            validate_safe_path(tmp_path, "../../etc/passwd")
        assert exc_info.value.status_code == 403

    def test_prefix_trick_blocked(self, tmp_path):
        """The critical bug: sibling directories with shared prefix."""
        from fastapi import HTTPException

        from haute.routes._helpers import validate_safe_path

        base = tmp_path / "project"
        base.mkdir()

        with pytest.raises(HTTPException) as exc_info:
            validate_safe_path(base, "../project_evil/file.txt")
        assert exc_info.value.status_code == 403

    def test_absolute_path_outside_base_blocked(self, tmp_path):
        from fastapi import HTTPException

        from haute.routes._helpers import validate_safe_path

        with pytest.raises(HTTPException) as exc_info:
            validate_safe_path(tmp_path, "/etc/passwd")
        assert exc_info.value.status_code == 403

    def test_dotdot_within_base_allowed(self, tmp_path):
        from haute.routes._helpers import validate_safe_path

        # sub/../file.txt resolves to file.txt — still within base
        result = validate_safe_path(tmp_path, "sub/../file.txt")
        assert result == (tmp_path / "file.txt").resolve()

    def test_path_object_input(self, tmp_path):
        from haute.routes._helpers import validate_safe_path

        result = validate_safe_path(tmp_path, Path("subdir/file.txt"))
        assert result == (tmp_path / "subdir" / "file.txt").resolve()

    def test_base_itself_allowed(self, tmp_path):
        """Resolving '.' should give back the base itself."""
        from haute.routes._helpers import validate_safe_path

        result = validate_safe_path(tmp_path, ".")
        assert result == tmp_path.resolve()

    def test_null_byte_in_path_blocked(self, tmp_path):
        """Null bytes in path components must not bypass validation.

        On Linux, null bytes in filenames raise ValueError from Path.resolve().
        validate_safe_path should not let this propagate as a 500; the
        ValueError from pathlib is acceptable (caught before file I/O).
        """
        from haute.routes._helpers import validate_safe_path

        with pytest.raises((HTTPException, ValueError)):
            validate_safe_path(tmp_path, "file\x00.txt")

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="Creating symlinks requires admin privileges on Windows",
    )
    def test_symlink_escape_blocked(self, tmp_path):
        """A symlink pointing outside the base must be blocked.

        Even though the path is within the base before resolution,
        resolve() follows symlinks, so the resolved path escapes.
        """
        from fastapi import HTTPException

        from haute.routes._helpers import validate_safe_path

        # Create a symlink inside base that points outside
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("secret")

        inside = tmp_path / "project"
        inside.mkdir()
        (inside / "escape").symlink_to(outside)

        with pytest.raises(HTTPException) as exc_info:
            validate_safe_path(inside, "escape/secret.txt")
        assert exc_info.value.status_code == 403

    def test_double_encoded_dotdot_blocked(self, tmp_path):
        """Literal %2e%2e in a path segment is not traversal (it's a filename).

        validate_safe_path operates on already-decoded strings, so URL
        encoding is irrelevant.  But a literal '%2e%2e' filename should
        resolve safely within the base (it is NOT '..' after decode).
        """
        from haute.routes._helpers import validate_safe_path

        # '%2e%2e' is a literal filename, not '..'
        result = validate_safe_path(tmp_path, "%2e%2e/file.txt")
        assert result.is_relative_to(tmp_path)

    def test_very_long_path_handled(self, tmp_path):
        """Extremely long paths should not cause unexpected behavior."""
        from haute.routes._helpers import validate_safe_path

        long_segment = "a" * 200
        long_path = "/".join([long_segment] * 5) + "/file.txt"
        result = validate_safe_path(tmp_path, long_path)
        assert result.is_relative_to(tmp_path)


# =========================================================================
# S3b: create_submodel — validate_safe_path for source_file
# =========================================================================


class TestCreateSubmodelPathTraversal:
    """S3b: create_submodel used body.source_file without validate_safe_path.

    A crafted source_file could silently skip writing the pipeline file
    (old behavior) or should now be rejected with 403.
    """

    def _minimal_create_body(
        self,
        source_file: str = "pipeline.py",
    ) -> dict:
        """Build a minimal create_submodel request body."""
        return {
            "name": "pricing",
            "node_ids": ["step_a", "step_b"],
            "graph": {
                "nodes": [
                    {
                        "id": "step_a",
                        "data": {
                            "label": "step_a",
                            "nodeType": "polars",
                            "config": {"code": "return df"},
                        },
                    },
                    {
                        "id": "step_b",
                        "data": {
                            "label": "step_b",
                            "nodeType": "polars",
                            "config": {"code": "return df"},
                        },
                    },
                    {
                        "id": "step_c",
                        "data": {
                            "label": "step_c",
                            "nodeType": "polars",
                            "config": {"code": "return df"},
                        },
                    },
                ],
                "edges": [
                    {"id": "e1", "source": "step_a", "target": "step_b"},
                    {"id": "e2", "source": "step_b", "target": "step_c"},
                ],
            },
            "source_file": source_file,
            "pipeline_name": "main",
            "preamble": "import polars as pl",
        }

    def test_traversal_source_file_blocked(self, client, tmp_path):
        """source_file = '../../etc/evil.py' must be rejected with 403."""
        body = self._minimal_create_body(source_file="../../etc/evil.py")
        resp = client.post("/api/submodel/create", json=body)
        assert resp.status_code == 403
        assert "outside" in resp.json()["detail"].lower()

    def test_absolute_source_file_blocked(self, client, tmp_path):
        """An absolute source_file must be rejected with 403."""
        body = self._minimal_create_body(source_file="/etc/passwd")
        resp = client.post("/api/submodel/create", json=body)
        assert resp.status_code == 403

    def test_empty_source_file_returns_400(self, client, tmp_path):
        """An empty source_file should return 400."""
        body = self._minimal_create_body(source_file="")
        resp = client.post("/api/submodel/create", json=body)
        assert resp.status_code == 400
        assert "source_file" in resp.json()["detail"]


# =========================================================================
# S3c: dissolve_submodel — sm_file traversal in submodel metadata
# =========================================================================


class TestDissolveSmFileTraversal:
    """S3c: dissolve_submodel's sm_file comes from graph submodel metadata.

    If an attacker crafts a graph with submodels.pricing.file =
    '../../important_file.py', the dissolve endpoint should NOT delete
    that file.
    """

    def test_traversal_sm_file_does_not_delete_outside(self, client, tmp_path):
        """A crafted sm_file pointing outside cwd must not be deleted."""
        from haute.graph_utils import PipelineGraph

        # Create a file outside cwd that the attacker wants to delete
        outside_dir = tmp_path.parent / (tmp_path.name + "_victim")
        outside_dir.mkdir(exist_ok=True)
        victim = outside_dir / "important.py"
        victim.write_text("# important code\n")

        pipeline_file = tmp_path / "pipeline.py"
        pipeline_file.write_text("# main pipeline\n")

        # Craft a graph where the submodel file path points outside
        evil_graph = _graph_with_submodel()
        evil_graph["submodels"]["pricing"]["file"] = f"../{tmp_path.name}_victim/important.py"

        flat_graph = PipelineGraph(pipeline_name="main")

        with patch("haute._flatten.flatten_graph", return_value=flat_graph):
            with patch("haute.codegen.graph_to_code", return_value="# code\n"):
                body = {
                    "submodel_name": "pricing",
                    "graph": evil_graph,
                    "source_file": "pipeline.py",
                    "pipeline_name": "main",
                }
                resp = client.post("/api/submodel/dissolve", json=body)

        # The dissolve should succeed (main operation completed)
        assert resp.status_code == 200

        # But the file outside cwd must NOT have been deleted
        assert victim.exists(), "File outside project root was deleted!"
