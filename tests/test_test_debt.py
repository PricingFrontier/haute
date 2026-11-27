"""Guardrails for skip/xfail/importorskip test debt.

These tests are deliberately strict. A skipped or expected-failing test can be
useful, but it is also invisible risk unless it has a reason, an explicit
review budget, and a ratchet that prevents casual growth.
"""

from __future__ import annotations

import argparse
import ast
import functools
import json
import re
import sys
import textwrap
from collections.abc import Iterable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tests._source_files import source_files
from tests._test_debt_scanner import (
    _FRONTEND_TEST_ROOTS,
    REPO_ROOT,
    TESTS_DIR,
    _DebtSite,
    _DebtVisitor,
    _FrontendDebtSite,
    _is_frontend_test_file,
    _scan_frontend_source,
)

_TEST_HEALTH_SUMMARY_PATH = TESTS_DIR / "test-health-summary.md"
_MUTATION_TARGETS_PATH = REPO_ROOT / "mutation" / "targets.json"
_PLAYWRIGHT_CI_RETRY_BUDGET = 2

# Exact debt-site fingerprints as of the test-suite review. The fingerprint uses
# path, enclosing scope, debt kind, reason text, and normalized AST source. A new
# skip/xfail/importorskip, or a changed reason, must be accepted deliberately.
# A skip whose condition is a file the repository does not hold (a frontend build,
# what a local training left in outputs/) is not accepted: it runs in no CI job,
# and on a developer's machine against whatever that file holds. The test makes
# the file in its temporary folder instead.
_EXPECTED_DEBT_IDS = {
    # tests/test_run_parity.py (EXE-10): a difference between the ways of running a
    # pipeline that another roadmap package owns, strict so its delivery turns it
    # green: MLR-04.
    "2e1728e0067476c5",
    # tests/test_cli_examples.py: model_lifecycle's run and dry run, whose Model Scoring
    # names a run nobody trained, end as an unrecognised failure where the node's
    # configuration error naming the run is meant; strict, so MLR-04 turns it green.
    "ad567900a5b0deb3",
    # The chunked GLM suite (MOD-M01) fits real RustyStats models in both fit modes;
    # rustystats is an optional dependency, so the module importorskips it like the
    # other GLM suites. See tests/test_glm_chunked_fit.py.
    "586f4dc88064defc",
    # Native Windows HighQoS requires the real Windows process API. The
    # strong-revision cache witness requires a filesystem exposing that proof;
    # unsupported filesystems are covered separately by cache-bypass tests.
    "b37a0e4332ab67a7",
    "d60cb6e25d9d0a46",
    # Two files whose names differ only in letter case exist only on a disk that keeps
    # case apart; on Windows and macOS (CI's platform smoke) the same module renames the
    # folders for real. See tests/test_git_letter_case.py.
    "926269bfd5231f6c",
    # The same holds for a node path naming one of two such files: on Windows and macOS
    # the module's cross-system verdict test covers the case-folding side.
    # See tests/test_path_resolution.py.
    "6b48cc35b453bbbe",
    # A file held open with open() blocks its replacement only on Windows, where
    # the git tree fence's real case lives; other platforms run the same fence
    # through a faked half-swap (tests/test_git_working_tree.py::TestTreeFence).
    "b8387c82cbbedb60",
    # The same holds for a catch-up over a file the remote deleted or changed; other
    # platforms run its faked case (tests/test_git_operations.py, held open).
    "1abcc6888781ad80",
    # Offset-through-serving suite — every test trains a real model, so each
    # leg importorskips its optional modelling engine (rustystats for the GLM
    # legs, catboost for the boosted / numeric-only legs), matching the
    # existing modelling suites' posture. See tests/test_offset_scoring.py.
    "4ca309e9ef45366d",
    "22a55d6a8665b087",
    "3a392e6541a16437",
    "3ab16f01c706b8cf",
    "4908d9a546bf0eb3",
    "78ccd0efb0b58c07",
    "e4a3424d22b3e4a1",
    "8575a86d12fcd9fd",
    "8ad195689440ad84",
    "8fc522e44d27d619",
    "9f129f4b22a3f0a6",
    "f0723d42ef80e1bb",
    "b809feae1b81044a",
    "d678759483265dfe",
    "dc9bc31593b95505",
    "e2d91359c364b2e7",
    "f0497dbc6c3e3f81",
    "f3ec6eddbd8aa75a",
    # Dependency-posture contracts (PR #203) — tests that ask the REAL
    # installed package whether an assumption still holds, and therefore skip,
    # saying so, where that package is absent. test_sandbox.py
    # `_allowlist_entry` skips an unpickler-allowlist entry whose distribution
    # is not installed, reporting it as unverified rather than passed;
    # `test_real_unpickler_reports_absent_catboost` is the converse and skips
    # where catboost IS installed. test_databricks_io.py
    # `test_real_databricks_cursor_exposes_rownumber` skips without the
    # databricks-sql-connector extra.
    "bad18e83b4ed758b",
    "3bf4ea089c17f407",
    "963014c76507d368",
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
    "de7596ed55652986",
    "a7c21a9dc1f1aada",
    # MSC-03 — a Model Scoring sibling contract that escapes the project through
    # a symlink is refused by scoring and bundling; same platform prerequisite.
    # See tests/test_model_file_source.py.
    "90c13b0fe47fe9ec",
    # W2.9 — the trace-cache budget wiring assertion cannot hold when an
    # operator deliberately overrides HAUTE_TRACE_CACHE_MAX_BYTES; the skip
    # documents that the pin targets default wiring only. See
    # tests/test_trace_cache_byte_awareness.py::TestTraceCacheByteBudgetWiring.
    "73141b06a8fbbace",
    # PL2-07 — Haute names a headerless Excel sheet's columns; the sheet is written
    # with openpyxl, which dev installs carry through the databricks extra but core
    # haute does not require, as TestExcelLeg in test_data_io_roundtrips.py. See
    # tests/test_polars_io_registry.py::TestColumnsAreNamedByTheHeaderOrTheConfig.
    "593386ddd3718589",
    # Multi-frame review follow-up — the atomic-write reader-contention tests
    # are win32-specific by design: POSIX rename(2) succeeds under a concurrent
    # reader, while Windows MoveFileExW raises. Skipped on non-win32. See
    # tests/test_file_ops.py::TestAtomicWriteWindowsReaderContention.
    "08c3e550bc505052",
    "d0db18a1315ae001",
    # CACHE-S17 — the lock-file sweep's POSIX protocol: a process waiting on a
    # lock file the sweep deletes must lock the file the path names. Windows
    # refuses to delete a file a process has open, so the race cannot occur
    # there; its branch is covered by tests/test_file_lock.py's Windows test.
    # See tests/test_file_lock.py and tests/test_node_snapshot_cross_process.py.
    "83e16e5482771a5d",
    "d2b7c7021cada091",
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
    "55b3c4a50777661d",
    "3c5baaf0a02d232d",
    "f200829b7b07df70",
    "f4daebe0d97f7096",
    "02519ef042dc229b",
    "b1fdda1913c30cea",
    "a8ba935e2e69547b",
    "1c1a7efd1b6b5496",
    "bc580591bd05253a",
    "ea75218e22580bb4",
    "6f5878234dddd567",
    "df615adf6facd8fc",
    "c60a5a978c60725a",
    "4ae118442dafce1e",
    "8a0f7160d9044069",
    "a78fb6a12665d489",
    "7f0862e589b36ce6",
    "707535b7903ffedc",
    "9b58538ec2c90223",
    "6881417aa251afb7",
    "fb0e81ff682c42ba",
    "de69a025e2015f76",
    "c846306558bad02c",
    "9ca93c4280b5017c",
    "bfd8943e3c6770ee",
    "6a71d9434ec9cee0",
    "f68efc20cf708633",
    "ad643ba2aef65736",
    "ccba5bfb3985c5e7",
    "2a634b625baa4350",
    "aec15b8d084c0f9f",
    "36389904372edffb",
    "40f0104677c0d566",
    "848a5576a63f17d0",
    "df789ca110c56d8d",
    "e800d20c2fdb0d00",
    "f6ab12590998eb2c",
    # This test reads RLIMIT_AS inside a real capped child, which only Linux enforces;
    # the RSS watchdog that every platform has is covered elsewhere.
    "010ed52ea296efcc",
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
    # MLF-D03 — tests/test_mlflow_destinations_e2e.py proves per-node destination
    # isolation against REAL local file stores (logging, scoring, optimiser
    # apply, deploy scoring, generated scripts). Same module-level
    # importorskip convention as test_mlflow_log_button_roundtrip.py so the
    # core-only CI leg (no mlflow) skips cleanly while the dev-group legs run it.
    "752af62493a1e027",
    # 4b.8 — tests/test_mlflow_log_button_roundtrip.py proves the "Log to
    # MLflow" button's signature/artifact round-trip against a REAL local
    # file-store MLflow (a wrong signature only fails at genuine pyfunc
    # schema enforcement, which mocks cannot reproduce). Same single
    # module-level importorskip convention as test_mlflow_io_real_pyfunc.py.
    "c12bf51de96471f5",
    # W4b (4b.1/4b.2/4b.3) — real-GLM route/export/diagnostics tests train
    # actual rustystats models; rustystats is an optional extra, so the
    # tests importorskip it. See tests/test_train_param_routing.py.
    "560f4d4069c7b172",
    "6e4e489debab1b3f",
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
    # See tests/test_config_io.py::TestConfigPathEscapeGuard
    # ::test_escape_guard_triggers_on_resolved_outside.
    "278a3f348cd0f191",
    # Windows symlink privilege — a save's record is acted on only inside the project:
    # a removal reached through a link out of it is left, and a .haute/saves folder
    # reached through a link refuses the save. The tests build directory symlinks,
    # which need a privilege Windows withholds by default (WinError 1314); Linux CI
    # runs them. See tests/test_save_record.py::_link.
    "df00a15d65500007",
    # Windows symlink privilege — the MLflow settings write-containment guard
    # test symlinks haute.toml at an external victim file to prove a save
    # refuses to follow it, but symlink creation needs a privilege Windows
    # withholds by default (WinError 1314). Skipped when symlink creation
    # raises, mirroring the other symlink-guard tests; Linux CI runs it.
    # See tests/test_mlflow_settings.py::TestLoadSaveSettings
    # ::test_save_refuses_symlinked_haute_toml_escaping_the_project.
    "0d633b8caaa866db",
    # Windows symlink privilege — the file-browser short-path regression test
    # builds a symlinked project dir so ``Path.cwd()`` differs from its
    # ``resolve()`` (the cross-platform stand-in for a Windows 8.3 short cwd),
    # but symlink creation needs a privilege Windows withholds by default
    # (WinError 1314). Skipped when symlink creation raises, mirroring the
    # other symlink-guard tests. See tests/test_files_routes.py
    # ::TestBrowseFilesUnresolvedCwd._symlinked_project.
    "e29ebc6050519fc5",
    # Windows symlink privilege — a submodel module linked out of the project is
    # refused at parse, but a file symlink needs a privilege Windows withholds by
    # default (WinError 1314); its folder-junction twin runs everywhere, and Linux
    # CI runs both. See tests/test_parser_project_layout.py
    # ::test_pipeline_rejects_submodel_outside_project_root.
    "0e5d37d3b717fa79",
    # Windows symlink privilege — the containment matrix proves every caller of
    # the one check refuses a symlink escape, but symlink creation needs a
    # privilege Windows withholds by default (WinError 1314). Only the symlink
    # cases skip; Linux CI runs them. See tests/test_path_containment.py
    # ::_require_links.
    "4f163427364999ba",
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
    # A native allocation refused by the per-worker memory cap is classified as
    # a memory outcome from the child's real crash status (Linux SIGABRT/SIGKILL,
    # Windows fail-fast). It needs a host where that native cap can be installed;
    # the exit-code classification itself is covered on every platform.
    # See tests/test_worker_isolation.py.
    "28de4bc1e189b1c6",
    # The RLIMIT_AS fallback counts reserved address space, which only Linux exposes
    # through /proc; the warm-up that keeps it honest is measured in a fresh process
    # there. The ordering and allowance logic is covered on every platform with a fake
    # resource API. See tests/test_native_memory_limit.py.
    "0658216fd4d47ec9",
    # A training fit's protocol worker runs under a real native cap (a Job Object on
    # Windows, a cgroup or RLIMIT_AS on Linux); macOS has no dependable per-process
    # cap, so the real-spawn check needs a host that has one. The entrypoint's lease
    # wiring is covered on every platform. See tests/test_worker_protocol.py.
    "809667c1ff73f5be",
    # CatBoost training under the RLIMIT_AS fallback: that cap counts reserved address
    # space, which only Linux reports, and the test installs it itself so no delegated
    # cgroup stands in for it. The preload ordering and the allowance formula are covered
    # on every platform. See tests/test_training_worker_protocol.py.
    "564994321efac1ec",
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
    # and test_data_io_nodes.py.
    "8e42868e79ff264b",
    # Automatic input preparation through a real hard-capped spawn worker: the
    # cap is mandatory, so the test can only run where the host can install a
    # native memory cap (Linux cgroup/rlimit, Windows Job Object); macOS skips.
    # See tests/test_input_preparation.py.
    "989b2ba60fe375be",
    # The optimiser's solver session and its dedicated worker (OPT-W01) exist to
    # run under a native memory cap, so both suites need one; macOS skips. See
    # tests/test_dedicated_workers.py and tests/test_optimiser_solver_session.py.
    "feb425948a23b85f",
    "2e1f146b53c9a4b1",
    # Hosted git-credential helper — the askpass helper is a `#!/bin/sh`
    # script installed only by the hosted container bootstrap
    # (databricks_app/bootstrap.py), so the two tests that execute it cannot
    # run on Windows, which has no shebang handling and refuses the file with
    # WinError 193. The Linux legs run them. The helper's other contracts (no
    # token in the file, no helper without a token, GIT_ASKPASS registration)
    # stay platform-neutral and run everywhere.
    # See tests/test_project_storage.py::TestCredentialHandling.
    "b4318bcdd014bb2e",
    "e2be584e12bb5227",
    # Real-symlink prerequisite in
    # tests/test_artifact_paths.py::test_safe_path_rejects_lexical_and_alias_paths:
    # Windows may lack symlink privilege; lexical and hardlink checks run before
    # the skip, Linux runs the real-symlink assertion.
    "676d266f149e829d",
    # Real-device XGBoost GPU tests in tests/test_xgboost_gpu.py (MOD-F06): they
    # need the full xgboost CUDA build and an NVIDIA GPU, which CI runners and
    # Haute's default xgboost-cpu install lack; the device-free GPU contract
    # (config, refusal, fallback detection, VRAM, route, gpu-setup) runs everywhere.
    "3f2e4ca1be0a6123",
    # SBS-03 — Haute's temporary folder is checked for its owner and mode on POSIX
    # only: Windows' temporary folder is the user's own and has neither, so its one
    # test (the folder's name, no owner checked) runs everywhere. See
    # tests/test_temp_folder.py::posix_only.
    "1f309cd3885ce515",
}

