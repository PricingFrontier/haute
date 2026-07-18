# CI Mirror — prove a PR is green before pushing

Reproduce every PR-gating CI check locally, so a green local run is strong
evidence the PR will pass CI — without burning a CI round per attempt. This is
a reusable per-PR procedure for the haute repo.

## Why this exists

CI failures are cheapest to diagnose before they happen. "We reproduced the
failures we hit" is not "we showed no others remain"; this procedure closes
that gap by enumerating *every* gate, mapping each to a local command, and
naming what genuinely cannot be mirrored.

The existing `scripts/preflight.sh` already mirrors most of the backend split
(`backend-static` + the coverage-authority pair, on the reference
interpreter), plus `frontend` and `init-smoke` wholesale. This procedure
covers the remaining `ci.yml` jobs (`dependency-floors`, `backend-compat`,
the non-blocking `backend-314-probe`, `perf`, both optional-deps smokes,
`package-smoke`, `platform-smoke`, `mutation-config-smoke`, `browser-e2e`)
plus the conditional `mutation.yml` and `docs.yml` workflows, and states the
residual deficiencies explicitly.

## CI gate inventory

Source of truth: `.github/workflows/`. Six workflow files. A check only
matters for a PR if it runs on `pull_request: branches: [main]`.

| Workflow | Job | Trigger | PR-gating? | What it runs |
|---|---|---|---|---|
| `ci.yml` | `canary` | push+PR | **Yes** | `uv sync --group dev --locked` → `ruff check --output-format=github` + `ruff format --check` → pytest on the core test subset (`scripts/core_test_files.txt`, 8 files, `-n 4 --timeout=60`) |
| `ci.yml` | `init-smoke` (×3: ubuntu, windows, macos) | push+PR | **Yes** | `uv run --no-project python scripts/init_smoke.py` — wheel build (frontend included) → fresh venv, fresh resolve → `haute init` in an empty dir → headless `haute serve` → authed `/api/files` → clean shutdown |
| `ci.yml` | `dependency-floors` | push+PR | **Yes** | `uv lock --resolution lowest-direct` (py3.11) → `uv sync --frozen --group dev` → core test subset run at the re-resolved floor lockfile — proves the published floor specifiers actually install and pass |
| `ci.yml` | `backend-static` | push+PR | **Yes** | `uv sync --group dev --locked` (py3.12) → ruff lint + ruff format-check + mypy + `HAUTE_BUILD_FRONTEND=1 uv build` |
| `ci.yml` | `backend-coverage-shard` (×2 shards) | push+PR | **Yes** | full suite split 2-way via `pytest-split` (py3.12), coverage collected per-shard (`--cov-fail-under=0`), uploaded as an artifact |
| `ci.yml` | `backend-coverage-gate` | push+PR | **Yes** | needs `backend-coverage-shard` → `coverage combine` the two shards → `coverage report --fail-under=90` → `scripts/check_critical_coverage.py` |
| `ci.yml` | `backend-compat` (×2: py3.11, py3.13) | push+PR | **Yes** | full suite, no coverage collected |
| `ci.yml` | `backend-314-probe` | push+PR | **No** (`continue-on-error: true`; advisory only) | py3.14 forward-looking probe: `uv sync` (expected to fail until catboost ships cp314 wheels) → full suite if sync succeeds |
| `ci.yml` | `perf` | push+PR | **Yes** | `uv run python scripts/run_perf_suite.py --output-dir .cache/perf` |
| `ci.yml` | `optional-deps-smoke` | push+PR | **Yes** | core install (`uv sync --locked --no-group dev` + `uv pip install pytest pytest-asyncio httpx`) → `pytest tests/test_optional_dependency_matrix.py -q` |
| `ci.yml` | `optional-deps-present-smoke` | push+PR | **Yes** | `uv sync --group dev --locked` → `pytest tests/test_optional_dependency_extras.py -q` |
| `ci.yml` | `package-smoke` | push+PR | **Yes** | `HAUTE_BUILD_FRONTEND=1 uv build --sdist --wheel` → install wheel into fresh venv → `package_smoke_check.py` + `haute --help`; repeat for sdist |
| `ci.yml` | `platform-smoke` (×2: windows-latest, macos-latest) | push+PR | **Yes** | `pytest tests/test_path_resolution.py tests/test_pipeline_runtime_path_validation.py tests/test_test_debt.py tests/test_file_ops.py tests/test_write_sandbox_guard.py tests/test_data_io_roundtrips.py -q` |
| `ci.yml` | `mutation-config-smoke` | push+PR | **Yes** | `uv run python scripts/run_mutation_suite.py --dry-run --output-dir .mutation-plan --run-id ci` |
| `ci.yml` | `frontend` | push+PR | **Yes** | `npm ci` → `bash scripts/preflight.sh --frontend-only` |
| `ci.yml` | `browser-e2e` | push+PR | **Yes** | `npm ci` → `playwright install --with-deps chromium firefox` → `npm run test:e2e` |
| `mutation.yml` | `plan` → `shard` (matrix) → `mutation` (gate) | PR **iff** `src/haute/**/*.py`, `tests/**/*.py`, `mutation/**`, `scripts/run_mutation_suite.py`, or the workflow changed | **Conditional** | `plan` selects targets and builds the shared Cosmic Ray session; matrix `shard` jobs execute disjoint mutant subsets in parallel; `mutation` merges shard results and checks survival thresholds — equivalent to one bounded run (`run_mutation_suite.py --changed-files-from <difflist>`) |
| `docs.yml` | `build`+`deploy` | push to main **iff** `docs/**` or `mkdocs.yml` changed (NOT on PR) | **Post-merge** | `uv run mkdocs build --strict` → deploy to GitHub Pages |
| `performance.yml` | `python-perf` + `frontend-performance` | `workflow_dispatch` + weekly cron (Mon 03:17 UTC) | **No** | perf lanes; not a PR gate (see Deficiencies) |
| `dependencies.yml` | `unlocked-resolve` | `workflow_dispatch` + weekly cron (Mon 04:41 UTC) | **No** | fresh unlocked-resolve smoke: builds the wheel, resolves latest-within-caps deps (incl. `databricks` extra) with no lockfile, runs `init_smoke.py` + the core test subset against it; not a PR gate — opens/updates a `dependency-watch` issue on failure |
| `frontend-shuffle.yml` | `frontend-shuffle` | `workflow_dispatch` + nightly cron (02:07 UTC) | **No** | frontend vitest suite under `--sequence.shuffle` to catch within-file test-order dependence; not a PR gate (ruled non-required 2026-07-15) — opens/updates a `shuffle-watch` issue on failure |

