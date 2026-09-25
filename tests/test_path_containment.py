"""SBX-R01: one path-containment check serves every caller.

``haute._sandbox.contained_path`` is the only containment comparison the
former callers use. For each of them (the routes that used the removed route
helper, the project-path check before deserialising, recovery artifact paths,
the save service's codegen output paths, the SQLite locator check and the
MLflow settings write target) a ``..`` escape
and a symlink escape are refused and an in-project path is accepted.
"""

from __future__ import annotations

import pickle
import time
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from haute._sandbox import contained_path, validate_project_path
from haute.errors import InvalidPathError, PathOutsideProjectError
from tests.job_store_support import seed_job


def _require_links(project: Path) -> None:
    # Windows withholds the symlink privilege by default (WinError 1314); Linux
    # CI runs these cases.
    if not (project / "link_dir").is_symlink():
        pytest.skip("symlink creation is unavailable on this platform")


@pytest.fixture
def layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """A project beside an outside directory, with links out of the project
    where the platform can create them (``link_dir`` and ``utility/link.py``)."""
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    (project / "data").mkdir(parents=True)
    (project / "utility").mkdir()
    outside.mkdir()
    (project / "data" / "rows.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    (project / "data" / "doc.json").write_text('{"k": 1}', encoding="utf-8")
    (project / "data" / "model.pkl").write_bytes(pickle.dumps({"ok": True}))
    (outside / "rows.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    (outside / "doc.json").write_text('{"k": 1}', encoding="utf-8")
    (outside / "model.pkl").write_bytes(pickle.dumps({"ok": True}))
    (project / "utility" / "helpers.py").write_text("X = 1\n", encoding="utf-8")
    (outside / "evil.py").write_text("X = 2\n", encoding="utf-8")
    try:
        (project / "link_dir").symlink_to(outside, target_is_directory=True)
        (project / "utility" / "link.py").symlink_to(outside / "evil.py")
    except OSError:
        pass  # the symlink cases skip themselves
    monkeypatch.chdir(project)
    monkeypatch.setattr("haute._sandbox._PROJECT_ROOT", project.resolve())
    monkeypatch.setattr("haute.routes.utility.pipeline_dir", lambda: project)
    monkeypatch.setattr("haute.routes._helpers.pipeline_dir", lambda: project)
    monkeypatch.setattr("haute.routes.pipeline.pipeline_dir", lambda: project)
    return project, outside


@pytest.fixture
def client() -> TestClient:
    from haute.server import app

    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# The check itself
# ---------------------------------------------------------------------------


class TestContainedPath:
    def test_an_inside_path_resolves(self, layout: tuple[Path, Path]) -> None:
        project, _ = layout
        assert contained_path(project, "data/rows.csv") == (project / "data/rows.csv").resolve()

    def test_a_dotdot_escape_is_refused(self, layout: tuple[Path, Path]) -> None:
        project, _ = layout
        with pytest.raises(PathOutsideProjectError, match="outside the project root"):
            contained_path(project, "../outside/rows.csv")

    def test_a_symlink_escape_is_refused(self, layout: tuple[Path, Path]) -> None:
        project, _ = layout
        _require_links(project)
        with pytest.raises(PathOutsideProjectError):
            contained_path(project, "link_dir/rows.csv")

    def test_a_nul_byte_is_an_invalid_path(self, layout: tuple[Path, Path]) -> None:
        project, _ = layout
        with pytest.raises(InvalidPathError):
            contained_path(project, "data/rows.csv\x00")

    def test_an_absolute_input_outside_the_root_is_refused_before_resolving(
        self, layout: tuple[Path, Path]
    ) -> None:
        """Even one that would resolve back inside: the lexical guard stays."""
        project, outside = layout
        back_inside = outside / ".." / "project" / "data" / "rows.csv"
        assert back_inside.resolve() == (project / "data/rows.csv").resolve()
        with pytest.raises(PathOutsideProjectError):
            contained_path(project, str(back_inside))

    def test_an_absolute_input_inside_the_root_resolves(self, layout: tuple[Path, Path]) -> None:
        project, _ = layout
        inside = project.resolve() / "data" / "rows.csv"
        assert contained_path(project, str(inside)) == inside


# ---------------------------------------------------------------------------
# Routes that used the removed route helper
# ---------------------------------------------------------------------------


def _optimiser_job() -> dict:
    return {
        "status": "completed",
        # Save publishes from the completion summary.
        "result": {
            "lambdas": {"lambda_1": 0.5},
            "total_objective": 100.0,
            "constraints": {"volume": 0.9},
            "converged": True,
            "baseline_objective": 90.0,
            "baseline_constraints": {"volume": 0.85},
            "iterations": 10,
        },
        "config": {},
        "node_label": "test_opt",
        "created_at": time.time(),
        "completed_at": time.time(),
    }


def _request(client: TestClient, route: str, value: str):  # noqa: ANN202
    if route == "browse":
        return client.get("/api/files", params={"dir": value})
    if route == "schema":
        return client.get("/api/schema", params={"path": value})
    if route == "read-json":
        return client.post("/api/pipeline/read-json", json={"path": value})
    if route == "recovery-preview":
        return client.post(
            "/api/pipeline/recovery-preview",
            json={"source_file": value, "source_revision": "r1", "target_recovery_id": "node"},
        )
    if route == "save":
        return client.post(
            "/api/pipeline/save",
            json={"name": "main", "source_file": value, "base_revision": None},
        )
    if route == "submodel":
        return client.get("/api/submodel/pricing", params={"source_file": value})
    if route == "optimiser-save":
        return client.post(
            "/api/optimiser/save", json={"job_id": "containment", "output_path": value}
        )
    raise AssertionError(route)


# route, in-project value and its expected status, dotdot value, symlink value
_ROUTES = [
    ("browse", "data", 200, "../outside", "link_dir"),
    ("schema", "data/rows.csv", 200, "../outside/rows.csv", "link_dir/rows.csv"),
    ("read-json", "data/doc.json", 200, "../outside/doc.json", "link_dir/doc.json"),
    # An in-project source that does not exist gets past containment to its 404.
    ("recovery-preview", "missing.py", 404, "../outside/main.py", "link_dir/main.py"),
    ("save", "main.py", 200, "../outside/main.py", "link_dir/main.py"),
    ("submodel", "missing.py", 404, "../outside/main.py", "link_dir/main.py"),
    ("optimiser-save", "output/result.json", 200, "../outside/result.json", "link_dir/r.json"),
]


_CASES = [
    pytest.param(route, kind, id=f"{route[0]}-{kind}")
    for route in _ROUTES
    for kind in ("inside", "dotdot", "symlink")
]


@pytest.mark.parametrize(("route", "kind"), _CASES)
def test_every_route_refuses_escapes_and_accepts_an_inside_path(
    layout: tuple[Path, Path],
    client: TestClient,
    clean_job_store,  # noqa: ANN001
    route: tuple[str, str, int, str, str],
    kind: str,
) -> None:
    name, inside, inside_status, dotdot, symlink = route
    project, outside = layout
    if kind == "symlink":
        _require_links(project)
    if name == "optimiser-save":
        seed_job(clean_job_store, "containment", _optimiser_job())
    before = sorted(path.name for path in outside.iterdir())

    if kind == "inside":
        response = _request(client, name, inside)
        assert response.status_code == inside_status, response.text
    else:
        response = _request(client, name, dotdot if kind == "dotdot" else symlink)
        assert response.status_code == 403, response.text
        assert response.json()["detail"] == "Cannot access paths outside the project root"
    assert sorted(path.name for path in outside.iterdir()) == before


class TestUtilityRoutes:
    """Module names are validated first, so a ``..`` is refused as a bad name
    (400); a symlinked module file escapes and is refused by containment."""

    def test_inside_and_dotdot(self, layout: tuple[Path, Path], client: TestClient) -> None:
        assert client.get("/api/utility/helpers").status_code == 200
        assert client.get("/api/utility/..%2Foutside").status_code in (400, 404)
        assert client.put("/api/utility/helpers", json={"content": "X = 3\n"}).status_code == 200
        assert client.post("/api/utility", json={"name": "fresh"}).status_code == 200
        assert client.post("/api/utility", json={"name": "../evil"}).status_code == 400

    def test_a_symlinked_module_is_refused_and_the_outside_file_left_alone(
        self, layout: tuple[Path, Path], client: TestClient
    ) -> None:
        project, outside = layout
        _require_links(project)
        assert client.get("/api/utility/link").status_code == 403
        assert client.put("/api/utility/link", json={"content": "X = 4\n"}).status_code == 403
        assert client.delete("/api/utility/link").status_code == 403
        assert client.post("/api/utility", json={"name": "link"}).status_code == 403
        assert (outside / "evil.py").read_text(encoding="utf-8") == "X = 2\n"


class TestSaveOutputPaths:
    """The save service's codegen output paths go through the check and keep
    their own 400 contract (generated paths are not request input)."""

    @staticmethod
    def _service(project: Path):  # noqa: ANN205
        from haute.routes._save_pipeline import SavePipelineService

        return SavePipelineService(project_root=project, pipeline_root=project)

    def test_ordinary_outputs_are_accepted(self, layout: tuple[Path, Path]) -> None:
        project, _ = layout
        (project / "modules").mkdir()
        service = self._service(project)
        assert service._validate_output_rel_path("main.py", "main.py") == (
            project.resolve() / "main.py"
        )
        assert service._validate_output_rel_path("modules/pricing.py", "main.py") == (
            project.resolve() / "modules" / "pricing.py"
        )

    def test_a_dotdot_output_is_refused(self, layout: tuple[Path, Path]) -> None:
        project, _ = layout
        with pytest.raises(HTTPException) as exc_info:
            self._service(project)._validate_output_rel_path("../outside/main.py", "main.py")
        assert exc_info.value.status_code == 400

    def test_a_modules_link_out_of_the_project_is_refused(self, layout: tuple[Path, Path]) -> None:
        project, outside = layout
        _require_links(project)
        (project / "modules").symlink_to(outside, target_is_directory=True)
        before = sorted(path.name for path in outside.iterdir())

        with pytest.raises(HTTPException) as exc_info:
            self._service(project)._validate_output_rel_path("modules/pricing.py", "main.py")

        assert exc_info.value.status_code == 400
        assert sorted(path.name for path in outside.iterdir()) == before

    def test_a_main_file_link_out_of_the_project_is_refused(
        self, layout: tuple[Path, Path]
    ) -> None:
        project, outside = layout
        _require_links(project)
        (project / "main.py").symlink_to(outside / "evil.py")

        with pytest.raises(HTTPException) as exc_info:
            self._service(project)._validate_output_rel_path("main.py", "main.py")

        assert exc_info.value.status_code == 400
        assert exc_info.value.detail == "Codegen output path resolves outside the project root."
        assert (outside / "evil.py").read_text(encoding="utf-8") == "X = 2\n"


def test_a_nul_byte_in_a_request_path_is_a_400(
    layout: tuple[Path, Path], client: TestClient
) -> None:
    response = client.post("/api/pipeline/read-json", json={"path": "data/doc.json\x00"})
    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid path"


# ---------------------------------------------------------------------------
# Callers outside the routes
# ---------------------------------------------------------------------------


class TestProjectPathBeforeDeserialising:
    def test_inside_loads(self, layout: tuple[Path, Path]) -> None:
        from haute._sandbox import safe_unpickle

        assert safe_unpickle("data/model.pkl") == {"ok": True}

    @pytest.mark.parametrize("path", ["../outside/model.pkl", "link_dir/model.pkl"])
    def test_escapes_are_refused(self, layout: tuple[Path, Path], path: str) -> None:
        from haute._sandbox import safe_unpickle

        if path.startswith("link_dir"):
            _require_links(layout[0])
        with pytest.raises(PathOutsideProjectError):
            validate_project_path(path)
        with pytest.raises(PathOutsideProjectError):
            safe_unpickle(path)


class TestRecoveryArtifactPaths:
    def test_inside_is_accepted(self, layout: tuple[Path, Path]) -> None:
        from haute._artifact_paths import safe_path

        project, _ = layout
        assert safe_path(project, "data/rows.csv") == project.resolve() / "data" / "rows.csv"

    @pytest.mark.parametrize("relative", ["../outside/rows.csv", "link_dir/rows.csv"])
    def test_escapes_are_refused(self, layout: tuple[Path, Path], relative: str) -> None:
        from haute._artifact_paths import safe_path
        from haute._pipeline_repair import PipelineRepairError

        project, _ = layout
        if relative.startswith("link_dir"):
            _require_links(project)
        with pytest.raises(PipelineRepairError):
            safe_path(project, relative)


class TestSqliteLocator:
    def test_inside_is_accepted(self, layout: tuple[Path, Path]) -> None:
        from haute._database_io import validate_sqlite_project_path

        project, _ = layout
        resolved = validate_sqlite_project_path(
            "sqlite:///data/rates.db", base_dir=project, project_root=project
        )
        assert resolved == project.resolve() / "data" / "rates.db"

    @pytest.mark.parametrize("relative", ["../outside/rates.db", "link_dir/rates.db"])
    def test_escapes_are_refused(self, layout: tuple[Path, Path], relative: str) -> None:
        from haute._database_io import validate_sqlite_project_path

        project, _ = layout
        if relative.startswith("link_dir"):
            _require_links(project)
        with pytest.raises(ValueError, match="outside the project root"):
            validate_sqlite_project_path(
                f"sqlite:///{relative}", base_dir=project, project_root=project
            )


class TestMlflowSettingsWriteTarget:
    def test_inside_is_accepted(self, layout: tuple[Path, Path]) -> None:
        from haute.modelling._mlflow_settings import _project_write_target

        project, _ = layout
        toml = project / "haute.toml"
        toml.write_text("", encoding="utf-8")
        assert _project_write_target(toml, project) == toml.resolve()

    @pytest.mark.parametrize("escape", ["dotdot", "symlink"])
    def test_escapes_are_refused(self, layout: tuple[Path, Path], escape: str) -> None:
        from haute.errors import MlflowConfigError
        from haute.modelling._mlflow_settings import _project_write_target

        project, outside = layout
        (outside / "haute.toml").write_text("", encoding="utf-8")
        toml = project / ".." / "outside" / "haute.toml"
        if escape == "symlink":
            _require_links(project)
            toml = project / "linked.toml"
            toml.symlink_to(outside / "haute.toml")
        with pytest.raises(MlflowConfigError, match="outside the project root"):
            _project_write_target(toml, project)
