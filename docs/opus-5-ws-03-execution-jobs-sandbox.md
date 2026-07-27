# WS-03 — Execution engine, background jobs & sandbox

Part of the Opus 5 review split (`opus-5-workstreams.md`). Evidence and fix guidance:
`opus-5-review.md`. Owner: WS-03 · Status: delivered in PR #138.

**Branch:** `opus5/ws-03-execution-jobs-sandbox`

## Mission

The runtime spine: lazy execution, chunking, admission, worker isolation and the shared job
store/lifecycle, plus the sandbox that contains user code. Carries two of the review's
Wave-2 security items and the worker-transport correctness fixes, and reconciles the
heavily-contracted execution-engine and background-jobs specs.

## Scope

| Component | C | H | M | L |
|---|---:|---:|---:|---:|
| execution-engine | 0 | 2 | 10 | 4 |
| background-jobs | 0 | 3 | 9 | 8 |
| sandbox-security | 0 | 2 | 4 | 4 |
| cross-cutting (assigned) | 0 | 0 | 0 | 1 |
| **Total** | **0** | **7** | **23** | **17** |

## Priorities

**P1 — security (review Wave 2):**

- `sandbox-security-1` (H): `str.format` dunder guard bypassable with a nested format spec
  (`{0.__globals__[X]:{w}}` validates clean) — parse with `string.Formatter().parse()` or
  reject non-bare field names; regression test for the nested-spec PoC.
- `execution-engine-3` (M): unvalidated node ids interpolated into checkpoint filenames —
  writes outside the checkpoint directory.
- `sandbox-security-2` (M): `f = exit; f()` raises SystemExit — blocked only at AST layer,
  absent from `_BLOCKED_BUILTINS`.

**P1 — worker/job correctness:**

- `execution-engine-2` (M): `run_isolated_worker` joins the child without draining the
  result queue — large results hang or are misreported as timeouts.
- `background-jobs-2` (M): parent cleanup failure after a committed publication discards the
  completed result and marks the job `error`.
- `background-jobs-3` (M): request-cancellation path can propagate the worker's exception
  instead of re-raising `CancelledError`.
- `seam-exec-10` (M): legacy isolated-worker path reports cancellation success without
  confirming the child died.
- `execution-engine-10` (L): non-`IsolatedWorkerError` exceptions skip cleanup callbacks and
  orphan the child.
- `execution-engine-4` (M): chunk byte budget silently exceeded when row-expansion exceeds
  the computed chunk size; `execution-engine-9` (L): RSS sampler `None` silently disables
  the memory budget mid-run.

**P2 — policy:** `failure-model-6` (cross-cutting): four contradictory numeric-env failure
policies; a malformed `HAUTE_SOLVER_TIMEOUT` silently removes the solve timeout. Decide one
policy in `_env.py`, document it, fix owned call sites (optimiser call site noted in WS-08).
`sandbox-security-8` (env clamping split), `sandbox-security-4` (dead
`HAUTE_TRUSTED_HOSTS`), `sandbox-security-7` (process-wide joblib patch), and
`background-jobs-11` (metrics publisher swallows `require_job`).

**P3 — spec truth:**

- background-jobs: completed→error correction path (`background-jobs-1`), supervisor
  control flow + stale `> NOTE:` (`background-jobs-4`, `seam-exec-3`),
  `launch_protocol` vs `launch` (`background-jobs-6`, `execution-engine-11` retention
  decision), `background_task` usage (`background-jobs-5`, `seam-exec-2`),
  `_KNOWN_PREFIXES` (`seam-exec-7`, `background-jobs-7`), fold four shipped contracts
  (`background-jobs-8`, `contracts-b-7`), SingleFlight coverage gap (`contracts-b-9`,
  `background-jobs-13`, `testing-credibility-10`), scope/ownership rows
  (`background-jobs-9`, `background-jobs-14`, `background-jobs-10`,
  `over-complication-12`).
- execution-engine: `execute_sink` → `write_data_output` rename sweep (`contracts-a-6` —
  includes rows in server-api and sandbox-security specs; those two doc files are owned
  here and by WS-04 respectively, coordinate the two lines), fold four shipped contracts
  and re-open the genuinely undelivered `_resolve_sink_path` removal
  (`execution-engine-1`, `contracts-a-9`, `contracts-a-10`), preview re-raise set
  (`execution-engine-5`), strict contract resolution (`execution-engine-6`), missing typed
  errors (`execution-engine-7`), `_builders.py` module-map row (`execution-engine-12` —
  ownership decision coordinated with WS-06), facade honesty for `execution.py`
  (`over-complication-11` — document now, split later).
