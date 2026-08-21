"""Guardrails for skip/xfail/importorskip test debt.

These tests are deliberately strict. A skipped or expected-failing test can be
useful, but it is also invisible risk unless it has a reason, an explicit
review budget, and a ratchet that prevents casual growth.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path

TESTS_DIR = Path(__file__).parent
REPO_ROOT = TESTS_DIR.parent

_CALL_DEBT_TARGETS = {
    "pytest.importorskip",
    "pytest.skip",
    "pytest.xfail",
}
_MARK_DEBT_TARGETS = {
    "pytest.mark.flaky",
    "pytest.mark.skip",
    "pytest.mark.skipif",
    "pytest.mark.xfail",
}
_DEBT_TARGETS = _CALL_DEBT_TARGETS | _MARK_DEBT_TARGETS

_TEST_HEALTH_SUMMARY_PATH = TESTS_DIR / "test-health-summary.md"
_MUTATION_TARGETS_PATH = REPO_ROOT / "mutation" / "targets.json"
_PLAYWRIGHT_CI_RETRY_BUDGET = 2

# Exact debt-site fingerprints as of the test-suite review. The fingerprint uses
# path, enclosing scope, debt kind, reason text, and normalized AST source. A new
# skip/xfail/importorskip, or a changed reason, must be accepted deliberately.
_EXPECTED_DEBT_IDS = {
    # Offset-through-serving suite — every test trains a real model, so each
    # leg importorskips its optional modelling engine (rustystats for the GLM
    # legs, catboost for the boosted / numeric-only legs), matching the
    # existing modelling suites' posture. See tests/test_offset_scoring.py.
    "0a4860e95cf0f3b6",
    "22a55d6a8665b087",
    "3a392e6541a16437",
    "3ab16f01c706b8cf",
    "4908d9a546bf0eb3",
    "53ef46f50a3e8e3c",
    "78ccd0efb0b58c07",
    "7ecc4c9aac18c56a",
    "8575a86d12fcd9fd",
    "8ad195689440ad84",
    "8fc522e44d27d619",
    "9f129f4b22a3f0a6",
    "a9db81806e754356",
    "b809feae1b81044a",
    "d678759483265dfe",
    "dc9bc31593b95505",
    "e2d91359c364b2e7",
    "f0497dbc6c3e3f81",
    "f3ec6eddbd8aa75a",
    # Data In/Out first pass — engine-gated legs. The dataInput/dataOutput
    # node types cover the full native polars width, but this pass ships zero
    # new runtime dependencies, so the excel/database/delta round-trip legs
    # importorskip their engine packages (they go live with the extras
    # tranche), and the engine-ABSENCE error-message pins conversely skip in
    # environments where an engine happens to be installed. See
    # tests/test_data_io_roundtrips.py, tests/test_data_io_nodes.py,
    # tests/test_polars_io_registry.py.
    "3b8e7f4c44b25931",
    "7008d3c3226e5b2f",
    "9f3ea940c8dfc2b4",
    "ad4157172134c634",
    "f0b346cafaa361b3",
    "ff0e859f39674bb2",
    # Invariants battery — the sanitizer parity drift gate asserts a fixture
    # that lives in frontend/; outside a repo checkout (installed-package test
    # runs) the frontend tree, and therefore the parity contract, does not
    # exist. In-repo CI always runs it. See
    # tests/test_sanitize_parity_fixture.py::_require_fixture.
    "5eb0998a88b3cbb7",
    # Invariants battery — coexisting case-twin files (Foo.csv + foo.csv) can
    # only be created on a case-sensitive filesystem, so the real-twin test
    # skips on macOS/Windows by physical necessity; the Linux CI legs run it,
    # and the spelling-mismatch test covers the case-insensitive direction.
    # See tests/test_path_case_audit.py::test_coexisting_case_twins_are_flagged.
    "b0ad28e0a98cbfa9",
    # Runtime containment — the symlink escape regression needs a real
    # directory symlink. Some Windows privilege configurations cannot create
    # one, while the Linux CI leg and developer-mode Windows run the assertion.
    # See test_data_input_nested_relative_path.py.
    "47f099585f35ab4e",
    # Deploy containment has the same platform prerequisite for local artifact
    # and pipeline-file escape tests. Linux CI and symlink-capable Windows
    # environments run both assertions.
    "6fb8a1d2d768c835",
    "a7c21a9dc1f1aada",
    # W2.9 — the trace-cache budget wiring assertion cannot hold when an
    # operator deliberately overrides HAUTE_TRACE_CACHE_MAX_BYTES; the skip
    # documents that the pin targets default wiring only. See
    # tests/test_trace_cache_byte_awareness.py::TestTraceCacheByteBudgetWiring.
    "73141b06a8fbbace",
    # API/WS 404-guard review — the SPA-serving regression test only applies when
    # a frontend build is present (serve_spa is registered under
    # ``if STATIC_DIR.exists()``), so it skips on backend-only CI where no build
    # exists. The unconditional 404 assertions carry the actual contract. See
    # tests/test_routes_error_handling.py::TestApiWsNotFoundReturnsJson
    # ::test_spa_still_served_for_non_api_routes.
    "3594921e8a8526c3",
    # Multi-frame review follow-up — the atomic-write reader-contention tests
    # are win32-specific by design: POSIX rename(2) succeeds under a concurrent
    # reader, while Windows MoveFileExW raises. Skipped on non-win32. See
    # tests/test_file_ops.py::TestAtomicWriteWindowsReaderContention.
    "08c3e550bc505052",
    "d0db18a1315ae001",
    # Bundle 5.M2 — atomic sidecar write means the readonly-DIR check
    # only fires on POSIX (Windows chmod differs); the test is skipped
    # on win32 by design. See TestPermissionDenied.
    "716bebf07ce4f9f3",
    "531fb2f9161337c8",
    # (test_windows_reserved_names_produce_paths's win32 skipif retired: the
    # test is now an all-platform predicate check, so its debt entry is gone.)
    "d23a55b468b6e518",
    "5efdb01a5c96e2ef",
    "bdc246f0c4c5f76e",
    "e7555cb715103f36",
    "b8ed76e2d8a5a137",
    "c87e80ef52f08568",
    "50a51dcce3b28cae",
    "fea0501479ba2a0e",
    "4328a90240a0dbba",
    "55b3c4a50777661d",
    "3c5baaf0a02d232d",
    "7644099cbe0b2599",
    "b999233846eb7ace",
    "02519ef042dc229b",
    "b1fdda1913c30cea",
    "c1d2feb4a8261f67",
    "a8ba935e2e69547b",
    "1c1a7efd1b6b5496",
    "bc580591bd05253a",
    "ea75218e22580bb4",
    "6f5878234dddd567",
    "df615adf6facd8fc",
    "46fd8404c50daff6",
    "c60a5a978c60725a",
    "4ae118442dafce1e",
    "8a0f7160d9044069",
    "a78fb6a12665d489",
    "3512c808a6273e35",
    "153ecf02f6848509",
    "b47ee7c16fbf5755",
    "9b58538ec2c90223",
    "6881417aa251afb7",
    "fb0e81ff682c42ba",
    "de69a025e2015f76",
    "c846306558bad02c",
    "9ca93c4280b5017c",
    "bfd8943e3c6770ee",
    "a6bc71691f1fd1db",
    "f68efc20cf708633",
    "68865fbae69e1d1c",
    "24327ebd107b8ae0",
    "2a634b625baa4350",
    "aec15b8d084c0f9f",
    "36389904372edffb",
    "40f0104677c0d566",
    "454198ff8535ff31",
    "4ba277207894ee6d",
    "848a5576a63f17d0",
    "df789ca110c56d8d",
    "e800d20c2fdb0d00",
    "f6ab12590998eb2c",
    # This test is now specifically address-space-cap gated because RSS watchdog
    # support exists cross-platform.
    "bd811d026e977f2c",
    # 4a.4 — the Poisson/Tweedie CatBoost SHAP space-reconciliation tests
    # train a real CatBoost model; catboost is an optional extra, so the
    # shared trainer helper importorskips it (same convention as every other
    # catboost site in tests/test_model_explainability.py). See
    # _train_catboost_link_loss_model.
    "cd960fb2eda832e3",
    # 4a.1/4a.6 — tests/test_mlflow_io_real_pyfunc.py builds REAL pyfunc
    # fixtures (the named-signature input contract cannot be proven with
    # mocks), so the module needs the real mlflow package (databricks
    # extra). The single module-level importorskip keeps a core-only
    # install skipping cleanly while the dev-group CI legs (mlflow
    # installed) execute every test.
    "e278858fa0b5e3b7",
    # 4b.8 — tests/test_mlflow_log_button_roundtrip.py proves the "Log to
    # MLflow" button's signature/artifact round-trip against a REAL local
    # file-store MLflow (a wrong signature only fails at genuine pyfunc
    # schema enforcement, which mocks cannot reproduce). Same single
    # module-level importorskip convention as test_mlflow_io_real_pyfunc.py.
    "c12bf51de96471f5",
    # W4b (4b.1/4b.2/4b.3) — real-GLM route/export/diagnostics tests train
    # actual rustystats models; rustystats is an optional extra, so the
    # tests importorskip it. See tests/test_train_param_routing.py and
    # tests/test_glm_integration.py::TestInferenceUnavailableDiagnostics.
    "560f4d4069c7b172",
    "6e4e489debab1b3f",
    "7b4fe4c7336c7b86",
    # W4b (4b.6/4b.9) — the temp-cleanup and per-model-contract suites fit
    # real CatBoost models (cancel/failure points inside genuine fits; the
    # two-runs-one-dir e2e); catboost is an optional extra. The pre-split
    # cancel test needs no skip (no catboost path) and carries none. See
    # tests/test_training_temp_cleanup.py and
    # tests/test_training_contract_per_model.py.
    "123ab384f9ef1fe8",
    "424aee6f3cb6d2c7",
    "b98bd1f0d20f0032",
    "e9cd0223c182cf3f",
    # 4a.3 — empty-batch classifier schema parity is pinned against genuine
    # CatBoost predictions for both integer and string labels. CatBoost is an
    # optional extra, so the real-engine parity test importorskips it while
    # the mock-backed scorer contract remains part of the core suite.
    "70f2346590cf6186",
    # W0 sandbox hardening — the RandomForest tree-ensemble round-trip widens
    # unpickle-allowlist coverage over a fitted sklearn model; sklearn is an
    # optional extra, so the test importorskips ``sklearn.ensemble`` (same
    # convention as the other optional-dependency sites). See
    # tests/test_sandbox.py::TestSafeJoblibLoad::test_fitted_random_forest_round_trips.
    "aaff93143a4007b3",
    # Windows symlink privilege — the config-escape guard test builds a
    # symlinked type folder to force ``.resolve()`` outside the config root,
    # but symlink creation needs a privilege Windows withholds by default
    # (WinError 1314). Skipped when symlink creation raises, mirroring the
    # other symlink-guard tests (e.g. test_files_routes, test_security_gaps).
    # See tests/test_config_io_gaps.py::TestConfigPathEscapeGuard
    # ::test_escape_guard_triggers_on_resolved_outside.
    "1e4116d06849b611",
    # Windows symlink privilege — the file-browser short-path regression test
    # builds a symlinked project dir so ``Path.cwd()`` differs from its
    # ``resolve()`` (the cross-platform stand-in for a Windows 8.3 short cwd),
    # but symlink creation needs a privilege Windows withholds by default
    # (WinError 1314). Skipped when symlink creation raises, mirroring the
    # other symlink-guard tests. See tests/test_files_routes.py
    # ::TestBrowseFilesUnresolvedCwd._symlinked_project.
    "e29ebc6050519fc5",
    # Assistant containment uses real directory/file symlinks to prove that
    # project-knowledge reads/cache writes and durable-session revival cannot
    # escape the project. Windows without Developer Mode or symlink privilege
    # cannot construct those fixtures; Linux CI and capable Windows hosts run
    # them. See test_assistant_project_knowledge.py and
    # test_assistant_session_persistence.py.
    "43dde5a20b2d64f6",
    "b5b34055c01d1d48",
    "d0922f4b9430bfba",
    # The hosted interpreter fix has a last-resort branch that reads
    # /proc/self/exe, which exists only on Linux. The primary path (resolve
    # before the working directory moves) is covered on every platform; this
    # one leg can only be exercised where that file exists.
    # See tests/test_worker_isolation.py.
    "e928b1daccb49a17",
    # macOS available-RAM probe — the Mach ``host_statistics64`` counters exist
    # only on darwin, so the unmocked-kernel assertion is darwin-gated. This is
    # the test that would have caught the original defect (darwin had no
    # working branch at all: no ``/proc/meminfo``, and ``SC_AVPHYS_PAGES`` is
    # absent from ``os.sysconf_names`` there), so it is deliberately a real
    # syscall rather than a mock. The branch's logic — free+inactive accounting,
    # purgeable exclusion, Mach port release, and every failure path — is
    # covered unconditionally on all platforms by the mocked tests in the same
    # class, which drive the real ctypes struct. See
    # tests/test_host_memory.py::TestAvailableRamMacOS
    # ::test_real_darwin_kernel_reports_available_memory.
    "23fb0fa7068e4340",
    # Polars snapshot contract on a deliberately unpinned resolve — the
    # committed I/O schema records one polars version, so exact snapshot
    # equality is unsatisfiable in the two lanes that resolve polars away from
    # the lockfile (unlocked-resolve, dependency-floors) and those lanes
    # declare themselves with HAUTE_POLARS_UNPINNED=1. Not a suppressed
    # failure: TestNoBreakingDriftAcrossTheCap asserts the contract that does
    # hold across the whole specifier in the same run, and the skip is gated on
    # the versions actually differing, so a declared lane that lands on the
    # recorded version still runs the full comparison. See
    # tests/test_polars_io_interface_contracts.py
    # ::TestCommittedSnapshotMatchesPinnedPolars
    # ::test_no_interface_drift_against_installed_polars.
    "ba1f02fb9964cdf3",
    # Verified JSON source/artifact proofs depend on a native filesystem
    # revision token (POSIX ctime or Windows USN), and one identity test also
    # needs hard-link support. Filesystems without those primitives must take
    # the deliberately conservative full-hash/copy path, so these tests skip
    # only the unavailable native fast-path assertion. Platform-independent
    # mocked witnesses in the same modules exercise every fallback, race, and
    # invalidation branch; Linux CI and capable Windows/NTFS hosts run the real
    # primitive assertions. Output and Explore publication also use real hard
    # links to prove the parent rejects aliased staging artifacts. See
    # test_json_shred_mut_validity.py, test_json_shred_runtime_snapshots.py,
    # test_data_io_nodes.py, and test_explore_routes.py.
    "0a5df3350d69e1f5",
    "0c7b2cb3effd87e0",
    "0ce7f0f48d149323",
    "0f0d9227e7f4a5ed",
    "1cc729c443a4270f",
    "20ec083bffcea5c5",
    "2c28fda4eed70866",
    "3f6505dcbcfac128",
    "513ab2404561fc80",
    "525220876e76a500",
    "7108885c10fdca44",
    "7e926940549a4f58",
    "7f618766970d6aa2",
    "8e42868e79ff264b",
    "894e7f67b861b301",
    "91878a6ff56e4c56",
    "bd96cc6f51a39c67",
    "bd9cf15e416714c0",
    "be4b790157583701",
    "c424c3f1d2cfe91a",
    "cc655b332f19c3a3",
    "fc936795d5ac2cdf",
}

_EXPECTED_NON_STRICT_XFAIL_IDS = {
    "9b58538ec2c90223",
}

_FRONTEND_TEST_ROOTS = (
    REPO_ROOT / "frontend" / "src",
    REPO_ROOT / "frontend" / "e2e",
)
_FRONTEND_TEST_SUFFIXES = {
    ".cjs",
    ".cts",
    ".js",
    ".jsx",
    ".mjs",
    ".mts",
    ".ts",
    ".tsx",
}
_FRONTEND_DEBT_BASE_PATTERN = re.compile(r"(?<![\w$.])(?P<base>test|describe|it)(?![\w$])")
_FRONTEND_IDENTIFIER_PATTERN = re.compile(r"[A-Za-z_$][\w$]*")
_FRONTEND_DEBT_METHODS = frozenset(
    {"fail", "fails", "fixme", "only", "runIf", "skip", "skipIf", "todo"}
)
_FRONTEND_UNRESOLVED_COMPUTED_MEMBER = "<computed>"
_FRONTEND_STATIC_COMPUTED_MEMBER = "<static-computed>"

# Frontend test skip/fixme/fail/only/conditional/todo sites are expected to be rare and
# reviewed.
# Keys are debt-site fingerprints; values are the reviewed reason for retaining
# the debt.
_EXPECTED_FRONTEND_DEBT_REASONS: dict[str, str] = {
    "bea80452a437e66c": (
        "Deliberate tripwire (PR #181 review adjudication): documents the "
        "self-consistent-foreign-Storage blind spot the storage canary cannot "
        "see — env.Storage is the same untrusted global as the instances, so "
        "the check proves jsdom provenance only while vitest's KEYS behaviour "
        "installs jsdom's Storage class. If vitest changes that, this "
        "it.fails flips to failing, which is the signal to redesign the "
        "provenance check."
    ),
}


@dataclass(frozen=True)
class _PytestAliases:
    module_names: frozenset[str]
    mark_names: frozenset[str]
    direct_names: dict[str, str]


@dataclass(frozen=True)
class _DebtSite:
    id: str
    path: Path
    line: int
    scope: str
    kind: str
    reason: str
    strict: bool | None
    reason_is_static: bool


@dataclass(frozen=True)
class _FrontendDebtSite:
    id: str
    path: Path
    line: int
    callee: str
    source: str


def _attr_path(node: ast.AST) -> str | None:
    parts: list[str] = []
    current: ast.AST | None = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
        return ".".join(reversed(parts))
    return None


def _canonical_pytest_path(node: ast.AST, aliases: _PytestAliases) -> str | None:
    if isinstance(node, ast.Name):
        return aliases.direct_names.get(node.id)

    raw_path = _attr_path(node)
    if raw_path is None:
        return None

    parts = raw_path.split(".")
    if parts[0] in aliases.module_names:
        return ".".join(("pytest", *parts[1:]))
    if parts[0] in aliases.mark_names:
        return ".".join(("pytest", "mark", *parts[1:]))
    if parts[0] in aliases.direct_names:
        direct = aliases.direct_names[parts[0]]
        return ".".join((direct, *parts[1:]))
    return raw_path


def _collect_pytest_aliases(tree: ast.AST) -> _PytestAliases:
    module_names = {"pytest"}
    mark_names: set[str] = set()
    direct_names: dict[str, str] = {}

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "pytest":
                    module_names.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module == "pytest":
            for alias in node.names:
                local_name = alias.asname or alias.name
                if alias.name == "mark":
                    mark_names.add(local_name)
                elif alias.name in {"skip", "xfail", "importorskip"}:
                    direct_names[local_name] = f"pytest.{alias.name}"

    return _PytestAliases(
        module_names=frozenset(module_names),
        mark_names=frozenset(mark_names),
        direct_names=direct_names,
    )


def _keyword(call: ast.Call, name: str) -> ast.AST | None:
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    return None


def _static_string_value(expr: ast.AST) -> str | None:
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        return expr.value
    if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Add):
        left = _static_string_value(expr.left)
        right = _static_string_value(expr.right)
        if left is not None and right is not None:
            return left + right
    return None


def _reason_expr(kind: str, call: ast.Call) -> ast.AST | None:
    keyword_reason = _keyword(call, "reason")
    if keyword_reason is not None:
        return keyword_reason
    if kind in {"pytest.skip", "pytest.xfail"} and call.args:
        return call.args[0]
    return None


def _strict_value(call: ast.Call) -> bool | None:
    strict = _keyword(call, "strict")
    if isinstance(strict, ast.Constant) and isinstance(strict.value, bool):
        return strict.value
    return None


def _has_non_empty_reason(kind: str, call: ast.Call) -> bool:
    expr = _reason_expr(kind, call)
    if expr is None:
        return False
    if isinstance(expr, ast.Constant):
        if expr.value is None:
            return False
        if isinstance(expr.value, str):
            return bool(expr.value.strip())
    return True


def _reason_text(kind: str, call: ast.Call) -> str:
    expr = _reason_expr(kind, call)
    if expr is None:
        return ""
    return _static_string_value(expr) or ast.unparse(expr)


def _reason_is_static_non_empty(kind: str, call: ast.Call) -> bool:
    expr = _reason_expr(kind, call)
    if expr is None:
        return False
    value = _static_string_value(expr)
    return value is not None and bool(value.strip())


def _normalized_source(node: ast.AST) -> str:
    return re.sub(r"\s+", " ", ast.unparse(node)).strip()


def _fingerprint(path: Path, scope: str, kind: str, reason: str, node: ast.AST) -> str:
    raw = "|".join(
        (
            path.as_posix(),
            scope,
            kind,
            reason,
            _normalized_source(node),
        )
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


class _DebtVisitor(ast.NodeVisitor):
    def __init__(self, path: Path) -> None:
        self.path = path
        self.scope: list[str] = []
        self.sites: list[_DebtSite] = []
        self.aliases = _PytestAliases(
            module_names=frozenset({"pytest"}),
            mark_names=frozenset(),
            direct_names={},
        )
        self._call_func_node_ids: set[int] = set()

    def scan(self, tree: ast.AST) -> list[_DebtSite]:
        self.aliases = _collect_pytest_aliases(tree)
        self.visit(tree)
        return sorted(self.sites, key=lambda site: (site.path.as_posix(), site.line, site.kind))

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_Call(self, node: ast.Call) -> None:
        self._call_func_node_ids.add(id(node.func))
        kind = _canonical_pytest_path(node.func, self.aliases)
        if kind in _DEBT_TARGETS:
            scope = ".".join(self.scope) or "<module>"
            reason = _reason_text(kind, node)
            strict = _strict_value(node) if kind == "pytest.mark.xfail" else None
            self.sites.append(
                _DebtSite(
                    id=_fingerprint(self.path, scope, kind, reason, node),
                    path=self.path,
                    line=node.lineno,
                    scope=scope,
                    kind=kind,
                    reason=reason,
                    strict=strict,
                    reason_is_static=_reason_is_static_non_empty(kind, node),
                )
            )
        self._record_dynamic_add_marker_debt(node)
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if id(node) in self._call_func_node_ids:
            self.generic_visit(node)
            return

        kind = _canonical_pytest_path(node, self.aliases)
        if kind in _MARK_DEBT_TARGETS:
            scope = ".".join(self.scope) or "<module>"
            self.sites.append(
                _DebtSite(
                    id=_fingerprint(self.path, scope, kind, "", node),
                    path=self.path,
                    line=node.lineno,
                    scope=scope,
                    kind=kind,
                    reason="",
                    strict=None,
                    reason_is_static=False,
                )
            )
        self.generic_visit(node)

    def _record_dynamic_add_marker_debt(self, node: ast.Call) -> None:
        call_path = _attr_path(node.func)
        if call_path is None or not call_path.endswith(".add_marker") or not node.args:
            return

        marker_arg = node.args[0]
        if not (
            isinstance(marker_arg, ast.Constant)
            and isinstance(marker_arg.value, str)
            and marker_arg.value in {"flaky", "skip", "skipif", "xfail"}
        ):
            return

        kind = f"pytest.mark.{marker_arg.value}"
        scope = ".".join(self.scope) or "<module>"
        self.sites.append(
            _DebtSite(
                id=_fingerprint(self.path, scope, kind, "", node),
                path=self.path,
                line=node.lineno,
                scope=scope,
                kind=kind,
                reason="",
                strict=None,
                reason_is_static=False,
            )
        )


def _scan_debt_sites() -> list[_DebtSite]:
    sites: list[_DebtSite] = []
    for path in sorted(TESTS_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        visitor = _DebtVisitor(path.relative_to(TESTS_DIR.parent))
        sites.extend(visitor.scan(tree))
    return sites


def _scan_source(source: str) -> list[_DebtSite]:
    tree = ast.parse(textwrap.dedent(source))
    return _DebtVisitor(Path("tests/example.py")).scan(tree)


def _format_site(site: _DebtSite) -> str:
    strict = "" if site.strict is None else f" strict={site.strict}"
    return (
        f"{site.id} {site.path.as_posix()}:{site.line} "
        f"{site.scope} {site.kind}{strict} reason={site.reason!r}"
    )


def _mask_frontend_comments_and_strings(source: str) -> str:
    result: list[str] = []
    index = 0
    state = "code"
    quote = ""

    while index < len(source):
        char = source[index]
        next_char = source[index + 1] if index + 1 < len(source) else ""

        if state == "code":
            if char == "/" and next_char == "/":
                result.extend((" ", " "))
                index += 2
                state = "line-comment"
                continue
            if char == "/" and next_char == "*":
                result.extend((" ", " "))
                index += 2
                state = "block-comment"
                continue
            if char in {"'", '"', "`"}:
                result.append(" ")
                index += 1
                state = "string"
                quote = char
                continue
            result.append(char)
            index += 1
            continue

        if state == "line-comment":
            result.append("\n" if char == "\n" else " ")
            index += 1
            if char == "\n":
                state = "code"
            continue

        if state == "block-comment":
            if char == "*" and next_char == "/":
                result.extend((" ", " "))
                index += 2
                state = "code"
                continue
            result.append("\n" if char == "\n" else " ")
            index += 1
            continue

        if state == "string":
            if char == "\\":
                result.append(" ")
                if next_char:
                    result.append("\n" if next_char == "\n" else " ")
                    index += 2
                else:
                    index += 1
                continue
            result.append("\n" if char == "\n" else " ")
            index += 1
            if char == quote:
                state = "code"
            continue

    return "".join(result)


def _skip_frontend_whitespace(source: str, index: int) -> int:
    while index < len(source) and source[index].isspace():
        index += 1
    return index


def _read_frontend_identifier(source: str, index: int) -> tuple[str, int] | None:
    match = _FRONTEND_IDENTIFIER_PATTERN.match(source, index)
    if match is None:
        return None
    return match.group(0), match.end()


def _skip_frontend_balanced_call(source: str, index: int) -> int:
    pairs = {"(": ")", "[": "]", "{": "}"}
    closers = set(pairs.values())
    stack = [")"]
    index += 1

    while index < len(source) and stack:
        char = source[index]
        if char in pairs:
            stack.append(pairs[char])
        elif char in closers and char == stack[-1]:
            stack.pop()
        index += 1

    return index


def _frontend_computed_property_end(masked: str, index: int) -> int | None:
    pairs = {"(": ")", "[": "]", "{": "}"}
    closers = set(pairs.values())
    stack = ["]"]
    cursor = index + 1

    while cursor < len(masked) and stack:
        char = masked[cursor]
        if char in pairs:
            stack.append(pairs[char])
        elif char in closers:
            if char != stack[-1]:
                return None
            stack.pop()
        cursor += 1

    if stack:
        return None
    return cursor


def _parse_frontend_string_member(expression: str) -> tuple[str, bool] | None:
    stripped = expression.strip()
    if not stripped or stripped[0] not in {"'", '"', "`"}:
        return None

    quote = stripped[0]
    cursor = 1
    value: list[str] = []
    has_template_interpolation = False
    while cursor < len(stripped):
        char = stripped[cursor]
        next_char = stripped[cursor + 1] if cursor + 1 < len(stripped) else ""
        if char == "\\":
            if not next_char:
                return None
            value.append(next_char)
            cursor += 2
            continue
        if quote == "`" and char == "$" and next_char == "{":
            has_template_interpolation = True
        if char == quote:
            if stripped[cursor + 1 :].strip():
                return None
            if has_template_interpolation:
                return _FRONTEND_UNRESOLVED_COMPUTED_MEMBER, False
            return "".join(value), True
        value.append(char)
        cursor += 1

    return None


def _split_frontend_top_level_conditional(masked_expression: str) -> tuple[int, int] | None:
    pairs = {"(": ")", "[": "]", "{": "}"}
    closers = set(pairs.values())
    stack: list[str] = []
    question_index: int | None = None

    for index, char in enumerate(masked_expression):
        next_char = masked_expression[index + 1] if index + 1 < len(masked_expression) else ""
        if char in pairs:
            stack.append(pairs[char])
        elif char in closers:
            if stack and char == stack[-1]:
                stack.pop()
        elif not stack and char == "?" and next_char != ".":
            question_index = index
            break

    if question_index is None:
        return None

    stack = []
    nested_conditionals = 0
    for index in range(question_index + 1, len(masked_expression)):
        char = masked_expression[index]
        next_char = masked_expression[index + 1] if index + 1 < len(masked_expression) else ""
        if char in pairs:
            stack.append(pairs[char])
        elif char in closers:
            if stack and char == stack[-1]:
                stack.pop()
        elif not stack and char == "?" and next_char != ".":
            nested_conditionals += 1
        elif not stack and char == ":":
            if nested_conditionals == 0:
                return question_index, index
            nested_conditionals -= 1

    return None


def _classify_frontend_computed_member(
    masked_expression: str,
    original_expression: str,
) -> tuple[str, bool]:
    static_string = _parse_frontend_string_member(original_expression)
    if static_string is not None:
        return static_string

    conditional = _split_frontend_top_level_conditional(masked_expression)
    if conditional is not None:
        question_index, colon_index = conditional
        branches = (
            (
                masked_expression[question_index + 1 : colon_index],
                original_expression[question_index + 1 : colon_index],
            ),
            (
                masked_expression[colon_index + 1 :],
                original_expression[colon_index + 1 :],
            ),
        )
        branch_results = [
            _classify_frontend_computed_member(masked_branch, original_branch)
            for masked_branch, original_branch in branches
        ]
        if all(
            is_static and name not in _FRONTEND_DEBT_METHODS for name, is_static in branch_results
        ):
            return _FRONTEND_STATIC_COMPUTED_MEMBER, True
        return _FRONTEND_UNRESOLVED_COMPUTED_MEMBER, False

    if re.fullmatch(
        r"(?:[+-]?\d+(?:\.\d+)?|true|false|null|undefined)",
        original_expression.strip(),
    ):
        return _FRONTEND_STATIC_COMPUTED_MEMBER, True

    return _FRONTEND_UNRESOLVED_COMPUTED_MEMBER, False


def _read_frontend_computed_property(
    masked: str,
    original: str,
    index: int,
) -> tuple[str, int, int, bool] | None:
    if index >= len(masked) or masked[index] != "[":
        return None

    property_start = _skip_frontend_whitespace(original, index + 1)
    if property_start >= len(original):
        return None
    end = _frontend_computed_property_end(masked, index)
    if end is None:
        return None

    name, is_static = _classify_frontend_computed_member(
        masked[index + 1 : end - 1],
        original[index + 1 : end - 1],
    )
    return name, end, property_start, is_static


def _frontend_debt_chain(
    masked: str,
    original: str,
    match: re.Match[str],
) -> tuple[int, int, str] | None:
    parts = [match.group("base")]
    index = match.end()
    first_debt_index: int | None = None
    called_after_debt = False
    end = index

    while True:
        index = _skip_frontend_whitespace(masked, index)
        if index >= len(masked):
            break

        if masked[index] == "(":
            index = _skip_frontend_balanced_call(masked, index)
            end = index
            if first_debt_index is not None:
                called_after_debt = True
            continue

        if masked.startswith("?.", index):
            index = _skip_frontend_whitespace(masked, index + 2)
            if index < len(masked) and masked[index] == "(":
                index = _skip_frontend_balanced_call(masked, index)
                end = index
                if first_debt_index is not None:
                    called_after_debt = True
                continue
        elif masked[index] == ".":
            index = _skip_frontend_whitespace(masked, index + 1)

        if index < len(masked) and masked[index] == "[":
            computed = _read_frontend_computed_property(masked, original, index)
            if computed is None:
                break
            name, index, property_start, is_static_literal = computed
            parts.append(name)
            end = index
            if (
                name in _FRONTEND_DEBT_METHODS
                or not is_static_literal
                and name == _FRONTEND_UNRESOLVED_COMPUTED_MEMBER
            ) and first_debt_index is None:
                first_debt_index = property_start
            continue

        identifier_start = index
        identifier = _read_frontend_identifier(masked, identifier_start)
        if identifier is None:
            break

        name, index = identifier
        parts.append(name)
        end = index
        if name in _FRONTEND_DEBT_METHODS and first_debt_index is None:
            first_debt_index = identifier_start

    if first_debt_index is None or not called_after_debt:
        return None

    return first_debt_index, end, ".".join(parts)


def _is_frontend_test_file(path: Path) -> bool:
    if path.suffix not in _FRONTEND_TEST_SUFFIXES:
        return False
    relative = path.relative_to(REPO_ROOT)
    if relative.parts[:2] == ("frontend", "e2e"):
        return True
    if relative.parts[:2] != ("frontend", "src"):
        return False
    return "__tests__" in relative.parts or ".test." in path.name or ".spec." in path.name


def _frontend_fingerprint(path: Path, callee: str, source: str) -> str:
    normalized_source = re.sub(r"\s+", " ", source).strip()
    raw = "|".join((path.as_posix(), callee, normalized_source))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _scan_frontend_source(source: str, path: Path) -> list[_FrontendDebtSite]:
    masked = _mask_frontend_comments_and_strings(source)
    lines = source.splitlines()
    sites: list[_FrontendDebtSite] = []
    for match in _FRONTEND_DEBT_BASE_PATTERN.finditer(masked):
        debt_chain = _frontend_debt_chain(masked, source, match)
        if debt_chain is None:
            continue

        debt_start, _end, callee = debt_chain
        line = masked.count("\n", 0, debt_start) + 1
        source_line = lines[line - 1].strip()
        sites.append(
            _FrontendDebtSite(
                id=_frontend_fingerprint(path, callee, source_line),
                path=path,
                line=line,
                callee=callee,
                source=source_line,
            )
        )
    return sites


def _scan_frontend_debt_sites() -> list[_FrontendDebtSite]:
    sites: list[_FrontendDebtSite] = []
    for root in _FRONTEND_TEST_ROOTS:
        if not root.exists():
            continue
        for path in sorted(candidate for candidate in root.rglob("*") if candidate.is_file()):
            if not _is_frontend_test_file(path):
                continue
            relative = path.relative_to(REPO_ROOT)
            sites.extend(_scan_frontend_source(path.read_text(encoding="utf-8"), relative))
    return sorted(sites, key=lambda site: (site.path.as_posix(), site.line, site.callee))


def _format_frontend_site(site: _FrontendDebtSite) -> str:
    return f"{site.id} {site.path.as_posix()}:{site.line} {site.callee} source={site.source!r}"


def _load_summary_mutation_targets() -> list[dict[str, object]]:
    try:
        payload = json.loads(_MUTATION_TARGETS_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid mutation target JSON: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("Mutation targets must use schema_version 1")
    targets = payload.get("targets")
    if not isinstance(targets, list):
        raise ValueError("Mutation targets must define targets as a list")
    result: list[dict[str, object]] = []
    for raw in targets:
        if not isinstance(raw, dict):
            raise ValueError("Mutation target must be an object")
        name, rate = raw.get("name"), raw.get("max_survival_rate")
        if not isinstance(name, str) or not name:
            raise ValueError("Mutation target requires a non-empty name")
        if isinstance(rate, bool) or not isinstance(rate, int | float) or not 0 <= rate <= 100:
            raise ValueError(f"Mutation target {name} has invalid max_survival_rate")
        result.append({"name": name, "rate": float(rate)})
    return sorted(result, key=lambda target: str(target["name"]))


def _test_health_summary() -> str:
    backend = _scan_debt_sites()
    frontend = _scan_frontend_debt_sites()
    rows = [
        (
            "Backend skip/skipif",
            sum(
                site.kind in {"pytest.skip", "pytest.mark.skip", "pytest.mark.skipif"}
                for site in backend
            ),
            "Backend AST scanner",
        ),
        (
            "Backend importorskip",
            sum(site.kind == "pytest.importorskip" for site in backend),
            "Backend AST scanner",
        ),
        (
            "Backend xfail",
            sum(site.kind in {"pytest.xfail", "pytest.mark.xfail"} for site in backend),
            "Backend AST scanner",
        ),
        (
            "Backend flaky",
            sum(site.kind == "pytest.mark.flaky" for site in backend),
            "Backend AST scanner (zero-budget fingerprint ratchet)",
        ),
        ("Frontend marker debt", len(frontend), "Frontend source scanner"),
        (
            "Playwright CI retries",
            _PLAYWRIGHT_CI_RETRY_BUDGET,
            "frontend/playwright.config.ts: process.env.CI ? 2 : 0",
        ),
    ]
    lines = [
        "# Test Health Summary",
        "",
        (
            "Generated deterministically from the live test-debt scanners and "
            "mutation targets. Regenerate with `uv run python tests/test_test_debt.py "
            "--write-summary`."
        ),
        "",
        "| Signal | Count / max survivor rate | Enforcement/source |",
        "| --- | ---: | --- |",
    ]
    for signal, value, source in rows:
        lines.append(f"| {signal} | {value} | {source} |")
    for target in _load_summary_mutation_targets():
        lines.append(
            f"| Mutation `{target['name']}` | {target['rate']:.2f}% | "
            "mutation/targets.json max_survival_rate |"
        )
    return "\n".join(lines) + "\n"


def test_skip_xfail_importorskip_sites_have_reasons() -> None:
    missing_reasons = [site for site in _scan_debt_sites() if not site.reason_is_static]

    assert not missing_reasons, "\n".join(_format_site(site) for site in missing_reasons)


def test_skip_xfail_importorskip_debt_matches_reviewed_budget() -> None:
    sites = _scan_debt_sites()
    actual_by_id = {site.id: site for site in sites}
    unexpected = sorted(set(actual_by_id) - _EXPECTED_DEBT_IDS)
    missing = sorted(_EXPECTED_DEBT_IDS - set(actual_by_id))

    details: list[str] = []
    if unexpected:
        details.append("Unexpected new or changed debt sites:")
        details.extend(_format_site(actual_by_id[site_id]) for site_id in unexpected)
    if missing:
        details.append("Reviewed debt entries no longer found; remove them from the budget:")
        details.extend(missing)

    assert not details, "\n".join(details)


def test_frontend_skip_fixme_fail_only_debt_matches_reviewed_budget() -> None:
    assert all(reason.strip() for reason in _EXPECTED_FRONTEND_DEBT_REASONS.values())

    sites = _scan_frontend_debt_sites()
    actual_by_id = {site.id: site for site in sites}
    expected_ids = set(_EXPECTED_FRONTEND_DEBT_REASONS)
    unexpected = sorted(set(actual_by_id) - expected_ids)
    missing = sorted(expected_ids - set(actual_by_id))

    details: list[str] = []
    if unexpected:
        details.append(
            "Unexpected frontend skip/fixme/fail/only/conditional/todo debt sites. "
            "Remove the debt, "
            "or add a reviewed fingerprint and reason to "
            "_EXPECTED_FRONTEND_DEBT_REASONS:"
        )
        details.extend(_format_frontend_site(actual_by_id[site_id]) for site_id in unexpected)
    if missing:
        details.append("Reviewed frontend debt entries no longer found; remove them:")
        details.extend(missing)

    assert not details, "\n".join(details)


def test_non_strict_xfails_are_explicitly_budgeted() -> None:
    sites = _scan_debt_sites()
    non_strict_or_ambiguous_xfails = {
        site.id for site in sites if site.kind == "pytest.mark.xfail" and site.strict is not True
    }

    assert non_strict_or_ambiguous_xfails == _EXPECTED_NON_STRICT_XFAIL_IDS


def test_playwright_ci_retry_budget_is_pinned() -> None:
    source = (REPO_ROOT / "frontend" / "playwright.config.ts").read_text(encoding="utf-8")
    assert f"retries: process.env.CI ? {_PLAYWRIGHT_CI_RETRY_BUDGET} : 0," in source


def test_test_health_summary_matches_regeneration() -> None:
    checked_in = _TEST_HEALTH_SUMMARY_PATH.read_text(encoding="utf-8")
    regenerated = _test_health_summary()
    assert checked_in == regenerated, (
        "tests/test-health-summary.md is stale: it must match a fresh scan of the "
        "checked-in skip/xfail/flaky debt sites. Regenerate and commit it with "
        "`uv run python tests/test_test_debt.py --write-summary`."
    )


def test_scanner_requires_explicit_marker_reasons() -> None:
    sites = _scan_source(
        """
        import pytest

        @pytest.mark.skipif(True)
        def test_missing_reason():
            pass
        """
    )

    assert len(sites) == 1
    assert sites[0].kind == "pytest.mark.skipif"
    assert not sites[0].reason_is_static


def test_scanner_detects_module_pytestmark_and_param_marks() -> None:
    sites = _scan_source(
        """
        import pytest

        pytestmark = pytest.mark.skip(reason="module is intentionally skipped")

        @pytest.mark.parametrize(
            "value",
            [pytest.param(1, marks=pytest.mark.xfail(reason="known", strict=True))],
        )
        def test_value(value):
            pass
        """
    )

    assert [(site.kind, site.reason, site.strict) for site in sites] == [
        ("pytest.mark.skip", "module is intentionally skipped", None),
        ("pytest.mark.xfail", "known", True),
    ]


def test_scanner_detects_flaky_markers() -> None:
    sites = _scan_source(
        """
        import pytest

        @pytest.mark.flaky(reason="intermittent dependency")
        def test_flaky():
            pass
        """
    )

    assert [(site.kind, site.reason) for site in sites] == [
        ("pytest.mark.flaky", "intermittent dependency")
    ]


def test_scanner_rejects_bare_and_dynamic_markers() -> None:
    sites = _scan_source(
        """
        import pytest

        pytestmark = pytest.mark.skip

        def test_dynamic_marker(request):
            request.node.add_marker("xfail")
        """
    )

    assert [site.kind for site in sites] == ["pytest.mark.skip", "pytest.mark.xfail"]
    assert all(not site.reason_is_static for site in sites)


def test_scanner_supports_pytest_aliases_without_losing_budget_visibility() -> None:
    sites = _scan_source(
        """
        import pytest as pt
        from pytest import importorskip, mark, skip

        skip("imperative skip")
        importorskip("thing", reason="optional thing")

        @mark.xfail(reason="known", strict=True)
        def test_alias():
            pt.xfail("runtime known")
        """
    )

    assert sorted(site.kind for site in sites) == [
        "pytest.importorskip",
        "pytest.mark.xfail",
        "pytest.skip",
        "pytest.xfail",
    ]
    assert all(site.reason_is_static for site in sites)


def test_scanner_rejects_dynamic_reasons_but_accepts_constant_concatenation() -> None:
    sites = _scan_source(
        """
        import pytest

        pytest.skip("static " + "reason")
        pytest.skip("dynamic " + str(object()))
        """
    )

    assert sites[0].reason == "static reason"
    assert sites[0].reason_is_static
    assert not sites[1].reason_is_static


def test_frontend_scanner_detects_skip_fixme_fail_only_variants() -> None:
    sites = _scan_frontend_source(
        """
        test.skip("temporarily skipped", () => {});
        describe.skip("suite", () => {});
        it.skip("case", () => {});
        test.fixme("fixme", () => {});
        test.fail("known fail");
        test.todo("planned coverage");
        test.describe.skip("playwright group", () => {});
        test.describe.fixme("playwright fixme group", () => {});
        it.fails("vitest expected failure", () => {});
        test.only("focused test", () => {});
        describe.only("focused suite", () => {});
        it.only("focused case", () => {});
        test.describe.only("focused playwright group", () => {});

        // test.skip("commented out", () => {});
        // test.only("commented focus", () => {});
        const text = "describe.skip('not code')";
        const focusText = "test.only('not code')";
        """,
        Path("frontend/e2e/example.spec.ts"),
    )

    assert [site.callee for site in sites] == [
        "test.skip",
        "describe.skip",
        "it.skip",
        "test.fixme",
        "test.fail",
        "test.todo",
        "test.describe.skip",
        "test.describe.fixme",
        "it.fails",
        "test.only",
        "describe.only",
        "it.only",
        "test.describe.only",
    ]


def test_frontend_scanner_detects_chained_skip_fail_and_focus_variants() -> None:
    sites = _scan_frontend_source(
        """
        test.skip.each([{ value: 1 }])("case %#", ({ value }) => {
            expect(value).toBe(1);
        });
        test.concurrent.skip("concurrent skip", async () => {});
        describe.each([["one"]]).skip("suite %#", (name) => {});
        it.each([["one"]]).skip("case %#", (name) => {});
        test.skipIf(isWindows)("conditional skip", () => {});
        test.runIf(isLinux)("conditional run", () => {});
        test.runIf(isLinux).skip("conditional chain skip", () => {});
        test.fails("vitest expected failure", () => {});
        test.concurrent.fails("concurrent expected failure", async () => {});
        test.describe.serial.only("focused serial group", () => {});
        test.describe.parallel.skip("parallel skip group", () => {});

        test.only.each([{ value: 1 }])("focused case %#", ({ value }) => {});
        test.concurrent.only("focused concurrent case", async () => {});
        describe.each([["one"]]).only("focused suite %#", (name) => {});
        it.each([["one"]]).only("focused case %#", (name) => {});
        test.runIf(isLinux).only("focused conditional chain", () => {});
        """,
        Path("frontend/src/example.test.ts"),
    )

    assert [site.callee for site in sites] == [
        "test.skip.each",
        "test.concurrent.skip",
        "describe.each.skip",
        "it.each.skip",
        "test.skipIf",
        "test.runIf",
        "test.runIf.skip",
        "test.fails",
        "test.concurrent.fails",
        "test.describe.serial.only",
        "test.describe.parallel.skip",
        "test.only.each",
        "test.concurrent.only",
        "describe.each.only",
        "it.each.only",
        "test.runIf.only",
    ]


def test_frontend_scanner_detects_optional_and_computed_debt_chains() -> None:
    sites = _scan_frontend_source(
        """
        test?.skip("optional skip", () => {});
        test.skip?.("optional call skip", () => {});
        test["only"]("computed focus", () => {});
        test['skip']("computed skip", () => {});
        test[`fixme`]("computed fixme", () => {});
        test.each([[1]])["fails"]("computed expected failure", () => {});
        describe.each([["one"]])["only"]("computed focused suite", () => {});
        it["skipIf"](isWindows)("computed conditional skip", () => {});
        """,
        Path("frontend/src/example.test.ts"),
    )

    assert [site.callee for site in sites] == [
        "test.skip",
        "test.skip",
        "test.only",
        "test.skip",
        "test.fixme",
        "test.each.fails",
        "describe.each.only",
        "it.skipIf",
    ]


def test_frontend_scanner_fails_closed_for_unresolved_computed_members() -> None:
    sites = _scan_frontend_source(
        """
        test[debtMethod]("dynamic test method", () => {});
        test[flag ? "skip" : "only"]("dynamic conditional method", () => {});
        test.each(cases)[method]("dynamic table method", () => {});
        describe[method]("dynamic suite method", () => {});
        it?.[method]("dynamic optional method", () => {});
        test[`sk${suffix}`]("dynamic template method", () => {});
        test.each(cases)?.[method]?.("dynamic optional table method", () => {});

        test["each"](cases)("static non-debt table", () => {});
        test[flag ? "each" : "describe"](cases)("static clean conditional", () => {});
        test[0]("static numeric member", () => {});
        test[true]("static boolean member", () => {});
        test[`each`](cases)("static template member", () => {});
        """,
        Path("frontend/src/example.test.ts"),
    )

    assert [site.callee for site in sites] == [
        "test.<computed>",
        "test.<computed>",
        "test.each.<computed>",
        "describe.<computed>",
        "it.<computed>",
        "test.<computed>",
        "test.each.<computed>",
    ]


def test_frontend_scanner_ignores_chained_debt_in_comments_and_strings() -> None:
    sites = _scan_frontend_source(
        r"""
        // test.skip.each([{ value: 1 }])("commented", () => {});
        // test.concurrent.only("commented focus", () => {});
        /*
        describe.each([["one"]]).skip("commented suite", () => {});
        it.each([["one"]]).only("commented case", () => {});
        */
        const skipped = "test.runIf(flag).skip('not code', () => {})";
        const focused = 'test.only.each([1])("not code", () => {})';
        const templated = `test.concurrent.fails("not code", () => {})`;

        test.skip("actual debt", () => {});
        """,
        Path("frontend/src/example.test.ts"),
    )

    assert [site.callee for site in sites] == ["test.skip"]


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate the deterministic test-health summary.")
    parser.add_argument("--write-summary", action="store_true")
    args = parser.parse_args(argv)
    summary = _test_health_summary()
    if args.write_summary:
        _write_generated_summary(_TEST_HEALTH_SUMMARY_PATH, summary)
    sys.stdout.write(summary)
    return 0


def _write_generated_summary(path: Path, summary: str) -> None:
    """Write the explicitly requested tracked report outside pytest execution."""
    path.write_text(summary, encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(_main())