_EXPECTED_NON_STRICT_XFAIL_IDS = {
    "9b58538ec2c90223",
}

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
    "39442a180206c699": (
        "Platform-only journey (the git restore PR): only on Windows does a file "
        "another program holds open stop git replacing it, so the browser test "
        "that switches branches over a held file (Excel's lock, and a "
        "read-sharing one) has nothing to exercise on the Linux CI lane and "
        "runs on Windows machines. The fence itself is unit-tested everywhere "
        "in tests/test_git_working_tree.py::TestTreeFence."
    ),
}


# The two tree scans below take seconds each (every test module is parsed), and
# several tests plus the summary read the same result. The test trees do not
# change during a session, so each process scans once.
@functools.cache
def _scan_debt_sites() -> tuple[_DebtSite, ...]:
    sites: list[_DebtSite] = []
    for path in source_files(TESTS_DIR):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        visitor = _DebtVisitor(path.relative_to(TESTS_DIR.parent))
        sites.extend(visitor.scan(tree))
    return tuple(sites)


def _scan_source(source: str) -> list[_DebtSite]:
    tree = ast.parse(textwrap.dedent(source))
    return _DebtVisitor(Path("tests/example.py")).scan(tree)


def _format_site(site: _DebtSite) -> str:
    strict = "" if site.strict is None else f" strict={site.strict}"
    return (
        f"{site.id} {site.path.as_posix()}:{site.line} "
        f"{site.scope} {site.kind}{strict} reason={site.reason!r}"
    )


