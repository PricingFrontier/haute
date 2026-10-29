"""One path-containment check serves every caller.

``haute._sandbox.contained_path`` is the only comparison of a path against a
directory it must stay in. For each caller a ``..`` escape and a symlink escape
are refused with the caller's own contract and an in-project path is accepted.
Every other ``is_relative_to`` or ``commonpath`` call in the shipped code is
marked ``# not contained_path: <reason>``, and the scan at the end of this
module fails on one that is not.
"""

from __future__ import annotations

import ast
import pickle
import time
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from haute._sandbox import contained_path, is_contained, validate_project_path
from haute.errors import InvalidPathError, PathOutsideProjectError
from haute.routes._helpers import pipeline_dir as _real_pipeline_dir
from tests._source_files import REPO_ROOT, source_files
from tests.job_store_support import seed_job
from tests.optimiser_fixtures import make_solved_result


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

    def test_is_contained_gives_the_same_answer(self, layout: tuple[Path, Path]) -> None:
        project, _ = layout
        assert is_contained(project, "data/rows.csv")
        assert not is_contained(project, "../outside/rows.csv")
        with pytest.raises(InvalidPathError):
            is_contained(project, "data/rows.csv\x00")
        _require_links(project)
        assert not is_contained(project, "link_dir/rows.csv")


# ---------------------------------------------------------------------------
# Routes that used the removed route helper
# ---------------------------------------------------------------------------


