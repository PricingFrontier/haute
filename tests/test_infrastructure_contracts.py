"""Focused coverage for shared infrastructure fail-loudly contracts."""

from __future__ import annotations

import re
import tomllib
from importlib import metadata
from pathlib import Path
from typing import Any, get_args, get_origin

import pytest
from fastapi.routing import APIRoute
from pydantic import BaseModel

from haute import _cache
from haute import _registry as registry
from haute._types import NodeType
from tests._source_files import source_files


def _exec_builder(*_args: object, **_kwargs: object) -> tuple[str, Any, bool]:
    return "node", lambda: None, False


def _codegen_builder(*_args: object, **_kwargs: object) -> str:
    return "def node():\n    pass\n"


class TestNodeRegistryContracts:
    def test_register_exec_rejects_duplicate_registration(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(registry, "NODE_REGISTRY", {})

        registry.register_exec(NodeType.POLARS)(_exec_builder)

        with pytest.raises(RuntimeError, match="duplicate exec registration"):
            registry.register_exec(NodeType.POLARS)(_exec_builder)

    def test_register_exec_stores_column_contract(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(registry, "NODE_REGISTRY", {})

        def contract(config: dict[str, Any]) -> set[str]:
            return set(config)

        registry.register_exec(NodeType.POLARS, column_contract=contract)(_exec_builder)

        assert registry.NODE_REGISTRY[NodeType.POLARS].column_contract is contract

    def test_set_codegen_rejects_duplicate_registration(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(registry, "NODE_REGISTRY", {})

        registry.set_codegen(NodeType.POLARS, _codegen_builder)

        with pytest.raises(RuntimeError, match="duplicate codegen registration"):
            registry.set_codegen(NodeType.POLARS, _codegen_builder)

    def test_getters_fail_loudly_when_entry_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(registry, "NODE_REGISTRY", {})

        with pytest.raises(KeyError, match="no exec builder"):
            registry.get_exec(NodeType.POLARS)
        with pytest.raises(KeyError, match="no codegen builder"):
            registry.get_codegen(NodeType.POLARS)

    def test_validate_registry_complete_reports_both_missing_sides(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(registry, "NODE_REGISTRY", {})

        with pytest.raises(RuntimeError) as exc_info:
            registry.validate_registry_complete()

        msg = str(exc_info.value)
        assert "Missing exec" in msg
        assert "Missing codegen" in msg


class TestGraphFingerprintCanonicalisation:
    def test_rejects_dicts_with_non_string_keys(self) -> None:
        with pytest.raises(TypeError, match="non-string key"):
            _cache._canonicalise({1: "not-json-object-compatible"})

    def test_rejects_values_without_deterministic_canonical_form(self) -> None:
        with pytest.raises(TypeError, match="no deterministic canonical form"):
            _cache._canonicalise(object())

    def test_rejects_unknown_canonical_sort_key_types(self) -> None:
        with pytest.raises(TypeError, match="Cannot produce sort key"):
            _cache._sort_key(object())

    def test_canonicalises_sets_by_value_not_insertion_order(self) -> None:
        left = _cache._canonicalise({"items": {"b", "a", "c"}})
        right = _cache._canonicalise({"items": {"c", "b", "a"}})

        assert left == right == {"items": ["a", "b", "c"]}


class TestApiRouteContracts:
    def test_openapi_and_installed_package_versions_match_pyproject(self) -> None:
        from haute import __version__
        from haute.server import app

        pyproject_path = Path(__file__).resolve().parents[1] / "pyproject.toml"
        with pyproject_path.open("rb") as pyproject_file:
            product_version = tomllib.load(pyproject_file)["project"]["version"]

        try:
            installed_version = metadata.version("haute")
        except metadata.PackageNotFoundError:
            pytest.fail(
                "The test environment has no installed Haute distribution metadata; "
                "run `uv sync --group dev` before checking version alignment.",
                pytrace=False,
            )

        assert installed_version == product_version, (
            "Installed Haute distribution metadata is stale relative to pyproject.toml; "
            "run `uv sync --group dev`."
        )
        assert __version__ == installed_version
        assert app.version == installed_version

    def test_all_api_routes_declare_pydantic_response_models(self) -> None:
        from haute.server import app

        def is_pydantic_response(model: object) -> bool:
            if isinstance(model, type) and issubclass(model, BaseModel):
                return True
            if get_origin(model) is list:
                return all(is_pydantic_response(arg) for arg in get_args(model))
            return False

        def is_declared_stream(route: APIRoute) -> bool:
            """A route that explicitly declares a streaming transport.

            SSE/streaming responses carry no JSON body to model; their wire
            contract lives in the per-event schema union instead (the assistant
            message stream is the first such route).  The exemption requires
            the EXPLICIT ``response_class=StreamingResponse`` declaration —
            an undeclared route still fails this contract.
            """

            from fastapi.responses import StreamingResponse

            response_class = getattr(route, "response_class", None)
            actual = getattr(response_class, "value", response_class)
            return isinstance(actual, type) and issubclass(actual, StreamingResponse)

        offenders = sorted(
            f"{','.join(route.methods or [])} {route.path}: {route.response_model!r}"
            for route in app.routes
            if isinstance(route, APIRoute)
            and route.path.startswith("/api/")
            and not is_pydantic_response(route.response_model)
            and not is_declared_stream(route)
        )

        assert offenders == []


class TestLocalToolingContracts:
    def test_pre_commit_config_avoids_platform_shell_wrappers(self) -> None:
        config = (Path(__file__).resolve().parents[1] / ".pre-commit-config.yaml").read_text(
            encoding="utf-8"
        )

        assert "bash -c" not in config
        assert "cd frontend" not in config
        assert "npm --prefix frontend run typecheck" in config
        assert "npm --prefix frontend run lint" in config


class TestTestFileNamingContracts:
    def test_test_paths_describe_contracts_not_review_phases(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        roots = (repo_root / "tests", repo_root / "frontend" / "src")
        stage_name = re.compile(r"(^|[\\/_.-])(phase|wave)\d+", re.IGNORECASE)

        offenders = sorted(
            str(path.relative_to(repo_root))
            for root in roots
            for path in source_files(root, suffix=None)
            if path.suffix in {".py", ".ts", ".tsx"}
            and stage_name.search(str(path.relative_to(repo_root)))
        )

        assert offenders == []


class TestContainerSmokeWorkflowContract:
    def test_container_smoke_workflow_matches_operational_contracts(self) -> None:
        import yaml

        workflow_path = (
            Path(__file__).resolve().parents[1] / ".github" / "workflows" / "container-smoke.yml"
        )
        data = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))

        # YAML 1.1 parses unquoted 'on' as boolean True
        on_section = data.get(True) or data.get("on") or {}

        # Cron schedule (Mondays 04:23 UTC)
        schedules = on_section.get("schedule", [])
        cron_exprs = [s.get("cron") for s in schedules]
        assert "23 4 * * 1" in cron_exprs

        # force_fail dispatch input
        dispatch_inputs = on_section.get("workflow_dispatch", {}).get("inputs", {})
        assert "force_fail" in dispatch_inputs
        assert dispatch_inputs["force_fail"].get("type") == "boolean"

        # Steps in container-smoke job
        steps = data.get("jobs", {}).get("container-smoke", {}).get("steps", [])
        run_commands = [step.get("run", "") for step in steps if "run" in step]

        # Wheel build step
        assert any("HAUTE_BUILD_FRONTEND=1 uv build --wheel" in cmd for cmd in run_commands)

        # Serve-check step
        assert any(
            "scripts/container_smoke.py" in cmd and "--serve-check" in cmd for cmd in run_commands
        )

        # Docker build and run steps
        assert any("docker build" in cmd for cmd in run_commands)
        assert any("docker run" in cmd for cmd in run_commands)

        # Alarm step's label
        alarm_step = next(
            (step for step in steps if "container-watch" in step.get("run", "")),
            None,
        )
        assert alarm_step is not None, "container-watch alarm step missing"
        assert "container-watch" in alarm_step["run"]


def _workflow(name: str) -> dict[str, Any]:
    import yaml

    path = Path(__file__).resolve().parents[1] / ".github" / "workflows" / name
    data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data


def _step_running(steps: list[dict[str, Any]], command: str) -> dict[str, Any]:
    (step,) = [step for step in steps if command in step.get("run", "")]
    return step


class TestWatchLaneIssueContract:
    """A watch lane's issue says whether the lane is red now, not whether it ever was.

    The locked advisory audit's issue stayed open after its fix, so the next, unrelated
    advisory would have landed as "Still red" on it. A deterministic lane's next green
    run on ``main`` closes its issue; a probabilistic lane's cannot.
    """

    def test_a_deterministic_lane_closes_its_issue_when_it_passes_on_main(self) -> None:
        jobs = _workflow("dependencies.yml")["jobs"]

        all_clear = jobs["locked-advisory-all-clear"]
        assert all_clear["needs"] == "locked-advisory-audit"
        condition = " ".join(all_clear["if"].split())
        assert "needs.locked-advisory-audit.result == 'success'" in condition
        assert "github.event_name != 'pull_request'" in condition
        assert "github.ref == 'refs/heads/main'" in condition
        assert all_clear["permissions"]["issues"] == "write"
        (step,) = all_clear["steps"]
        # No checkout, so gh needs the repository named (as the alarm job does).
        assert step["env"]["GH_REPO"] == "${{ github.repository }}"
        assert 'title="Locked dependency advisory audit is red"' in step["run"]
        assert "--label dependency-watch" in step["run"]
        assert 'gh issue close "${existing}"' in step["run"]

        for job, steps, title, label in (
            (
                "unlocked-resolve",
                jobs["unlocked-resolve"]["steps"],
                "Scheduled unlocked-resolve lane is red",
                "dependency-watch",
            ),
            (
                "container-smoke",
                _workflow("container-smoke.yml")["jobs"]["container-smoke"]["steps"],
                "Weekly container-smoke lane is red",
                "container-watch",
            ),
        ):
            stand_down = _step_running(steps, "gh issue close")
            assert stand_down["if"] == "success() && github.ref == 'refs/heads/main'", job
            assert f'title="{title}"' in stand_down["run"], job
            assert f"--label {label}" in stand_down["run"], job

    @pytest.mark.parametrize("workflow", ["frontend-shuffle.yml", "property-exploration.yml"])
    def test_a_probabilistic_lane_leaves_its_issue_to_the_fix(self, workflow: str) -> None:
        """One passing seed or exploration budget does not prove the failure gone."""
        steps = [step for job in _workflow(workflow)["jobs"].values() for step in job["steps"]]

        assert not any("gh issue close" in step.get("run", "") for step in steps)