## Local mirror — gate by gate

`R` = repo root. All commands assume cwd = `R` unless noted.

### Covered by `preflight.sh` today

1. **`backend-static` + `backend-coverage-shard`/`backend-coverage-gate`** →
   `bash scripts/preflight.sh --backend-only` — runs ruff lint, ruff
   format-check, mypy, pytest collect, pytest + 90% global coverage +
   `scripts/check_critical_coverage.py`, and `HAUTE_BUILD_FRONTEND=1 uv build`,
   all in one local pass. **Gap vs CI:** CI splits this across three parallel
   jobs (static gates + build on py3.12; two coverage shards; a gate job that
   combines them and enforces 90%) rather than one job, and separately runs
   `backend-compat` (full suite, **no** coverage) on py3.11 and py3.13, plus a
   non-blocking `backend-314-probe`. Preflight itself still runs one
   interpreter (the `.venv`). See "Multi-Python matrix" below.
2. **`frontend`** → `bash scripts/preflight.sh --frontend-only` — runs
   `npm run typecheck`, `npm run lint`, `npm run build`, `npm run check:bundle`,
   `npm run test:coverage`.
3. **`init-smoke`** → `bash scripts/preflight.sh --init-smoke` (Windows:
   `preflight.ps1 -InitSmoke`) — the exact script the CI job runs
   (`scripts/init_smoke.py`). Local runs mirror the **macOS leg**; the
   ubuntu/windows legs are CI-only (Deficiency 1).
4. **`canary`** → no separate local run needed: it is a strict subset of
   `--backend-only` (ruff lint/format + the eight test files in
   `scripts/core_test_files.txt`, all of which the full suite already
   contains), so a green `--backend-only` implies a green canary modulo
   runner load. To run the exact subset (matches how `canary`,
   `dependency-floors`, and the scheduled `dependencies.yml` unlocked-resolve
   lane all invoke it):
   `uv run pytest $(grep -v '^#' scripts/core_test_files.txt) -q -n 4 --timeout=60 --timeout-method=signal`.