def _optimiser_job() -> dict:
    return {
        "status": "completed",
        # Save publishes from the completion summary.
        "result": make_solved_result(
            total_objective=100.0,
            baseline_objective=90.0,
            constraints={"volume": 0.9},
            iterations=10,
        ),
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


# ---------------------------------------------------------------------------
# Every other caller: a ``..`` escape and a link escape, each refused with the
# caller's own contract, beside an in-project path that is accepted
# ---------------------------------------------------------------------------

_ESCAPES = pytest.mark.parametrize("escape", ["dotdot", "symlink"])

_PIPELINE = """\
import polars as pl

import haute

pipeline = haute.Pipeline("main")


@pipeline.polars
def quotes() -> pl.LazyFrame:
    return pl.LazyFrame({"x": [1, 2]})
"""


def _escaping(project: Path, escape: str, name: str) -> str:
    """A path relative to *project* that leaves it, through ``..`` or ``link_dir``."""
    if escape == "symlink":
        _require_links(project)
        return f"link_dir/{name}"
    return f"../outside/{name}"


def _back_inside(project: Path, name: str) -> str:
    """An absolute spelling outside *project* that resolves back to its *name*."""
    back = project.parent / "outside" / ".." / project.name / name
    assert back.resolve() == (project / name).resolve()
    return str(back)


def _link(project: Path, link: Path, target: Path) -> None:
    """Make *link* a directory link to *target*, where the platform can."""
    _require_links(project)
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(target, target_is_directory=True)


def _listing(folder: Path) -> list[str]:
    return sorted(path.relative_to(folder).as_posix() for path in folder.rglob("*"))


class TestRequestInputCallers:
    """Request input reaches the check as received, so an absolute spelling outside
    the project is refused before it resolves, even one that resolves back inside."""

    def test_a_repair_source_identity(self, layout: tuple[Path, Path]) -> None:
        from haute._pipeline_repair import PipelineRepairError, _resolve_project_file

        project, outside = layout
        (project / "main.py").write_text(_PIPELINE, encoding="utf-8")
        (outside / "main.py").write_text(_PIPELINE, encoding="utf-8")
        assert _resolve_project_file(project, "main.py", suffix=".py") == (
            project.resolve() / "main.py"
        )
        for raw in (
            _escaping(project, "dotdot", "main.py"),
            _back_inside(project, "main.py"),
            _escaping(project, "symlink", "main.py"),
        ):
            with pytest.raises(PipelineRepairError) as refused:
                _resolve_project_file(project, raw, suffix=".py")
            assert (refused.value.code, refused.value.status_code) == (
                "repair_path_outside_project",
                403,
            )

    @_ESCAPES
    def test_a_model_export_destination(self, layout: tuple[Path, Path], escape: str) -> None:
        from haute._path_resolution import RuntimePathOutsideProjectError
        from haute.modelling._model_export import resolve_model_export_destination

        project, _ = layout
        inside = resolve_model_export_destination(
            "models/fit.cbm", model_suffix=".cbm", project_root=project
        )
        assert inside.path == project.resolve() / "models" / "fit.cbm"
        with pytest.raises(RuntimePathOutsideProjectError, match="outside the project root"):
            resolve_model_export_destination(
                _escaping(project, escape, "fit.cbm"), model_suffix=".cbm", project_root=project
            )

    @_ESCAPES
    def test_the_assistant_pipeline_source(self, layout: tuple[Path, Path], escape: str) -> None:
        from haute.assistant._application import PipelineApplicationService
        from haute.assistant._ops import AssistantOperationError

        project, _ = layout
        service = PipelineApplicationService(
            project_root=project,
            pipeline_root=project,
            mutations_readiness=lambda _root: (True, None),
            publish_document_update=lambda _source, _change: "f" * 64,
        )
        assert service._source_path("main.py") == project.resolve() / "main.py"
        with pytest.raises(AssistantOperationError) as refused:
            service._source_path(_escaping(project, escape, "main.py"))
        assert refused.value.code == "project_source_forbidden"

    @_ESCAPES
    def test_the_project_knowledge_pipeline_source(
        self, layout: tuple[Path, Path], escape: str
    ) -> None:
        from haute.assistant._project_knowledge import _graph_fact

        project, outside = layout
        (project / "main.py").write_text(_PIPELINE, encoding="utf-8")
        (outside / "main.py").write_text(_PIPELINE, encoding="utf-8")
        assert _graph_fact(project.resolve(), "main.py").source == "main.py"
        with pytest.raises(ValueError, match="must resolve inside the project root"):
            _graph_fact(project.resolve(), _escaping(project, escape, "main.py"))

    @_ESCAPES
    def test_a_static_file_of_the_spa(self, layout: tuple[Path, Path], escape: str) -> None:
        from haute.server import _spa_static_file

        project, _ = layout
        assert _spa_static_file(project, "data/rows.csv") == project.resolve() / "data" / "rows.csv"
        assert _spa_static_file(project, "data/missing.js") is None
        assert _spa_static_file(project, _escaping(project, escape, "rows.csv")) is None

    def test_absolute_spellings_that_resolve_back_inside_are_refused(
        self, layout: tuple[Path, Path]
    ) -> None:
        """A caller that resolved request input before the check would accept these."""
        from haute._path_resolution import RuntimePathOutsideProjectError
        from haute.assistant._application import PipelineApplicationService
        from haute.assistant._ops import AssistantOperationError
        from haute.assistant._project_knowledge import _graph_fact
        from haute.modelling._model_export import resolve_model_export_destination
        from haute.server import _spa_static_file

        project, _ = layout
        (project / "main.py").write_text(_PIPELINE, encoding="utf-8")
        (project / "models").mkdir()
        service = PipelineApplicationService(
            project_root=project,
            pipeline_root=project,
            mutations_readiness=lambda _root: (True, None),
            publish_document_update=lambda _source, _change: "f" * 64,
        )

        with pytest.raises(RuntimePathOutsideProjectError, match="outside the project root"):
            resolve_model_export_destination(
                _back_inside(project, "models/fit.cbm"), model_suffix=".cbm", project_root=project
            )
        with pytest.raises(AssistantOperationError) as refused:
            service._source_path(_back_inside(project, "main.py"))
        assert refused.value.code == "project_source_forbidden"
        with pytest.raises(ValueError, match="must resolve inside the project root"):
            _graph_fact(project.resolve(), _back_inside(project, "main.py"))
        assert _spa_static_file(project, _back_inside(project, "data/rows.csv")) is None


class TestRuntimePaths:
    @_ESCAPES
    def test_a_relative_runtime_path(self, layout: tuple[Path, Path], escape: str) -> None:
        from haute._path_resolution import RuntimePathOutsideProjectError, resolve_runtime_file_path

        project, _ = layout
        assert resolve_runtime_file_path("data/rows.csv", project_root=project) == (
            project.resolve() / "data" / "rows.csv"
        )
        with pytest.raises(RuntimePathOutsideProjectError):
            resolve_runtime_file_path(_escaping(project, escape, "rows.csv"), project_root=project)

    @_ESCAPES
    def test_an_absolute_runtime_path(self, layout: tuple[Path, Path], escape: str) -> None:
        from haute._path_resolution import RuntimePathOutsideProjectError, resolve_runtime_file_path

        project, _ = layout
        inside = str(project / "data" / "rows.csv")
        assert resolve_runtime_file_path(inside, project_root=project) == Path(inside)
        with pytest.raises(RuntimePathOutsideProjectError):
            resolve_runtime_file_path(
                str(project / _escaping(project, escape, "rows.csv")), project_root=project
            )

    @_ESCAPES
    def test_a_pipeline_source_spelled_inside_the_project(
        self, layout: tuple[Path, Path], escape: str
    ) -> None:
        from haute._path_resolution import (
            RuntimePathOutsideProjectError,
            runtime_project_root_scope,
        )

        project, _ = layout
        with runtime_project_root_scope(str(project / "main.py")) as root:
            assert root == project.resolve()
        with (
            pytest.raises(RuntimePathOutsideProjectError),
            runtime_project_root_scope(str(project / _escaping(project, escape, "main.py"))),
        ):
            pass


class TestExecutorOutputs:
    @_ESCAPES
    def test_a_sink_path(self, layout: tuple[Path, Path], escape: str) -> None:
        from haute._path_resolution import RuntimePathOutsideProjectError
        from haute._types import PipelineGraph
        from haute.executor import _contain_output_path

        project, _ = layout
        graph = PipelineGraph(nodes=[], edges=[], source_file="main.py")
        assert _contain_output_path(graph, "outputs/x.parquet", project_root=project) == (
            project.resolve() / "outputs" / "x.parquet"
        )
        with pytest.raises(RuntimePathOutsideProjectError):
            _contain_output_path(
                graph, _escaping(project, escape, "x.parquet"), project_root=project
            )

    @_ESCAPES
    def test_the_paths_of_a_publication(self, layout: tuple[Path, Path], escape: str) -> None:
        from haute.executor import _validate_output_publish_paths, new_data_output_staging_path

        project, _ = layout
        inside = project / "outputs" / "x.parquet"
        _validate_output_publish_paths(
            inside, new_data_output_staging_path(inside), project_root=project
        )
        final = project / _escaping(project, escape, "x.parquet")
        with pytest.raises(ValueError, match="outside the project root"):
            _validate_output_publish_paths(
                final, new_data_output_staging_path(final), project_root=project
            )

    @_ESCAPES
    def test_a_staging_path_at_cleanup(self, layout: tuple[Path, Path], escape: str) -> None:
        from haute.executor import _cleanup_output_staging_path

        project, outside = layout
        inside = project / "data" / ".x.haute-stage.parquet"
        inside.write_bytes(b"staged")
        _cleanup_output_staging_path(inside, project_root=project)
        assert not inside.exists()
        (outside / ".x.haute-stage.parquet").write_bytes(b"not ours")
        _cleanup_output_staging_path(
            project / _escaping(project, escape, ".x.haute-stage.parquet"), project_root=project
        )
        assert (outside / ".x.haute-stage.parquet").read_bytes() == b"not ours"


class TestNodeConfigs:
    def test_a_config_sidecar_path(self, layout: tuple[Path, Path]) -> None:
        from haute._config_io import config_path_for_node
        from haute._types import NodeType

        project, outside = layout
        assert config_path_for_node(NodeType.DATA_INPUT, "quotes", base_dir=project) == (
            project.resolve() / "config" / "data_input" / "quotes.json"
        )
        with pytest.raises(ValueError, match="must not contain path separators or '..'"):
            config_path_for_node(NodeType.DATA_INPUT, "..", base_dir=project)
        _link(project, project / "config" / "data_input", outside)
        with pytest.raises(ValueError, match="escapes config directory"):
            config_path_for_node(NodeType.DATA_INPUT, "quotes", base_dir=project)

    @_ESCAPES
    def test_a_loaded_config(self, layout: tuple[Path, Path], escape: str) -> None:
        from haute._config_io import load_node_config

        project, _ = layout
        assert load_node_config("data/doc.json", base_dir=project) == {"k": 1}
        with pytest.raises(ValueError, match="resolves outside project root"):
            load_node_config(_escaping(project, escape, "doc.json"), base_dir=project)

    @_ESCAPES
    def test_a_model_score_config(self, layout: tuple[Path, Path], escape: str) -> None:
        from haute._model_scorer import score_from_config
        from haute.errors import ConfigError

        project, _ = layout
        # An in-project config gets past containment to its own validation.
        with pytest.raises(ConfigError, match="no model source"):
            score_from_config(config="data/doc.json", base_dir=project)
        with pytest.raises(ValueError, match="resolves outside project root"):
            score_from_config(config=_escaping(project, escape, "doc.json"), base_dir=project)

    @_ESCAPES
    def test_a_submodel_reference(self, layout: tuple[Path, Path], escape: str) -> None:
        from haute._submodel_paths import (
            MalformedSubmodelPathError,
            SubmodelPathOutsideProjectError,
            resolve_submodel_reference,
        )

        project, _ = layout
        resolved, base = resolve_submodel_reference(
            "modules/pricing.py", pipeline_dir=project, project_root=project
        )
        assert (resolved, base) == (project.resolve() / "modules" / "pricing.py", project.resolve())
        # A ``..`` segment is refused as malformed before anything resolves.
        refusal = (
            SubmodelPathOutsideProjectError if escape == "symlink" else MalformedSubmodelPathError
        )
        with pytest.raises(refusal):
            resolve_submodel_reference(
                _escaping(project, escape, "main.py"), pipeline_dir=project, project_root=project
            )


class TestRecoveryAndRepair:
    @_ESCAPES
    def test_the_editor_document(self, layout: tuple[Path, Path], escape: str) -> None:
        from haute._pipeline_recovery import load_pipeline_editor_document

        project, outside = layout
        (project / "main.py").write_text(_PIPELINE, encoding="utf-8")
        (outside / "main.py").write_text(_PIPELINE, encoding="utf-8")
        document = load_pipeline_editor_document(project / "main.py", project_root=project)
        assert document.source_revision is not None
        with pytest.raises(ValueError, match="escapes the project root"):
            load_pipeline_editor_document(
                project / _escaping(project, escape, "main.py"), project_root=project
            )

    @_ESCAPES
    def test_a_revision_artifact(self, layout: tuple[Path, Path], escape: str) -> None:
        from haute._pipeline_revision import pipeline_recovery_revision

        project, _ = layout
        assert pipeline_recovery_revision(
            project_root=project, artifacts=[("node_config", project / "data" / "doc.json")]
        )
        with pytest.raises(ValueError, match="escapes the project root"):
            pipeline_recovery_revision(
                project_root=project,
                artifacts=[("node_config", project / _escaping(project, escape, "doc.json"))],
            )

    @_ESCAPES
    def test_a_config_reference_outside_is_not_a_recovery_artifact(
        self, layout: tuple[Path, Path], escape: str
    ) -> None:
        from haute._pipeline_recovery import _recovery_artifacts

        project, _ = layout
        source = (
            "import polars as pl\n\nimport haute\n\npipeline = haute.Pipeline('main')\n\n\n"
            "@pipeline.data_input(config='config/data_input/inside.json')\n"
            "def inside() -> pl.LazyFrame:\n    return pl.scan_csv('x.csv')\n\n\n"
            f"@pipeline.data_input(config='{_escaping(project, escape, 'doc.json')}')\n"
            "def escape() -> pl.LazyFrame:\n    return pl.scan_csv('x.csv')\n"
        )
        artifacts = _recovery_artifacts(project / "main.py", project, parent_source=source)
        assert [path for role, path in artifacts if role == "node_config"] == [
            project.resolve() / "config" / "data_input" / "inside.json"
        ]

    @_ESCAPES
    def test_a_repair_config_reference(self, layout: tuple[Path, Path], escape: str) -> None:
        from haute._pipeline_repair import PipelineRepairError, _resolve_config_reference

        project, _ = layout
        root_path = project / "main.py"
        assert _resolve_config_reference(
            root_path=root_path, project_root=project, reference="data/doc.json"
        ) == (project.resolve() / "data" / "doc.json")
        with pytest.raises(PipelineRepairError) as refused:
            _resolve_config_reference(
                root_path=root_path,
                project_root=project,
                reference=_escaping(project, escape, "doc.json"),
            )
        assert (refused.value.code, refused.value.status_code) == (
            "repair_config_outside_project",
            403,
        )


def _data_input(label: str) -> object:
    from haute._types import GraphNode, NodeData, NodeType

    return GraphNode(
        id=label,
        data=NodeData(
            label=label,
            nodeType=NodeType.DATA_INPUT,
            config={
                "inputType": "file",
                "format": "csv",
                "mode": "scan",
                "path": "data/rows.csv",
                "arguments": {},
            },
        ),
    )


class TestSaveService:
    @staticmethod
    def _service(project: Path, pipeline_root: Path | None = None):  # noqa: ANN205
        from haute.routes._save_pipeline import SavePipelineService

        return SavePipelineService(project_root=project, pipeline_root=pipeline_root)

    @_ESCAPES
    def test_the_pipeline_root(self, layout: tuple[Path, Path], escape: str) -> None:
        project, _ = layout
        (project / "rating").mkdir()
        self._service(project, project / "rating")
        with pytest.raises(ValueError, match="pipeline_root must resolve inside project_root"):
            self._service(project, project / _escaping(project, escape, ""))

    @_ESCAPES
    def test_a_source_file_outside_the_pipeline_root(
        self, layout: tuple[Path, Path], escape: str
    ) -> None:
        project, outside = layout
        rating = project / "rating"
        rating.mkdir()
        service = self._service(project, rating)
        service._validate_source_file_matches_pipeline_root(rating / "main.py")
        if escape == "symlink":
            _link(project, rating / "elsewhere", outside)
            source = rating / "elsewhere" / "main.py"
        else:
            source = rating / ".." / "main.py"
        with pytest.raises(HTTPException) as refused:
            service._validate_source_file_matches_pipeline_root(source)
        assert refused.value.status_code == 400

    def test_a_config_write_through_a_link_out_of_the_pipeline_folder(
        self, layout: tuple[Path, Path]
    ) -> None:
        """Config paths are made from node names, which carry no ``..``."""
        from haute._types import PipelineGraph

        project, outside = layout
        service = self._service(project)
        service._prev_config_files = {}
        service._write_config_files(PipelineGraph(nodes=[_data_input("quotes")], edges=[]))
        assert (project / "config" / "data_input" / "quotes.json").is_file()

        _link(project, project / "elsewhere" / "config", outside)
        linked = self._service(project, project / "elsewhere")
        linked._prev_config_files = {}
        with pytest.raises(HTTPException) as refused:
            linked._write_config_files(PipelineGraph(nodes=[_data_input("quotes")], edges=[]))
        assert refused.value.status_code == 400
        assert "resolves outside the pipeline folder" in str(refused.value.detail)
        assert not (outside / "data_input").exists()

    @_ESCAPES
    def test_the_stale_sweep_leaves_a_file_outside_the_pipeline_folder(
        self, layout: tuple[Path, Path], escape: str
    ) -> None:
        from haute._types import PipelineGraph

        project, outside = layout
        stale = project / "config" / "data_input" / "old.json"
        stale.parent.mkdir(parents=True)
        stale.write_text("{}", encoding="utf-8")
        escaping = _escaping(project, escape, "doc.json")
        service = self._service(project)
        service._prev_config_files = {"config/data_input/old.json": "{}", escaping: "{}"}
        graph = PipelineGraph(nodes=[], edges=[])
        service._write_config_files(graph)

        removed = service._remove_stale_config_files(graph)

        assert removed == [stale.resolve()]
        assert (outside / "doc.json").read_text(encoding="utf-8") == '{"k": 1}'

    @_ESCAPES
    def test_the_on_disk_baseline_leaves_out_a_file_outside_the_pipeline_folder(
        self, layout: tuple[Path, Path], escape: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from haute.routes._save_pipeline import SavePipelineService

        project, _ = layout
        (project / "main.py").write_text(_PIPELINE, encoding="utf-8")
        owned = project / "config" / "polars" / "quotes.json"
        owned.parent.mkdir(parents=True)
        owned.write_text("{}", encoding="utf-8")
        escaping = _escaping(project, escape, "doc.json")
        monkeypatch.setattr(
            SavePipelineService,
            "_discarded_sidecars_recursive",
            lambda _self, _graph: ["config/polars/quotes.json", escaping],
        )

        prev = self._service(project)._compute_disk_prev_config_files(project / "main.py")

        assert "config/polars/quotes.json" in prev
        assert escaping not in prev


class TestWorkerArtifacts:
    @staticmethod
    def _root(project: Path) -> Path:
        root = project / "artifacts"
        root.mkdir()
        (root / "inside.bin").write_bytes(b"artifact")
        return root

    @_ESCAPES
    def test_a_staged_path(self, layout: tuple[Path, Path], escape: str) -> None:
        from haute._worker_protocol import WorkerProtocolError, WorkerRuntime

        project, outside = layout
        root = self._root(project)
        runtime = WorkerRuntime(progress_queue=None, artifact_root=str(root))
        assert runtime.staged_path("a/b.bin") == root.resolve() / "a" / "b.bin"
        if escape == "symlink":
            _link(project, root / "linked", outside)
            relative = "linked/sub/b.bin"
        else:
            relative = "../b.bin"
        before = _listing(outside)
        with pytest.raises(WorkerProtocolError):
            runtime.staged_path(relative)
        assert _listing(outside) == before

    @_ESCAPES
    def test_a_manifest_built_by_the_worker(self, layout: tuple[Path, Path], escape: str) -> None:
        from haute._worker_protocol import WorkerProtocolError, build_artifact_manifest

        project, outside = layout
        root = self._root(project)
        manifest = build_artifact_manifest(
            artifact_root=root, path=root / "inside.bin", kind="k", lifetime="staged"
        )
        assert manifest.relative_path == "inside.bin"
        if escape == "symlink":
            _link(project, root / "linked", outside)
            path = root / "linked" / "rows.csv"
        else:
            path = root / ".." / ".." / "outside" / "rows.csv"
        with pytest.raises(WorkerProtocolError, match="not a contained regular file"):
            build_artifact_manifest(artifact_root=root, path=path, kind="k", lifetime="staged")

    @_ESCAPES
    def test_a_manifest_the_parent_validates(self, layout: tuple[Path, Path], escape: str) -> None:
        import hashlib

        from haute._worker_protocol import (
            WorkerArtifactManifest,
            WorkerProtocolError,
            WorkerResultManifest,
            build_artifact_manifest,
            validate_result_manifest,
        )

        project, outside = layout
        root = self._root(project)
        limits = {"artifact_kinds": frozenset({"k"}), "max_artifact_size_bytes": 1 << 20}
        inside = build_artifact_manifest(
            artifact_root=root, path=root / "inside.bin", kind="k", lifetime="staged"
        )
        validate_result_manifest(WorkerResultManifest({}, (inside,)), artifact_root=root, **limits)

        data = (outside / "rows.csv").read_bytes()
        if escape == "dotdot":
            # A ``..`` is refused as the manifest is built, before containment.
            with pytest.raises(WorkerProtocolError, match="normalized and relative"):
                WorkerArtifactManifest(
                    "k", "../rows.csv", len(data), hashlib.sha256(data).hexdigest(), "staged"
                )
            return
        _link(project, root / "linked", outside)
        linked = WorkerArtifactManifest(
            "k", "linked/rows.csv", len(data), hashlib.sha256(data).hexdigest(), "staged"
        )
        with pytest.raises(WorkerProtocolError, match="not a contained regular file"):
            validate_result_manifest(
                WorkerResultManifest({}, (linked,)), artifact_root=root, **limits
            )


class TestServerOwnedArtifacts:
    @_ESCAPES
    def test_an_optimiser_artifact_handle(self, layout: tuple[Path, Path], escape: str) -> None:
        from haute.routes._optimiser_artifacts import (
            _ARTIFACT_HANDLE_VERSION,
            _validate_server_owned_parquet_handle,
        )

        project, outside = layout
        root = project / "artifacts"
        (root / "run-1").mkdir(parents=True)

        def validate(directory: Path) -> tuple[Path, Path]:
            return _validate_server_owned_parquet_handle(
                {
                    "kind": "k",
                    "version": _ARTIFACT_HANDLE_VERSION,
                    "format": "parquet",
                    "directory": str(directory),
                    "path": str(directory / "result.parquet"),
                },
                kind="k",
                root=root.resolve(),
                directory_prefix="run-",
                filename="result.parquet",
                description="Test",
            )

        inside = root.resolve() / "run-1"
        assert validate(inside) == (inside / "result.parquet", inside)
        if escape == "symlink":
            _link(project, root / "run-linked", outside)
            directory = root / "run-linked"
        else:
            directory = root / ".." / ".." / "outside"
        with pytest.raises(ValueError, match="outside the artifact root"):
            validate(directory)

    @_ESCAPES
    def test_an_mlflow_artifact_cache_path(self, layout: tuple[Path, Path], escape: str) -> None:
        from haute._mlflow_io import _artifact_cache_path

        project, outside = layout
        cache = project / "cache"
        digest = "0123456789abcdef"
        path = _artifact_cache_path(cache, digest, "run1", "model/model.cbm")
        assert path.parent.parent.parent == cache.resolve() / digest
        if escape == "symlink":
            _link(project, cache / digest, outside)
            with pytest.raises(ValueError, match="Invalid artifact cache identity"):
                _artifact_cache_path(cache, digest, "run1", "model/model.cbm")
        else:
            # A run id is a single path component, so ``..`` is refused by name.
            with pytest.raises(ValueError, match="run"):
                _artifact_cache_path(cache, digest, "..", "model/model.cbm")


class TestAssistantStorage:
    @_ESCAPES
    def test_the_assistant_pipeline_root(self, layout: tuple[Path, Path], escape: str) -> None:
        from haute.assistant._application import PipelineApplicationService

        project, _ = layout

        def service(pipeline_root: Path) -> PipelineApplicationService:
            return PipelineApplicationService(
                project_root=project,
                pipeline_root=pipeline_root,
                mutations_readiness=lambda _root: (True, None),
                publish_document_update=lambda _source, _change: "f" * 64,
            )

        service(project / "data")
        with pytest.raises(ValueError, match="pipeline_root must resolve inside project_root"):
            service(project / _escaping(project, escape, ""))

    def test_the_session_storage(self, layout: tuple[Path, Path]) -> None:
        """A fixed path no input reaches: only a link can move it."""
        from haute.errors import ConfigError
        from haute.routes.assistant import _sessions_storage_dir

        project, outside = layout
        assert _sessions_storage_dir() == project.resolve() / ".haute" / "assistant" / "sessions"
        _link(project, project / ".haute", outside)
        with pytest.raises(ConfigError, match="must resolve inside the project root"):
            _sessions_storage_dir()


class TestProjectKnowledge:
    """Fixed paths no input reaches: only a link can move them. A linked ``docs/``
    and a linked cache are refused in ``tests/test_assistant_project_knowledge.py``."""

    def test_haute_toml(self, layout: tuple[Path, Path]) -> None:
        from haute.assistant._project_knowledge import _toml_fact

        project, outside = layout
        (project / "haute.toml").write_text("[project]\n", encoding="utf-8")
        item = _toml_fact(project.resolve())
        assert item is not None and item.source == "haute.toml"
        _require_links(project)
        (project / "haute.toml").unlink()
        (outside / "haute.toml").write_text("[project]\n", encoding="utf-8")
        (project / "haute.toml").symlink_to(outside / "haute.toml")
        with pytest.raises(ValueError, match="regular file inside the project root"):
            _toml_fact(project.resolve())

    def test_a_document(self, layout: tuple[Path, Path]) -> None:
        from haute.assistant._project_knowledge import _document_items

        project, outside = layout
        (project / "docs").mkdir()
        (project / "docs" / "terms.md").write_text("Sensitivity: public\n\nterms", encoding="utf-8")
        _require_links(project)
        (outside / "secret.md").write_text("Sensitivity: public\n\nOUTSIDE", encoding="utf-8")
        (project / "docs" / "linked.md").symlink_to(outside / "secret.md")

        assert [item.source for item in _document_items(project.resolve())] == ["docs/terms.md"]


class TestConfiguredPipelines:
    @_ESCAPES
    def test_the_pipeline_haute_toml_names(self, layout: tuple[Path, Path], escape: str) -> None:
        """The route helper itself; the layout fixture stands one in for the routes."""
        from haute.errors import ConfigError

        project, outside = layout
        (outside / "main.py").write_text(_PIPELINE, encoding="utf-8")
        toml = project / "haute.toml"
        toml.write_text('[project]\npipeline = "main.py"\n', encoding="utf-8")
        assert _real_pipeline_dir() == project.resolve()
        _real_pipeline_dir.cache_clear()  # it is cached for the process
        toml.write_text(
            f'[project]\npipeline = "{_escaping(project, escape, "main.py")}"\n', encoding="utf-8"
        )
        with pytest.raises(ConfigError, match="resolves outside the project root"):
            _real_pipeline_dir()

    @_ESCAPES
    def test_the_pipeline_a_deployment_names(self, layout: tuple[Path, Path], escape: str) -> None:
        from haute.deploy._config import DeployConfig, resolve_config
        from haute.errors import DeployError

        project, outside = layout
        (project / "main.py").write_text(_PIPELINE, encoding="utf-8")
        (outside / "main.py").write_text(_PIPELINE, encoding="utf-8")
        # An in-project pipeline gets past containment to the deploy's own checks.
        with pytest.raises(ValueError, match="No output node"):
            resolve_config(
                DeployConfig(model_name="m", pipeline_file=Path("main.py"), project_dir=project)
            )
        config = DeployConfig(
            model_name="m",
            pipeline_file=Path(_escaping(project, escape, "main.py")),
            project_dir=project,
        )
        with pytest.raises(DeployError, match="resolves outside the configured project root"):
            resolve_config(config)


# ---------------------------------------------------------------------------
# Every other path comparison says why it is not the check
# ---------------------------------------------------------------------------

_MARKER = "# not contained_path:"
_COMPARISONS = frozenset({"is_relative_to", "commonpath"})


def _marked(lines: list[str], lineno: int) -> bool:
    """Whether the call on *lineno* carries the marker, on its own line or in the
    comment block directly above it, with a reason after the colon."""
    candidates = [lines[lineno - 1]]
    above = lineno - 2
    while above >= 0 and lines[above].lstrip().startswith("#"):
        candidates.append(lines[above])
        above -= 1
    return any(_MARKER in line and line.split(_MARKER, 1)[1].strip() for line in candidates)


def _unmarked_comparisons(source: str, *, sandbox: bool = False) -> list[int]:
    """Lines of *source* holding an unmarked ``is_relative_to``/``commonpath`` call;
    in the sandbox module the body of ``contained_path`` itself is exempt."""
    tree = ast.parse(source)
    lines = source.splitlines()
    exempt: set[int] = set()
    if sandbox:
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "contained_path":
                exempt.update(range(node.lineno, (node.end_lineno or node.lineno) + 1))
    found: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = (
            func.attr
            if isinstance(func, ast.Attribute)
            else func.id
            if isinstance(func, ast.Name)
            else None
        )
        if name in _COMPARISONS and node.lineno not in exempt and not _marked(lines, node.lineno):
            found.add(node.lineno)
    return sorted(found)


def test_the_scan_finds_unmarked_comparisons_and_accepts_marked_ones() -> None:
    source = """
import os.path
from os.path import commonpath

def contained_path(root, path):
    return path.is_relative_to(root)

def other(root, path):
    a = path.is_relative_to(root)
    b = path.is_relative_to(root)  # not contained_path: chooses the root
    # not contained_path: classifies a change event
    # by its folder
    c = path.is_relative_to(root)
    d = os.path.commonpath([root, path])
    e = commonpath([root, path])
    f = path.is_relative_to(root)  # not contained_path:
    return a, b, c, d, e, f
"""
    assert _unmarked_comparisons(source, sandbox=True) == [9, 14, 15, 16]
    assert _unmarked_comparisons(source) == [6, 9, 14, 15, 16]


@pytest.mark.meta
def test_every_other_path_comparison_is_marked() -> None:
    sandbox = REPO_ROOT / "src" / "haute" / "_sandbox.py"
    unmarked = [
        f"{path.relative_to(REPO_ROOT).as_posix()}:{lineno}"
        for path in [*source_files(REPO_ROOT / "src" / "haute"), REPO_ROOT / "hatch_build.py"]
        for lineno in _unmarked_comparisons(
            path.read_text(encoding="utf-8"), sandbox=path == sandbox
        )
    ]
    assert unmarked == [], (
        "Route these through haute._sandbox.contained_path, or mark each with "
        f"'{_MARKER} <reason>' on its line or the line above: {unmarked}"
    )