- sandbox-security: document the preamble binding filter (`sandbox-security-5`), scope/map
  mismatch (`sandbox-security-9`), factual slips (`sandbox-security-12`), fold shipped
  contracts (`contracts-b-10`), index the adversarial traversal suites
  (`testing-credibility-5`).

## Finding inventory

High: `background-jobs-1`, `background-jobs-4`, `seam-exec-3`, `contracts-a-6`,
`execution-engine-1`, `sandbox-security-1`, `testing-credibility-5`.
Medium: `contracts-a-10`, `contracts-a-9`, `execution-engine-12`, `execution-engine-2`,
`execution-engine-3`, `execution-engine-4`, `execution-engine-5`, `execution-engine-6`,
`over-complication-11`, `seam-exec-10`, `background-jobs-2`, `background-jobs-3`,
`background-jobs-5`, `background-jobs-6`, `background-jobs-8`, `contracts-b-7`,
`contracts-b-9`, `seam-exec-2`, `seam-exec-7`, `contracts-b-10`, `sandbox-security-2`,
`sandbox-security-5`, `sandbox-security-9`.
Low: `execution-engine-10`, `execution-engine-11`, `execution-engine-7`,
`execution-engine-9`, `background-jobs-10`, `background-jobs-11`, `background-jobs-13`,
`background-jobs-14`, `background-jobs-7`, `background-jobs-9`, `over-complication-12`,
`testing-credibility-10`, `sandbox-security-12`, `sandbox-security-4`,
`sandbox-security-7`, `sandbox-security-8`, `failure-model-6`.

## File ownership (exclusive)

- `src/haute/executor.py`, `execution.py`, `_execute_lazy.py`, `chunking.py`,
  `projection.py`, `_execution_context.py`, `_execution_admission.py`,
  `_worker_isolation.py`, `_worker_protocol.py`, `_graph_utils.py`
- `src/haute/_sandbox.py`, `_local_security.py`, `_env.py`, `_path_resolution.py`
- `src/haute/routes/_background_jobs.py`, `routes/_job_store.py`,
  `routes/_job_lifecycle.py`, `routes/_timeouts.py`
- `docs/specs/execution-engine/**`, `docs/specs/background-jobs/**`,
  `docs/specs/sandbox-security/**`
- Their tests (`tests/test_worker_isolation.py`, `test_worker_protocol.py`,
  `test_sandbox.py`, `test_path_traversal*.py`, `test_write_sandbox*.py`, chunking,
  admission, job store/lifecycle suites)

## Cross-stream touchpoints

- `routes/_supersession.py` and `routes/pipeline.py` admission-release race
  (`server-api-1`) are WS-04's; the `BlockingWorkTimeoutError.background_task` spec fixes
  here must match WS-04's chosen fix.
- `modelling-1` (WS-07) may need a publication-claim API on `_job_lifecycle` — WS-07
  implements in `_train_service.py` first; if a lifecycle change is needed, it lands here.
- `projection.py:2847` half of `json-shredding-4` (WS-04) — accept a coordinated hunk or
  make the edit on WS-04's behalf.
- `caching-4`/`seam-exec-9` `execution.py` changes are made here if WS-02 chooses
  unification; WS-02 owns the caching spec text.
- `failure-model-6` optimiser call-site follow-up noted in WS-08.

## Definition of done

- Sandbox bypass, checkpoint traversal and `exit` alias closed with adversarial regression
  tests; worker queue-drain, cleanup and cancellation semantics fixed and tested.
- One documented env-knob failure policy; `_env.py` implements it.
- All shipped contract sections in the three components folded and deleted; the one
  genuinely open item (`_resolve_sink_path` removal) re-opened as a roadmap package via
  WS-01's roadmap format.
- Baseline entries for these components deleted; every finding fixed or deferred with
  reasons.

## Verification

- `uv run pytest tests/test_sandbox.py tests/test_worker_isolation.py tests/test_worker_protocol.py -q`
- `uv run pytest tests/test_path_traversal_advanced.py tests/test_write_sandbox_guard.py -q`
- Job store/lifecycle suites; `uv run pytest tests/test_docs_accuracy.py -q`; quick
  preflight near completion.