@functools.cache
def _scan_frontend_debt_sites() -> tuple[_FrontendDebtSite, ...]:
    sites: list[_FrontendDebtSite] = []
    for root in _FRONTEND_TEST_ROOTS:
        if not root.exists():
            continue
        for path in sorted(candidate for candidate in root.rglob("*") if candidate.is_file()):
            if not _is_frontend_test_file(path):
                continue
            relative = path.relative_to(REPO_ROOT)
            sites.extend(_scan_frontend_source(path.read_text(encoding="utf-8"), relative))
    return tuple(sorted(sites, key=lambda site: (site.path.as_posix(), site.line, site.callee)))


def _format_frontend_site(site: _FrontendDebtSite) -> str:
    return f"{site.id} {site.path.as_posix()}:{site.line} {site.callee} source={site.source!r}"


def _load_summary_mutation_targets() -> list[dict[str, object]]:
    try:
        payload = json.loads(_MUTATION_TARGETS_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid mutation target JSON: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != 2:
        raise ValueError("Mutation targets must use schema_version 2")
    targets = payload.get("targets")
    if not isinstance(targets, list):
        raise ValueError("Mutation targets must define targets as a list")
    result: list[dict[str, object]] = []
    for raw in targets:
        if not isinstance(raw, dict):
            raise ValueError("Mutation target must be an object")
        name = raw.get("name")
        rate = raw.get("max_survival_rate")
        max_pending_per_shard = raw.get("max_pending_per_shard")
        if not isinstance(name, str) or not name:
            raise ValueError("Mutation target requires a non-empty name")
        if isinstance(rate, bool) or not isinstance(rate, int | float) or not 0 <= rate <= 100:
            raise ValueError(f"Mutation target {name} has invalid max_survival_rate")
        if (
            isinstance(max_pending_per_shard, bool)
            or not isinstance(max_pending_per_shard, int)
            or max_pending_per_shard <= 0
        ):
            raise ValueError(f"Mutation target {name} has invalid max_pending_per_shard")
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


@pytest.mark.meta
def test_skip_xfail_importorskip_sites_have_reasons() -> None:
    missing_reasons = [site for site in _scan_debt_sites() if not site.reason_is_static]

    assert not missing_reasons, "\n".join(_format_site(site) for site in missing_reasons)


@pytest.mark.meta
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


@pytest.mark.meta
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


@pytest.mark.meta
def test_non_strict_xfails_are_explicitly_budgeted() -> None:
    sites = _scan_debt_sites()
    non_strict_or_ambiguous_xfails = {
        site.id for site in sites if site.kind == "pytest.mark.xfail" and site.strict is not True
    }

    assert non_strict_or_ambiguous_xfails == _EXPECTED_NON_STRICT_XFAIL_IDS


@pytest.mark.meta
def test_playwright_ci_retry_budget_is_pinned() -> None:
    source = (REPO_ROOT / "frontend" / "playwright.config.ts").read_text(encoding="utf-8")
    assert f"retries: process.env.CI ? {_PLAYWRIGHT_CI_RETRY_BUDGET} : 0," in source


@pytest.mark.meta
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


def test_frontend_scanner_keeps_adjacent_declarations_separate() -> None:
    source = (
        'test("ordinary case", () => {})\n'
        'test.skip("skipped case", () => {})\n'
        'it.only("focused case", () => {})\n'
    )
    sites = _scan_frontend_source(source, Path("frontend/src/example.test.ts"))

    assert [(site.line, site.callee, site.source) for site in sites] == [
        (2, "test.skip", 'test.skip("skipped case", () => {})'),
        (3, "it.only", 'it.only("focused case", () => {})'),
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


# ---------------------------------------------------------------------------
# Tests only Windows runs are run by the Windows job
# ---------------------------------------------------------------------------

# The jobs that run the whole suite are on Linux; the platform smoke job's Windows leg
# runs the modules and the tests its steps name.
_WINDOWS_JOB = "platform-smoke"
_CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
_LINUX = {"sys": SimpleNamespace(platform="linux"), "os": SimpleNamespace(name="posix")}
_WINDOWS = {"sys": SimpleNamespace(platform="win32"), "os": SimpleNamespace(name="nt")}
_PLATFORM_SYSTEM = {"linux": "Linux", "win32": "Windows"}
# A module, or a test or class in one, as pytest names it on its command line.
_SELECTION = re.compile(r"tests/[\w/]+\.py(?:::\w+)*")


def _holds_off_windows_only(condition: ast.expr) -> bool:
    """Whether *condition*, a skip's, holds on Linux and not on Windows, judged with only
    the platform known (``sys.platform``, ``os.name``, ``platform.system()``): a condition
    that reads anything else is not a platform's."""
    code = compile(ast.Expression(body=condition), "<skip condition>", "eval")

    def holds(names: dict[str, SimpleNamespace]) -> bool:
        system = _PLATFORM_SYSTEM[names["sys"].platform]
        scope = {**names, "platform": SimpleNamespace(system=lambda: system)}
        return bool(eval(code, {"__builtins__": {}}, scope))

    try:
        return holds(_LINUX) and not holds(_WINDOWS)
    except (AttributeError, NameError, TypeError):
        return False


def _keyword_value(call: ast.Call, name: str) -> ast.expr | None:
    return next((keyword.value for keyword in call.keywords if keyword.arg == name), None)


def _skipif_condition(node: ast.AST) -> ast.expr | None:
    """A ``skipif`` mark's condition (one written as a string, as pytest evaluates it)."""
    if not (isinstance(node, ast.Call) and ast.unparse(node.func).endswith("mark.skipif")):
        return None
    condition = node.args[0] if node.args else _keyword_value(node, "condition")
    if isinstance(condition, ast.Constant) and isinstance(condition.value, str):
        return ast.parse(condition.value.strip(), mode="eval").body
    return condition


class _SkipScopes(ast.NodeVisitor):
    """Each skip's condition, with the class and test it skips: a mark on a definition
    skips that definition, a mark or a skip inside one skips what holds it, and a mark
    made at module level (``pytestmark``, or a mark bound to a name) the whole module."""

    def __init__(self) -> None:
        self.scope: list[str] = []
        self.found: list[tuple[tuple[str, ...], ast.expr]] = []

    def _definition(self, node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.scope.append(node.name)
        for child in (*node.decorator_list, *node.body):
            self.visit(child)
        self.scope.pop()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._definition(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._definition(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._definition(node)

    def visit_Call(self, node: ast.Call) -> None:
        condition = _skipif_condition(node)
        if condition is not None:
            self.found.append((tuple(self.scope), condition))
        self.generic_visit(node)

    def visit_If(self, node: ast.If) -> None:
        if any(
            isinstance(call, ast.Call) and ast.unparse(call.func) == "pytest.skip"
            for statement in node.body
            for call in ast.walk(statement)
        ):
            self.found.append((tuple(self.scope), node.test))
        self.generic_visit(node)


def _skipped_off_windows(modules: Iterable[tuple[str, str]]) -> set[str]:
    """Of *modules* (path, source), the tests skipped on every system but Windows, as
    pytest names them: ``path::Class::test``, or the module's path when all of it is."""
    skipped: set[str] = set()
    for path, source in modules:
        scopes = _SkipScopes()
        scopes.visit(ast.parse(source))
        skipped.update(
            "::".join((path, *scope))
            for scope, condition in scopes.found
            if _holds_off_windows_only(condition)
        )
    return skipped


def _selected(test: str, selection: Iterable[str]) -> bool:
    """Whether a command line naming *selection* runs *test*: its module, its class or
    itself is named."""
    return any(test == named or test.startswith(f"{named}::") for named in selection)


_RUNNER_OS_CONDITION = re.compile(r"""^\s*runner\.os\s*(==|!=)\s*(['"])(\w+)\2\s*$""")


def _runs_on_windows(step: dict[str, Any]) -> bool:
    """Whether the job's *step* runs on its Windows leg: it has no condition, or one on
    ``runner.os`` alone that holds there. Any other condition cannot be told, and fails."""
    condition = step.get("if")
    if condition is None:
        return True
    match = _RUNNER_OS_CONDITION.match(str(condition))
    assert match is not None, (
        f"The {_WINDOWS_JOB} step {step.get('name')!r} runs if {condition!r}, which this "
        "check cannot judge: condition it on runner.os == or != a system alone."
    )
    operator, _quote, system = match.groups()
    # GitHub Actions compares strings without regard to case.
    return (system.casefold() == "windows") == (operator == "==")


def _windows_job_selection(workflow: dict[str, Any]) -> set[str]:
    """The modules and tests the Windows leg of *workflow*'s platform job runs: those
    named by its steps that run there."""
    job = workflow["jobs"][_WINDOWS_JOB]
    assert "windows-latest" in job["strategy"]["matrix"]["os"]
    return {
        named
        for step in job["steps"]
        if _runs_on_windows(step)
        for named in _SELECTION.findall(str(step.get("run", "")))
    }


@pytest.mark.meta
def test_every_test_skipped_off_windows_is_run_by_the_windows_job() -> None:
    """A test only Windows runs, which the Windows job does not name, runs in no job."""
    import yaml

    modules = (
        (path.relative_to(REPO_ROOT).as_posix(), path.read_text(encoding="utf-8"))
        for path in source_files(TESTS_DIR)
    )
    selection = _windows_job_selection(yaml.safe_load(_CI_WORKFLOW.read_text(encoding="utf-8")))
    left_out = sorted(
        test for test in _skipped_off_windows(modules) if not _selected(test, selection)
    )
    assert not left_out, (
        f"Name these tests, or their modules, in the {_WINDOWS_JOB} job's steps in "
        f".github/workflows/ci.yml: {left_out}"
    )


def test_a_skip_off_windows_is_told_from_any_other_and_placed() -> None:
    probe = textwrap.dedent(
        """
        import os, sys, pytest

        @pytest.mark.skipif(sys.platform != "win32", reason="Windows locks")
        def test_a(): ...

        class TestHeld:
            @pytest.mark.skipif("sys.platform != 'win32'", reason="as pytest reads it")
            def test_a2(self): ...

            def test_b(self):
                if os.name != "nt":
                    pytest.skip("Windows paths")

        @pytest.mark.skipif(not sys.platform.startswith("win"), reason="Windows")
        class TestWindows:
            def test_c(self): ...
        """
    )
    whole = (
        'import sys, pytest\npytestmark = pytest.mark.skipif(sys.platform != "win32", reason="x")\n'
    )
    others = textwrap.dedent(
        """
        import sys, pytest
        POSIX = pytest.mark.skipif(sys.platform == "win32", reason="chmod")
        LINUX = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="RLIMIT_AS")

        def test_c(flag):
            if flag:
                pytest.skip("not the platform's")
        """
    )

    assert _skipped_off_windows(
        [("tests/a.py", probe), ("tests/w.py", whole), ("tests/o.py", others)]
    ) == {
        "tests/a.py::test_a",
        "tests/a.py::TestHeld::test_a2",
        "tests/a.py::TestHeld::test_b",
        "tests/a.py::TestWindows",
        "tests/w.py",
    }
    for spelling in (
        'not sys.platform.startswith("win")',
        'platform.system() != "Windows"',
        'sys.platform != "win32"',
    ):
        assert _holds_off_windows_only(ast.parse(spelling, mode="eval").body), spelling


def test_a_test_is_run_when_its_module_its_class_or_itself_is_named() -> None:
    named = {"tests/a.py", "tests/b.py::TestHeld", "tests/c.py::test_one"}

    assert _selected("tests/a.py::TestX::test", named)
    assert _selected("tests/b.py::TestHeld::test_b", named)
    assert _selected("tests/c.py::test_one", named)
    assert not _selected("tests/b.py::TestOther::test", named)
    assert not _selected("tests/c.py::test_one_more", named)
    assert not _selected("tests/d.py", named)


def test_the_windows_job_runs_only_the_steps_its_windows_leg_runs() -> None:
    def workflow(*steps: dict[str, str]) -> dict[str, Any]:
        job = {"strategy": {"matrix": {"os": ["windows-latest"]}}, "steps": list(steps)}
        return {"jobs": {_WINDOWS_JOB: job}}

    steps = (
        {"run": "uv run pytest tests/a.py tests/h.py::TestHeld::test_b -q"},
        {"if": "runner.os != 'Windows'", "run": "uv run pytest tests/b.py"},
        {"if": "runner.os == 'Windows'", "run": "uv run pytest tests/c.py"},
        {"if": 'runner.os == "macOS"', "run": "uv run pytest tests/d.py"},
        {"if": "runner.os != 'windows'", "run": "uv run pytest tests/f.py"},
        {"if": "runner.os == 'WINDOWS'", "run": "uv run pytest tests/g.py"},
    )
    assert _windows_job_selection(workflow(*steps)) == {
        "tests/a.py",
        "tests/h.py::TestHeld::test_b",
        "tests/c.py",
        "tests/g.py",
    }

    unjudged = {"name": "Matrix", "if": "matrix.os == 'windows-latest'", "run": "tests/e.py"}
    with pytest.raises(AssertionError, match="cannot judge"):
        _windows_job_selection(workflow(unjudged))


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
