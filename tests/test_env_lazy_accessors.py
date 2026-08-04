"""Regression tests for lazy environment-variable accessors.

Defect class (Maginot: import-time environment capture): timeout/limit knobs
used to be frozen into module-level constants at import, so an override set
*after* the module was imported — the common case for a programmatic server
start or a pytest ``monkeypatch.setenv`` — was silently ignored, and a
malformed value crashed module import instead of the request.

The fix (mirroring PR #64's ``HAUTE_MEM_LOG`` treatment) resolves each knob per
call via ``haute._env``. Every test below sets the env var AFTER the module is
imported and proves the new value takes effect. Explicitly invalid configuration
fails loudly so it cannot silently disable a safety limit.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from haute import _env


class TestEnvHelpers:
    """The shared parse-with-fallback helpers in ``haute._env``."""

    def test_float_env_unset_returns_default(self, monkeypatch):
        monkeypatch.delenv("HAUTE_TEST_KNOB", raising=False)
        assert _env.float_env("HAUTE_TEST_KNOB", 12.5) == 12.5

    def test_float_env_reads_value(self, monkeypatch):
        monkeypatch.setenv("HAUTE_TEST_KNOB", "3.5")
        assert _env.float_env("HAUTE_TEST_KNOB", 12.5) == 3.5

    def test_float_env_malformed_fails_loudly(self, monkeypatch):
        monkeypatch.setenv("HAUTE_TEST_KNOB", "not-a-float")
        with pytest.raises(RuntimeError, match="HAUTE_TEST_KNOB must be a finite number"):
            _env.float_env("HAUTE_TEST_KNOB", 12.5)

    @pytest.mark.parametrize("raw", ["nan", "inf", "-inf", "0", "-0.1"])
    def test_float_env_rejects_non_finite_or_non_positive_values(self, monkeypatch, raw):
        monkeypatch.setenv("HAUTE_TEST_KNOB", raw)
        with pytest.raises(
            RuntimeError, match="HAUTE_TEST_KNOB must be a finite number greater than 0"
        ):
            _env.float_env("HAUTE_TEST_KNOB", 12.5)

    def test_int_env_unset_returns_default(self, monkeypatch):
        monkeypatch.delenv("HAUTE_TEST_KNOB", raising=False)
        assert _env.int_env("HAUTE_TEST_KNOB", 42) == 42

    def test_int_env_reads_value(self, monkeypatch):
        monkeypatch.setenv("HAUTE_TEST_KNOB", "7")
        assert _env.int_env("HAUTE_TEST_KNOB", 42) == 7

    def test_int_env_malformed_fails_loudly(self, monkeypatch):
        monkeypatch.setenv("HAUTE_TEST_KNOB", "3.5")
        with pytest.raises(RuntimeError, match="HAUTE_TEST_KNOB must be a positive integer"):
            _env.int_env("HAUTE_TEST_KNOB", 42)

    @pytest.mark.parametrize("raw", ["0", "-1"])
    def test_int_env_rejects_non_positive_values(self, monkeypatch, raw):
        monkeypatch.setenv("HAUTE_TEST_KNOB", raw)
        with pytest.raises(RuntimeError, match="HAUTE_TEST_KNOB must be a positive integer"):
            _env.int_env("HAUTE_TEST_KNOB", 42)

    def test_optional_int_env_unset_returns_none(self, monkeypatch):
        monkeypatch.delenv("HAUTE_TEST_KNOB", raising=False)
        assert _env.optional_int_env("HAUTE_TEST_KNOB") is None

    def test_optional_int_env_empty_fails_loudly(self, monkeypatch):
        monkeypatch.setenv("HAUTE_TEST_KNOB", "")
        with pytest.raises(RuntimeError, match="HAUTE_TEST_KNOB must be a positive integer"):
            _env.optional_int_env("HAUTE_TEST_KNOB")

    def test_optional_int_env_reads_value(self, monkeypatch):
        monkeypatch.setenv("HAUTE_TEST_KNOB", "99")
        assert _env.optional_int_env("HAUTE_TEST_KNOB") == 99

    @pytest.mark.parametrize("raw", ["not-an-int", "0", "-1"])
    def test_optional_int_env_invalid_value_fails_loudly(self, monkeypatch, raw):
        monkeypatch.setenv("HAUTE_TEST_KNOB", raw)
        with pytest.raises(RuntimeError, match="HAUTE_TEST_KNOB must be a positive integer"):
            _env.optional_int_env("HAUTE_TEST_KNOB")


# (accessor, env var, override string, expected parsed value, default) for every
# knob that used to be captured at import. Setting the env var AFTER import must
# change the accessor's result — that is exactly the frozen-constant regression.
_ACCESSOR_CASES = [
    ("haute.routes.pipeline", "_trace_timeout", "HAUTE_TRACE_TIMEOUT", "5", 5.0, 120.0),
    ("haute.routes.pipeline", "_preview_timeout", "HAUTE_PREVIEW_TIMEOUT", "5", 5.0, 120.0),
    ("haute.routes.pipeline", "_sink_timeout", "HAUTE_SINK_TIMEOUT", "5", 5.0, 300.0),
    ("haute.routes.json_cache", "_build_timeout", "HAUTE_BUILD_TIMEOUT", "5", 5.0, 1800.0),
    (
        "haute.routes.output_assemble",
        "_dry_run_timeout",
        "HAUTE_OUTPUT_DRY_RUN_TIMEOUT",
        "5",
        5.0,
        120.0,
    ),
    ("haute.routes.input_cache", "_build_timeout", "HAUTE_BUILD_TIMEOUT", "5", 5.0, 1800.0),
    (
        "haute.routes.input_cache",
        "_max_concurrent_builds",
        "HAUTE_INPUT_CACHE_MAX_CONCURRENT_BUILDS",
        "5",
        5,
        4,
    ),
    (
        "haute.routes._optimiser_service",
        "_default_auto_range_timeout",
        "HAUTE_AUTO_RANGE_TIMEOUT",
        "60",
        60,
        1800,
    ),
    (
        "haute.routes._optimiser_service",
        "_default_auto_range_chunk_size",
        "HAUTE_AUTO_RANGE_CHUNK_SIZE",
        "111",
        111,
        2_000_000,
    ),
    (
        "haute.routes._optimiser_service",
        "_default_auto_range_partitions",
        "HAUTE_AUTO_RANGE_PARTITIONS",
        "8",
        8,
        16,
    ),
    (
        "haute.routes._train_service",
        "_default_train_timeout",
        "HAUTE_TRAIN_TIMEOUT",
        "60",
        60,
        3600,
    ),
    (
        "haute.routes._train_service",
        "_max_train_loss_history",
        "HAUTE_TRAIN_LOSS_HISTORY_LIMIT",
        "10",
        10,
        200,
    ),
]


def _resolve(module_name: str, accessor: str):
    import importlib

    module = importlib.import_module(module_name)
    return getattr(module, accessor)


@pytest.mark.parametrize(
    ("module_name", "accessor", "env_var", "override", "expected", "default"),
    _ACCESSOR_CASES,
    ids=[f"{m.rsplit('.', 1)[-1]}.{a}" for m, a, *_ in _ACCESSOR_CASES],
)
def test_accessor_override_takes_effect_after_import(
    module_name, accessor, env_var, override, expected, default, monkeypatch
):
    """An env override set after import is honoured (was frozen before)."""
    fn = _resolve(module_name, accessor)
    monkeypatch.delenv(env_var, raising=False)
    assert fn() == default
    monkeypatch.setenv(env_var, override)
    assert fn() == expected


@pytest.mark.parametrize(
    ("module_name", "accessor", "env_var", "override", "expected", "default"),
    _ACCESSOR_CASES,
    ids=[f"{m.rsplit('.', 1)[-1]}.{a}" for m, a, *_ in _ACCESSOR_CASES],
)
def test_accessor_malformed_value_fails_loudly(
    module_name, accessor, env_var, override, expected, default, monkeypatch
):
    """A malformed explicit override is a configuration error."""
    fn = _resolve(module_name, accessor)
    monkeypatch.setenv(env_var, "not-a-number")
    with pytest.raises(RuntimeError, match=env_var):
        fn()


def test_solver_timeout_optional_semantics(monkeypatch):
    """The optional timeout is absent by default and strict when configured."""
    from haute.routes import _optimiser_service as opt

    monkeypatch.delenv("HAUTE_SOLVER_TIMEOUT", raising=False)
    assert opt._default_solver_timeout() is None
    monkeypatch.setenv("HAUTE_SOLVER_TIMEOUT", "42")
    assert opt._default_solver_timeout() == 42
    # Malformed must not silently remove the timeout.
    monkeypatch.setenv("HAUTE_SOLVER_TIMEOUT", "not-an-int")
    with pytest.raises(RuntimeError, match="HAUTE_SOLVER_TIMEOUT.*positive integer"):
        opt._default_solver_timeout()


@pytest.mark.parametrize(
    ("module_name", "accessor", "env_var"),
    [
        ("haute.routes.json_cache", "_build_timeout", "HAUTE_BUILD_TIMEOUT"),
        ("haute.routes.input_cache", "_build_timeout", "HAUTE_BUILD_TIMEOUT"),
    ],
)
@pytest.mark.parametrize("raw", ["0", "-1", "nan", "inf"])
def test_build_timeout_has_one_positive_finite_policy(
    module_name, accessor, env_var, raw, monkeypatch
):
    fn = _resolve(module_name, accessor)
    monkeypatch.setenv(env_var, raw)
    with pytest.raises(RuntimeError, match=env_var):
        fn()


@pytest.mark.parametrize(
    ("module_name", "accessor"),
    [
        ("haute.routes.json_cache", "_build_timeout"),
        ("haute.routes.input_cache", "_build_timeout"),
    ],
)
def test_build_timeout_accepts_positive_values_below_old_clamp(module_name, accessor, monkeypatch):
    fn = _resolve(module_name, accessor)
    monkeypatch.setenv("HAUTE_BUILD_TIMEOUT", "0.0005")
    assert fn() == 0.0005


def test_auto_range_context_default_reflects_env(monkeypatch):
    """The frozen dataclass default is a ``default_factory``, so a per-test
    env override reaches ``FrontierAutoRangeContext()`` — proving the fix also
    covers the dataclass-default capture, not just the direct accessor call.
    """
    from haute.routes._optimiser_service import FrontierAutoRangeContext

    monkeypatch.setenv("HAUTE_AUTO_RANGE_CHUNK_SIZE", "333")
    monkeypatch.setenv("HAUTE_AUTO_RANGE_PARTITIONS", "9")
    ctx = FrontierAutoRangeContext()
    assert ctx.chunk_size == 333
    assert ctx.partition_count == 9


DirectEnvRead = tuple[str, str, str, str]


class _DirectEnvReadVisitor(ast.NodeVisitor):
    """Find literal direct reads through ``os`` without following aliases to mappings."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.reads: set[DirectEnvRead] = set()
        self._scopes: list[str] = ["<module>"]
        self._constants: list[dict[str, str]] = [{}]
        self._os_aliases: list[set[str]] = [set()]
        self._getenv_aliases: list[set[str]] = [set()]
        self._environ_aliases: list[set[str]] = [set()]

    @property
    def _scope(self) -> str:
        return ".".join(self._scopes)

    def _push_scope(self, name: str) -> None:
        self._scopes.append(name)
        self._constants.append({})
        self._os_aliases.append(set())
        self._getenv_aliases.append(set())
        self._environ_aliases.append(set())

    def _pop_scope(self) -> None:
        self._scopes.pop()
        self._constants.pop()
        self._os_aliases.pop()
        self._getenv_aliases.pop()
        self._environ_aliases.pop()

    def _known(self, stacks: list[set[str]], name: str) -> bool:
        return any(name in values for values in reversed(stacks))

    def _key(self, node: ast.expr) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.Name):
            for constants in reversed(self._constants):
                if node.id in constants:
                    return constants[node.id]
        return None

    def _record(self, key_node: ast.expr, form: str) -> None:
        key = self._key(key_node)
        if key is not None:
            self.reads.add((self.path, self._scope, key, form))

    def _is_environ(self, node: ast.expr) -> bool:
        return (
            isinstance(node, ast.Name)
            and self._known(self._environ_aliases, node.id)
            or isinstance(node, ast.Attribute)
            and node.attr == "environ"
            and isinstance(node.value, ast.Name)
            and self._known(self._os_aliases, node.value.id)
        )

    def visit_Import(self, node: ast.Import) -> None:
        for imported in node.names:
            if imported.name == "os":
                self._os_aliases[-1].add(imported.asname or "os")

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module == "os":
            for imported in node.names:
                if imported.name == "getenv":
                    self._getenv_aliases[-1].add(imported.asname or "getenv")
                elif imported.name == "environ":
                    self._environ_aliases[-1].add(imported.asname or "environ")

    def visit_Assign(self, node: ast.Assign) -> None:
        self.generic_visit(node)
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self._constants[-1][target.id] = node.value.value

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self.generic_visit(node)
        if (
            isinstance(node.target, ast.Name)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            self._constants[-1][node.target.id] = node.value.value

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._push_scope(node.name)
        self.generic_visit(node)
        self._pop_scope()

    visit_AsyncFunctionDef = visit_FunctionDef  # noqa: N815 - ast.NodeVisitor hook

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._push_scope(node.name)
        self.generic_visit(node)
        self._pop_scope()

    def visit_Call(self, node: ast.Call) -> None:
        if node.args:
            if (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "getenv"
                and isinstance(node.func.value, ast.Name)
                and self._known(self._os_aliases, node.func.value.id)
            ):
                self._record(node.args[0], "os.getenv")
            elif isinstance(node.func, ast.Name) and self._known(
                self._getenv_aliases, node.func.id
            ):
                self._record(node.args[0], "os.getenv")
            elif (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "get"
                and self._is_environ(node.func.value)
            ):
                self._record(node.args[0], "os.environ.get")
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        if isinstance(node.ctx, ast.Load) and self._is_environ(node.value):
            self._record(node.slice, "os.environ[]")
        self.generic_visit(node)


def _direct_env_reads(source: str, path: str = "synthetic.py") -> set[DirectEnvRead]:
    visitor = _DirectEnvReadVisitor(path)
    visitor.visit(ast.parse(source))
    return visitor.reads


def test_direct_env_read_visitor_finds_aliases_constants_and_subscript_loads():
    source = """
import os as operating_system
from os import getenv as environment_value
KEY = "HAUTE_CONSTANT_KEY"

def read():
    local_key = "HAUTE_LOCAL_KEY"
    return (
        operating_system.getenv(KEY),
        environment_value(local_key),
        operating_system.environ["HAUTE_SUBSCRIPT_KEY"],
    )
"""
    assert _direct_env_reads(source) == {
        ("synthetic.py", "<module>.read", "HAUTE_CONSTANT_KEY", "os.getenv"),
        ("synthetic.py", "<module>.read", "HAUTE_LOCAL_KEY", "os.getenv"),
        ("synthetic.py", "<module>.read", "HAUTE_SUBSCRIPT_KEY", "os.environ[]"),
    }


def test_direct_env_read_visitor_ignores_writes_and_dynamic_keys():
    source = """
import os
key = make_key()
os.environ["HAUTE_WRITE_ONLY"] = "value"
value = os.environ[key]
other = os.environ.get(key)
"""
    assert _direct_env_reads(source) == set()


# Every direct production read must be reviewed here. Numeric tuning knobs belong
# in haute._env; these exceptions are strings, booleans, credentials, mappings,
# or custom non-negative/readiness policies with deliberately different semantics.
_REVIEWED_DIRECT_ENV_READS: set[DirectEnvRead] = {
    # Credentials and external integration endpoints.
    (
        "src/haute/_databricks_io.py",
        "<module>._connection_settings",
        "DATABRICKS_HOST",
        "os.getenv",
    ),
    (
        "src/haute/_databricks_io.py",
        "<module>._connection_settings",
        "DATABRICKS_TOKEN",
        "os.getenv",
    ),
    (
        "src/haute/_databricks_io.py",
        "<module>._connection_settings",
        "DATABRICKS_CLIENT_ID",
        "os.getenv",
    ),
    (
        "src/haute/_databricks_io.py",
        "<module>._connection_settings",
        "DATABRICKS_CLIENT_SECRET",
        "os.getenv",
    ),
    (
        "src/haute/deploy/_mlflow.py",
        "<module>._check_databricks_connectivity",
        "DATABRICKS_RATING_HOST",
        "os.environ.get",
    ),
    (
        "src/haute/deploy/_mlflow.py",
        "<module>._check_databricks_connectivity",
        "DATABRICKS_RATING_TOKEN",
        "os.environ.get",
    ),
    (
        "src/haute/deploy/_mlflow.py",
        "<module>._create_or_update_serving_endpoint",
        "DATABRICKS_RATING_HOST",
        "os.environ.get",
    ),
    (
        "src/haute/deploy/_mlflow.py",
        "<module>._create_or_update_serving_endpoint",
        "DATABRICKS_RATING_TOKEN",
        "os.environ.get",
    ),
    (
        "src/haute/modelling/_mlflow_log.py",
        "<module>.build_run_url",
        "DATABRICKS_HOST",
        "os.getenv",
    ),
    (
        "src/haute/modelling/_mlflow_log.py",
        "<module>.resolve_tracking_backend",
        "DATABRICKS_HOST",
        "os.getenv",
    ),
    (
        "src/haute/modelling/_mlflow_log.py",
        "<module>.resolve_tracking_backend",
        "DATABRICKS_TOKEN",
        "os.getenv",
    ),
    (
        "src/haute/routes/databricks.py",
        "<module>._get_databricks_client",
        "DATABRICKS_HOST",
        "os.getenv",
    ),
    (
        "src/haute/routes/databricks.py",
        "<module>._get_databricks_client",
        "DATABRICKS_TOKEN",
        "os.getenv",
    ),
    (
        "src/haute/routes/databricks.py",
        "<module>._get_databricks_client",
        "DATABRICKS_CLIENT_ID",
        "os.getenv",
    ),
    (
        "src/haute/routes/databricks.py",
        "<module>._get_databricks_client",
        "DATABRICKS_CLIENT_SECRET",
        "os.getenv",
    ),
    # Hosted durable storage: deployment identity and credential locations,
    # each read per call so a container can be reconfigured without a rebuild.
    (
        "src/haute/_project_storage.py",
        "<module>.state_volume_configured",
        "HAUTE_STATE_VOLUME",
        "os.environ.get",
    ),
    (
        "src/haute/_project_storage.py",
        "<module>._state_volume_root",
        "HAUTE_STATE_VOLUME",
        "os.environ.get",
    ),
    (
        "src/haute/_project_storage.py",
        "<module>._app_name",
        "DATABRICKS_APP_NAME",
        "os.environ.get",
    ),
    (
        "src/haute/_project_storage.py",
        "<module>.resolve_project_dir",
        "HAUTE_PROJECT_DIR",
        "os.environ.get",
    ),
    (
        "src/haute/_project_storage.py",
        "<module>.configure_git_credentials",
        "HAUTE_GIT_TOKEN",
        "os.environ.get",
    ),
    (
        "src/haute/_project_storage.py",
        "<module>._assert_credential_may_reach",
        "HAUTE_GIT_TOKEN",
        "os.environ.get",
    ),
    (
        "src/haute/_project_storage.py",
        "<module>._allowed_hosts",
        "HAUTE_GIT_ALLOWED_HOSTS",
        "os.environ.get",
    ),
    ("src/haute/routes/modelling.py", "<module>.mlflow_check", "DATABRICKS_HOST", "os.getenv"),
    # String, boolean, mapping, or custom validation semantics.
    (
        "src/haute/_execution_admission.py",
        "<module>._memory_policy_name",
        "HAUTE_EXECUTION_MEMORY_POLICY",
        "os.environ.get",
    ),
    (
        "src/haute/_execution_context.py",
        "<module>._execution_telemetry_enabled",
        "HAUTE_EXECUTION_TELEMETRY",
        "os.environ.get",
    ),
    (
        "src/haute/_git.py",
        "<module>._protected_branches",
        "HAUTE_PROTECTED_BRANCHES",
        "os.environ.get",
    ),
    (
        "src/haute/_local_security.py",
        "<module>._configured_local_hosts",
        "HAUTE_TRUSTED_HOSTS",
        "os.environ.get",
    ),
    (
        "src/haute/_local_security.py",
        "<module>.ensure_local_session_token_env",
        "HAUTE_LOCAL_SESSION_TOKEN",
        "os.environ.get",
    ),
    (
        "src/haute/_local_security.py",
        "<module>.local_session_auth_disabled",
        "HAUTE_DISABLE_LOCAL_SESSION_AUTH",
        "os.environ.get",
    ),
    (
        "src/haute/_local_security.py",
        "<module>.local_session_token",
        "HAUTE_LOCAL_SESSION_TOKEN",
        "os.environ.get",
    ),
    ("src/haute/_logging.py", "<module>.configure_logging", "HAUTE_LOG_FORMAT", "os.environ.get"),
    ("src/haute/_logging.py", "<module>.configure_logging", "HAUTE_LOG_LEVEL", "os.environ.get"),
    (
        "src/haute/_worker_isolation.py",
        "<module>.resolve_worker_memory_enforcement",
        "HAUTE_WORKER_MEMORY_ENFORCEMENT",
        "os.environ.get",
    ),
    (
        "src/haute/assistant/_config.py",
        "<module>._max_output_tokens",
        "HAUTE_ASSISTANT_MAX_OUTPUT_TOKENS",
        "os.getenv",
    ),
    (
        "src/haute/cli/_helpers.py",
        "<module>.resolve_model_name",
        "HAUTE_MODEL_NAME",
        "os.environ.get",
    ),
    (
        "src/haute/modelling/_algorithms.py",
        "<module>._mem_log_path",
        "HAUTE_MEM_LOG",
        "os.environ.get",
    ),
    (
        "src/haute/routes/_optimiser_service.py",
        "<module>._artifact_stale_seconds",
        "HAUTE_ARTIFACT_STALE_SECONDS",
        "os.environ.get",
    ),
    # GitHub's supplied output-file path is an external integration string.
    ("src/haute/cli/_impact.py", "<module>.handle_impact", "GITHUB_STEP_SUMMARY", "os.environ.get"),
}


def test_production_direct_environment_reads_match_reviewed_exceptions():
    root = Path(__file__).parents[1]
    discovered: set[DirectEnvRead] = set()
    for source_path in (root / "src" / "haute").rglob("*.py"):
        if source_path.name == "_env.py":
            continue
        relative_path = source_path.relative_to(root).as_posix()
        discovered.update(_direct_env_reads(source_path.read_text(encoding="utf-8"), relative_path))
    assert discovered == _REVIEWED_DIRECT_ENV_READS