### NOT covered by `preflight.sh` — the additions this procedure exists for

5. **`dependency-floors`** → re-resolve `uv.lock` at the published floors and
   run the core subset against them (py3.11, the `requires-python` floor):
   ```bash
   uv lock --resolution lowest-direct
   UV_PROJECT_ENVIRONMENT=.venv-floors uv sync --frozen --group dev --python 3.11
   UV_PROJECT_ENVIRONMENT=.venv-floors uv run --frozen --no-sync pytest \
     $(grep -v '^#' scripts/core_test_files.txt) -q -n 4 --timeout=60 --timeout-method=signal
   git checkout -- uv.lock   # restore the highest-resolution lockfile — never commit the floor re-lock
   ```
   `--frozen` (not `--locked`) matters both times: `uv lock --resolution
   lowest-direct` deliberately produces a lockfile that diverges from the
   default `highest` resolution, and any bare `uv sync`/`uv run` would re-lock
   at `highest` and silently swap the floor env back before testing. This is
   the lane that proves the floor specifiers in `[project] dependencies`
   (and the extras pulled in via the dev group's `haute[databricks]`) are
   honest — a red run means a dishonest floor, and raising it is a maintainer
   decision, not something a PR does unilaterally.
6. **`perf`** → `uv run python scripts/run_perf_suite.py --output-dir .cache/perf`
   (or `preflight.sh --backend-only --perf`, which wraps the same call).
7. **`optional-deps-smoke`** → **separate venv + direct interpreter** (NOT
   `uv run`). `uv run` re-syncs by default and would rip out the
   manually-installed pytest/httpx (they aren't project deps). Run:
   ```bash
   uv venv .venv-coreonly --python 3.12
   UV_PROJECT_ENVIRONMENT=.venv-coreonly uv sync --locked --no-group dev
   uv pip install --python .venv-coreonly/bin/python pytest pytest-asyncio httpx
   .venv-coreonly/bin/python -m pytest tests/test_optional_dependency_matrix.py -q
   ```
   This is the lane that proves haute imports and runs with **no**
   MLflow/Databricks extras — i.e. that lazy-loading of optional deps actually
   holds. The `--python 3.12` matches CI's interpreter; the direct
   `.venv-coreonly/bin/python -m pytest` call avoids the `uv run` re-sync trap.
8. **`optional-deps-present-smoke`** →
   `uv run pytest tests/test_optional_dependency_extras.py -q` (dev group
   present; safe in the normal `.venv`).
9. **`package-smoke`** ← **THE GAP THAT CATCHES A DEPENDENCY YANK.** Build the
   artifacts and install each into a throwaway venv, exactly as CI does (clean
   stale build dirs first, or the `*.whl` glob matches multiple files on rerun):
   ```bash
   rm -rf dist-smoke .pkg-wheel .pkg-sdist
   HAUTE_BUILD_FRONTEND=1 uv build --out-dir dist-smoke --sdist --wheel
   uv venv .pkg-wheel --python 3.12
   uv pip install --python .pkg-wheel/bin/python dist-smoke/*.whl
   ./.pkg-wheel/bin/python scripts/package_smoke_check.py
   ./.pkg-wheel/bin/haute --help >/dev/null
   uv venv .pkg-sdist --python 3.12
   uv pip install --python .pkg-sdist/bin/python dist-smoke/*.tar.gz
   ./.pkg-sdist/bin/python scripts/package_smoke_check.py
   ./.pkg-sdist/bin/haute --help >/dev/null
   ```
   The `uv pip install …whl` step does a **fresh resolve of haute's published
   `[project] dependencies` against live PyPI, with no lockfile** — which is
   precisely why it (and only it) surfaces a yanked-transitive failure (e.g. a
   pinned `polars==X` whose `polars-runtime-32==X` got yanked). Running this
   locally catches that before the push. **This is the single highest-value
   addition.** Arch caveat: locally this resolves **macOS-arm64** wheels; CI
   resolves **linux-x86-64**. catboost / rustystats / polars / price-contour
   ship per-arch wheels, so this lane catches a version yank that hits *both*
   arches but NOT a missing/yanked *linux-only* wheel for a pinned version.
   Docker (linux container) is the only local way to close that — see
   Deficiencies. Also note `HAUTE_BUILD_FRONTEND=1` is behaviour-flipping (it
   makes `uv build` compile the frontend into the wheel via `hatch_build.py`,
   which shells into `npm` — so Node must be on PATH); without it the
   package-smoke `_assert_static_assets_present()` check fails.
10. **`platform-smoke`** → macOS leg runs locally:
   `uv run pytest tests/test_path_resolution.py tests/test_pipeline_runtime_path_validation.py tests/test_test_debt.py tests/test_file_ops.py tests/test_write_sandbox_guard.py tests/test_data_io_roundtrips.py -q`.
   **Windows leg: cannot be mirrored on macOS** — see Deficiencies.
11. **`mutation-config-smoke`** →
   `uv run python scripts/run_mutation_suite.py --dry-run --output-dir .mutation-plan --run-id ci`.
12. **`browser-e2e`** →
   `cd frontend && ./node_modules/.bin/playwright install chromium firefox && CI=1 npm run test:e2e`.
   **`CI=1` matters**: `frontend/playwright.config.ts` branches on it — with
   `CI`, retries are 2 (vs 0 locally) and `reuseExistingServer` flips, so a
   local zero-retry run can red on a flake where CI's 2-retry run would pass
   (and vice-versa on server reuse). Drop `--with-deps` locally (it `apt-get`s
   OS libs on Linux; macOS installs browsers without it) — see Deficiencies
   for that caveat.

### Conditional workflows

13. **`mutation.yml`** — runs on PR **only if** `src/haute/**/*.py`,
    `tests/**/*.py`, `mutation/**`, `scripts/run_mutation_suite.py`, or the
    workflow itself changed. CI itself now runs this as three jobs
    (`plan` → matrix `shard` → `mutation` gate) so the mutant execution can be
    sharded across parallel runners; because every mutant still runs exactly
    once with the identical test command and timeout, the sharded result is
    equivalent to one unsharded run. Local mirror (unsharded — the script's
    `--phase` flag is CI-orchestration-only; omitting it runs a self-contained
    local pass):
    ```bash
    git diff --name-only origin/main HEAD > .mutation-changed-files.txt
    uv run python scripts/run_mutation_suite.py --output-dir mutation-artifacts --changed-files-from .mutation-changed-files.txt
    ```
    Mutation testing mutates **source** (`src/haute`), so a PR that changed only
    a *test* file should map to few/zero mutation targets and finish fast —
    **but confirm**, don't assume; the `--dry-run` from gate 11 reports the
    target plan cheaply first. (A test-only diff is *not* universally cheap: if
    it touches a target's declared `test_paths`, or touches `mutation/targets.json`,
    the full bounded run is selected.)
14. **`docs.yml`** — not a PR gate, but a **post-merge** gate: a PR that touches
    `docs/**` or `mkdocs.yml` won't fail the PR, yet **will** fail the docs
    deploy when it lands on main if `mkdocs build --strict` breaks. Mirror when
    docs change:
    ```bash
    uv run mkdocs build --strict
    ```

## Deficiencies — what cannot be faithfully mirrored locally

1. **Windows legs of `platform-smoke` and `init-smoke`.** No Windows host here.
   `scripts/preflight.ps1` exists for a real Windows machine (including
   `-InitSmoke`), but on macOS these legs are unmirrorable. For platform-smoke:
   of the six tests it runs, `tests/test_test_debt.py` is AST-static (parses
   files, doesn't execute them) and so is already covered by the macOS leg; the
   genuinely Windows-only risk is narrower — the `os.name == "nt"`-gated paths in
   `tests/test_path_resolution.py`, `tests/test_pipeline_runtime_path_validation.py`,
   `tests/test_file_ops.py`, and `tests/test_write_sandbox_guard.py` (sandbox
   HOME/USERPROFILE redirection), plus the platform-sensitive file-IO paths in
   `tests/test_data_io_roundtrips.py`. For init-smoke: the Windows console-signal
   shutdown path (`CTRL_BREAK_EVENT` → uvicorn graceful stop) is exercised only
   on the windows-latest runner. Rely on CI for these legs, or run
   `preflight.ps1` on a Windows box if a change touches path logic or the smoke.
2. **OS/arch mismatch (macOS aarch64 vs CI ubuntu-latest x86-64).** Every
   "linux" CI job actually runs on Linux x86-64; we run macOS arm64. This
   produces **both** false-passes (passes locally, fails on Linux) **and**
   false-fails (a macOS-only quirk, e.g. the known
   `test_memory_cap_is_applied_inside_child_process` RLIMIT difference, that
   would pass on Linux). **Mitigation (optional, heavyweight):** run the
   linux-shaped gates inside a `ghcr.io/astral-sh/uv` or `ubuntu:24.04` Docker
   container to match arch+OS. Worth it only when a change is OS/arch-sensitive
   (path handling, subprocess/resource limits, native wheels).
3. **Linux wheel availability (the arch-specific slice of `package-smoke`).**
   Distinct from #2: `package-smoke`'s fresh-resolve installs per-arch binary
   wheels (catboost, rustystats, polars, price-contour). Locally it resolves
   **macOS-arm64** wheels, so it proves *macOS* installability of the pins, not
   *linux-x86-64* installability. A pinned version whose linux wheel is missing
   or yanked but whose macOS wheel is present passes local package-smoke and
   fails CI's. Docker (linux container running the same `uv build` + fresh-venv
   install) is the **only** local mirror for this, and is the strongest reason
   to reach for Docker on any PR that changes `pyproject.toml`, `uv.lock`, or
   `hatch_build.py`.
4. **`playwright install --with-deps`.** The `--with-deps` flag `apt-get`s
   system libraries on the Linux runner; it's a no-op/unavailable on macOS.
   Local e2e installs browsers without it, so we exercise the *tests* but not
   the *system-dependency install path*. Low risk (that path rarely breaks),
   but it is not mirrored.
5. **Multi-Python matrix.** `preflight.sh` runs one interpreter (3.12,
   matching `backend-coverage-shard`/`backend-coverage-gate`). CI additionally
   runs the full suite **without coverage** on py3.11 and py3.13
   (`backend-compat`), re-resolves and tests at the published dependency
   floors on py3.11 (`dependency-floors` — mirrored above, gate 5), and runs a
   **non-blocking** py3.14 probe (`backend-314-probe`, excluded — see
   Justifications). uv can fetch 3.11/3.13 locally; to mirror `backend-compat`,
   re-run the suite under each (see runlist Step A-matrix). Running the *full*
   backend preflight (including the 90% coverage gate) under 3.11/3.13, as the
   runlist does, is a **stricter** check than `backend-compat` itself performs
   (which drops coverage) — that's fine, just not an exact 1:1 mirror. Cost: 2×
   the backend test time. **Residual risk if skipped:** version-conditional
   code paths (`sys.version_info` branches, stdlib behaviour differences).
6. **Live-PyPI timing for fresh-resolve lanes.** `package-smoke` and
   `optional-deps-smoke` resolve against live PyPI. A yank/upload between the
   local run and the CI run is a (small) window where local-green ≠ CI-green.
   Unavoidable; note it, don't chase it.
7. **`performance.yml` (workflow_dispatch + weekly cron).** Excluded because it
   is **not a PR gate** — nothing in it can fail a PR. Note the `frontend-performance`
   job (`npm run analyze:bundle`, `npm run test:e2e:benchmark`) is mirrored by
   **nothing** in the runlist, and `tests/test_performance_docs.py` does NOT
   cover it (that meta-test asserts only that certain *strings* exist in the
   workflow YAML and in a markdown doc — it executes none of the perf lanes).
   If "no surprises when the weekly perf run fires" matters, run those two
   `npm` lanes manually. `npm run check:bundle` (in the frontend preflight) is a
   size budget, related to but not the same as `analyze:bundle` (sourcemap
   analysis).
8. **GitHub-only steps.** Artifact upload (`actions/upload-artifact`), Pages
   deploy (`actions/deploy-pages`), and runner provisioning are not gates — the
   gate is the build/test step *before* the upload. Excluded with justification.

## The runlist — ordered, copy-pasteable, for a standard PR

Fast-failing order (cheapest/most-likely-to-fail first). Stop at the first
failure, fix, restart from the top of the affected block.

```bash
# --- A. Static + unit gates, default interpreter (ci.yml backend-static + backend-coverage-shard/gate + frontend + canary) ---
# (Node must be on PATH: preflight's `uv build` shells into npm via hatch_build.py.)
bash scripts/preflight.sh --backend-only      # ruff, ruff-format, mypy, pytest+cov(90%)+critical-cov, uv build
bash scripts/preflight.sh --frontend-only     # tsc, eslint, vite build, bundle budget, vitest+cov
# canary is a strict subset of --backend-only (ruff + the eight files in scripts/core_test_files.txt); no separate run needed.

# --- A-matrix. backend-compat: full suite, no coverage, on the supported version edges (ci.yml backend-compat) ---
# ruff/mypy/format/build are config-pinned to py311 semantics (ruff target-version=py311,
# mypy python_version=3.11) → interpreter-invariant; only the test suite varies by interpreter.
# CI's backend-compat DROPS coverage on 3.11/3.13 (coverage is enforced once, on 3.12, by
# backend-coverage-gate). Running the *full backend preflight* (incl. coverage) under each below
# is therefore a superset of backend-compat, not an exact 1:1 mirror — the stricter check is
# harmless. Each interpreter gets its OWN env (never the ambient .venv, or you clobber the env
# gates A–D depend on):
for PY in 3.11 3.13; do
  UV_PROJECT_ENVIRONMENT=.venv-$PY uv sync --group dev --locked --python $PY \
    || { echo "sync FAIL $PY"; break; }
  UV_PROJECT_ENVIRONMENT=.venv-$PY uv run --no-sync bash scripts/preflight.sh --backend-only \
    || { echo "preflight FAIL $PY"; break; }
done
# To match backend-compat exactly (full suite, no coverage) rather than the stricter superset above:
#   UV_PROJECT_ENVIRONMENT=.venv-$PY uv run --no-sync pytest tests/ -q -n 4 --timeout=60 --timeout-method=signal
# A bare `pytest tests/` loop with no coverage is what backend-compat itself runs on 3.11/3.13 —
# but it is NOT an acceptable substitute on 3.12, the reference interpreter backend-coverage-gate
# enforces 90% + critical-coverage against.

# --- A-floors. Dependency floors at re-resolved lowest-direct (ci.yml dependency-floors) ---
uv lock --resolution lowest-direct
UV_PROJECT_ENVIRONMENT=.venv-floors uv sync --frozen --group dev --python 3.11
UV_PROJECT_ENVIRONMENT=.venv-floors uv run --frozen --no-sync pytest \
  $(grep -v '^#' scripts/core_test_files.txt) -q -n 4 --timeout=60 --timeout-method=signal
git checkout -- uv.lock   # restore the highest-resolution lockfile — never commit the floor re-lock

# --- B. Smokes preflight.sh omits ---
uv run pytest tests/test_optional_dependency_extras.py -q          # optional-deps-present-smoke
git diff --name-only origin/main HEAD > .mutation-changed-files.txt   # two-dot diff to match CI's base..head
uv run python scripts/run_mutation_suite.py --dry-run --changed-files-from .mutation-changed-files.txt --output-dir .mutation-plan --run-id ci   # mutation-config-smoke + preview THIS PR's target set
uv run pytest tests/test_path_resolution.py tests/test_pipeline_runtime_path_validation.py tests/test_test_debt.py tests/test_file_ops.py tests/test_write_sandbox_guard.py tests/test_data_io_roundtrips.py -q   # platform-smoke (macOS leg; Windows leg unmirrorable)
uv run python scripts/run_perf_suite.py --output-dir .cache/perf   # perf
bash scripts/preflight.sh --init-smoke   # init-smoke (macOS leg; ubuntu/windows legs CI-only). ~1-2 min warm.

# --- C. optional-deps-smoke: SEPARATE env + DIRECT interpreter (never `uv run` a hand-curated env) ---
uv venv .venv-coreonly --python 3.12
UV_PROJECT_ENVIRONMENT=.venv-coreonly uv sync --locked --no-group dev
uv pip install --python .venv-coreonly/bin/python pytest pytest-asyncio httpx
.venv-coreonly/bin/python -m pytest tests/test_optional_dependency_matrix.py -q

# --- D. Package build + fresh-install (THE dependency-yank gate) ---
rm -rf dist-smoke .pkg-wheel .pkg-sdist        # clean, or the *.whl/*.tar.gz globs match stale files
HAUTE_BUILD_FRONTEND=1 uv build --out-dir dist-smoke --sdist --wheel
uv venv .pkg-wheel --python 3.12 && uv pip install --python .pkg-wheel/bin/python dist-smoke/*.whl && ./.pkg-wheel/bin/python scripts/package_smoke_check.py && ./.pkg-wheel/bin/haute --help >/dev/null
uv venv .pkg-sdist --python 3.12 && uv pip install --python .pkg-sdist/bin/python dist-smoke/*.tar.gz && ./.pkg-sdist/bin/python scripts/package_smoke_check.py && ./.pkg-sdist/bin/haute --help >/dev/null
# Resolves macOS-arm64 wheels locally; CI resolves linux-x86-64. Catches both-arch yanks, NOT a
# linux-only missing wheel — Docker is the only local mirror for that (Deficiencies).

# --- E. Browser e2e (CI=1 so retries/reuseExistingServer match CI) ---
( cd frontend && ./node_modules/.bin/playwright install chromium firefox && CI=1 npm run test:e2e )

# --- Conditional (run iff the relevant paths changed) ---
# docs/** or mkdocs.yml changed (post-merge gate):
uv run mkdocs build --strict
# src/haute/** or tests/** changed (bounded mutation — slow; the gate-B dry-run previews targets first):
uv run python scripts/run_mutation_suite.py --output-dir mutation-artifacts --changed-files-from .mutation-changed-files.txt
```

## Justifications for everything passed over

- **Windows `platform-smoke`** — physically unmirrorable on macOS. Mitigation
  named (`preflight.ps1` / CI). Accepted residual.
- **`--with-deps` on playwright** — Linux system-lib install; not a code gate.
  The e2e *tests* are mirrored; only the apt step isn't. Accepted residual
  (low risk).
- **`performance.yml`** — excluded because it is **not a PR gate** (it runs on
  `workflow_dispatch` + weekly cron only; nothing in it can fail a PR). The
  honest reason is "non-gating", not "covered" — `tests/test_performance_docs.py`
  only asserts that certain *strings* exist in the workflow YAML and a markdown
  doc; it executes none of the perf lanes, and the `frontend-performance` job
  (`analyze:bundle`, `test:e2e:benchmark`) is not mirrored locally at all.
  Gate B's `run_perf_suite.py` runs the Python perf lane, which mirrors
  `ci.yml`'s `perf` job, not `performance.yml`.
- **`docs.yml` deploy step** — Pages deployment is not a correctness gate; the
  gate is `mkdocs build --strict`, which is mirrored (conditional gate 14).
  Note `mkdocstrings` imports from `src`, so `mkdocs build --strict` needs the
  package importable — the dev group provides that. Docs CI runs on 3.11, so
  this is the one lane where the local 3.11 `.venv` and CI agree on interpreter.
- **Artifact-upload steps across all jobs** — `actions/upload-artifact` /
  `upload-pages-artifact` are post-hoc (`if: always()`); they cannot fail the
  underlying check. Excluded.
- **Multi-Python matrix** — *not* passed over; `backend-compat` (3.11/3.13) is
  mirrored in A-matrix and `dependency-floors` (3.11, re-resolved) in
  A-floors. Listed as a deficiency only in the sense that the default
  `preflight.sh` runs one interpreter, so both must be run explicitly.
- **`backend-314-probe`** — excluded because it is **non-blocking**
  (`continue-on-error: true`): it can go red without failing the PR check. It
  exists to give early warning of a cp314 wheel gap (catboost is the likely
  blocker), not to gate anything. If you have Python 3.14 locally, `uv sync
  --group dev --locked --python 3.14` reproduces the same fail-fast signal;
  not added to the runlist since a red result can never block a push.
- **`dependencies.yml` (`unlocked-resolve`)** — excluded because it is **not a
  PR gate** (`workflow_dispatch` + weekly cron only). Like `performance.yml`,
  it deliberately tests the *index's* state, not the *diff's*, so it must not
  block unrelated PRs; a failure opens/updates a `dependency-watch` issue
  instead. Its core-subset assertion is already covered locally by the
  `dependency-floors` mirror (A-floors) and the canary subset (gate 4), just
  against a different resolution (latest-within-caps vs. locked vs. floors).
- **`frontend-shuffle.yml`** — excluded because it is **not a PR gate**
  (`workflow_dispatch` + nightly cron; explicitly ruled non-required
  2026-07-15, see the workflow's own comment). It catches within-file
  test-order dependence in the vitest suite, a slow-moving regression class
  judged not worth charging every PR for. A failure opens/updates a
  `shuffle-watch` issue with the exact seed to reproduce; run `npm run
  test:shuffle` in `frontend/` manually if a change is suspicious for
  cross-test state leakage (uncleared mock spies, unconsumed
  `mockImplementationOnce`, DOM/store state not reset).

## Residual risk after a fully-green local run

Even with every mirrorable gate green locally, a PR can still fail CI on: (1)
the Windows leg, (2) a Linux-x86-64-only behaviour, (3) a PyPI state change in
the resolve window. These are bounded and named. Everything else — lint, types,
unit + coverage gates, optional-dep smokes, package build+install (incl.
fresh-resolve yank detection), mutation config, e2e, docs build — is
reproducible locally and should be run before every push per the runlist above.

## Review history

The first revision of this procedure was challenged by an adversarial Opus
review whose explicit stance was to contest every exclusion and every
"can't be mirrored" claim. It found the procedure did **not** yet support its
headline guarantee ("green locally ⇒ green on CI, modulo named deficiencies")
and surfaced eight must-fixes, all since incorporated:

1. **Multi-Python matrix was not actually mirrored.** The original loop ran a
   bare, coverage-less `pytest` on 3.12/3.13, while CI runs the *full*
   `preflight.sh --backend-only` (including the 90% coverage gate and
   `check_critical_coverage.py`) under each interpreter — dressed up as
   "mirrored" but giving false confidence. Now runs the full backend preflight
   per interpreter in isolated envs (runlist A-matrix).
2. **`optional-deps-smoke` recipe was self-defeating.** `uv run` re-syncs the
   project env by default, ripping out the hand-installed pytest/httpx. Fixed
   by using a fully separate `uv venv` + a direct `.venv-coreonly/bin/python -m
   pytest` invocation, pinned to `--python 3.12` to match CI (runlist C).
3. **False `performance.yml` exclusion rationale.** An earlier draft claimed it
   was "covered indirectly by `test_performance_docs.py`"; that meta-test only
   asserts strings exist in YAML/markdown and executes no perf lane. Rationale
   corrected to the honest "non-gating", with the unmirrored `frontend-performance`
   lane named (Deficiency 7).
4. **Wrong `git diff` base for the mutation target selection.** The original
   three-dot `origin/main...HEAD` diverges from CI's two-endpoint base..head
   whenever main has advanced, changing which mutation targets get selected.
   Fixed to two-dot `git diff origin/main HEAD`, and a `--dry-run
   --changed-files-from` preview was added so the selected subset is shown
   before any slow run.
5. **`browser-e2e` missing `CI=1`.** `playwright.config.ts` branches on `CI`
   for retry count (2 vs 0) and `reuseExistingServer`; without it local results
   diverge from CI. `CI=1` added to the runlist (gate 12 / runlist E).
6. **`package-smoke` ergonomics/safety.** Stale `dist-smoke` globs match
   multiple artifacts on rerun, and the matrix loop could clobber the ambient
   `.venv` that earlier gates depend on. Added `rm -rf` cleanup and per-leg
   `UV_PROJECT_ENVIRONMENT` isolation.
7. **`package-smoke` oversold as bulletproof.** It resolves macOS-arm64 wheels,
   not the linux-x86-64 wheels CI resolves, so a linux-only missing/yanked
   wheel passes locally and fails CI. Arch-sensitivity caveat added (gate 9 /
   Deficiency 3), with Docker named as the only local mirror.
8. **Undocumented Node 22 / `HAUTE_BUILD_FRONTEND` coupling.** The backend
   `uv build` path shells into npm via `hatch_build.py`, so Node must be on
   PATH; `HAUTE_BUILD_FRONTEND` is a behaviour-flipping env var (validate
   committed assets vs rebuild frontend). Both are now called out where the
   build commands appear.
