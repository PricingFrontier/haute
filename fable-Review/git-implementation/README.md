# Fable Review — Git implementation (engine, routes, panel UX)

**Read-only deep review of Haute's Git subsystem, performed 2026-07-06 at HEAD `220bcccd`
(branch `code-fixes`).**
Six passes: a primary read of the full backend + panel, then five parallel reviewers — engine
correctness (scratch-repo reproductions), performance (measured on this Windows machine), HTTP
routes/security/concurrency, dual-persona frontend UX, and test-coverage mapping. Every finding
below was verified against the current code; the load-bearing ones were **reproduced** (the
`.haute/` seed trap, the active-pair delete corruption, the tab-corrupted history rows, the
linearized-merge replay) or **measured** (the N+1 costs). Reviewer reports were cross-checked
against each other and against the source before packaging.

**Nothing in the source tree was changed by this review.** This folder is the deliverable: the
verdict, 16 fix packages, and [CLEARED.md](CLEARED.md) — behaviours adversarially checked and
found correct, which the implementing agent must not "fix".

Scope: `src/haute/_git.py`, `_git_state.py`, `routes/git.py`, `routes/_helpers.py` (watcher
pause, `commit_pipeline_graph`), `routes/_save_pipeline.py` (ledger capture),
`cli/_init_cmd.py` (ignore scaffold), the full frontend git surface (GitPanel, BranchManager,
RemotePushControl, BranchIndicator, the four modals, ComparisonView, useGitStore, App wiring,
api/client), and all git test suites.

---

## Verdict

Against the four axes asked of this review:

**Elegant — genuinely, yes.** The working/ledger branch-pair model is the strongest part of the
subsystem: saves accumulate as real commits on a hidden ledger, milestones are plumbing-built
merge commits onto the working branch (CAS-guarded, no checkout, no index), and full granularity
stays reachable through each merge's second parent. Guardrails, error taxonomy (sanitize-raw /
verbatim-domain / 403-guardrail), never-force-push, the honesty states ("untracked" vs "unknown"
vs measured), and the data-bearing 409 rejections are design-quality work, and the test
architecture (real git repos, surgical fault injection) matches it. A git-savvy reader can map
every UI action onto git semantics and `git log` tells the truth afterwards.

**Robust — strong core, with specific, now-reproduced holes.** Three matter most:
1. **No cross-request mutation lock** — a pipeline save's ledger commit can interleave with a
   move/fast-forward/create checkout: spurious sanitized 400s at best, an **orphaned save commit
   reported as success** at worst (G01).
2. **The unborn-repo seed runs `git add -A`** — reproduced committing `.haute/` state (→ a
   permanent "You have unsaved changes" lock-out), and in hand-`git init`ed repos it sweeps
   **`.env` credentials and datasets** into the root commit that a later push publishes (G02).
3. **Adopted-repo lifecycle edges** — deleting/archiving the active pair half-deletes it when no
   `main`/`master`/remote-HEAD exists (reproduced), made sticky by a cache that memoizes a
   mutable fallback (G03); a tab in a commit message corrupts the history rows, and move-forks
   silently linearize externally-merged ledgers (G04).

**Efficient/performant — no, not yet, and it's measurable.** Every git call is a subprocess
(~45-80 ms on Windows), and the hot paths multiply them: one `git tag --points-at` **per
milestone** (panel fetches 50), three spawns **per branch** in the branch manager, both refetched
on every save plus a duplicate fetch on open. Measured: **a single save with the panel open costs
~100 spawns ≈ 4.5 s**; `working_milestones(limit=50)` alone is 4.56 s vs 197 ms batched (23×).
Comparison opens pay it twice. Fetches (≤10 s) also run inside request handlers under a global
lock (G05, G06, G12, G13).

**The two personas — the model serves both; the surfaces short-change both.** For the git-naive:
every hand-written guardrail message is currently replaced by a literal **"HTTP 400"** toast
(G07); switching branches **silently discards unsaved editor work** despite the README's
"saves your current work first" (G08); on a non-git project the entire feature invisibly vanishes
with no set-up path (G11); and the README promises backups and a review flow that don't exist
(G15). For the git-savvy: CLI interop is *detected* honestly (`invalid`/`divergent`) but rendered
as a mislabelled "Set branch" with a raw invariant dump and no remediation (G11), and non-English
git localisation silently kills the flagship divergence-resolution UX (G09).

Totals: **8 HIGH, ~17 MEDIUM, ~15 LOW** verified findings across 16 packages. Nothing here
requires rearchitecting — the model is right; the work is locks, batching, seeding hygiene, and
letting the backend's own voice reach the user.

---

## Fix packages, in recommended execution order

| # | Package | Severity | Effort | Main files |
|---|---------|----------|--------|-----------|
| G01 | [Cross-request repo mutation lock](G01-repo-mutation-lock.md) | HIGH · silent loss | M | `_git.py` |
| G02 | [Unborn-seed safety (`add -A` scope + ignore seeding)](G02-unborn-seed-safety.md) | HIGH · data safety | S-M | `_git.py`, `cli/_init_cmd.py` |
| G03 | [Pair-lifecycle edges (switch-away, cached fallback, X2 rollback)](G03-pair-lifecycle-edges.md) | HIGH | M | `_git.py` |
| G07 | [Frontend error surfacing (`apiErrorText`)](G07-frontend-error-surfacing.md) | HIGH · UX | S | 6 components + client |
| G08 | [Dirty-switch guard (save-first switching)](G08-dirty-switch-guard.md) | HIGH · data loss | S-M | `BranchManager.tsx` |
| G05 | [Version-label N+1 + commit-context walk](G05-version-label-nplus1.md) | HIGH · perf (23×) | S-M | `_git.py` |
| G06 | [`working_branches` N+1 + redundant refetches](G06-working-branches-nplus1.md) | HIGH · perf | M | `_git.py`, GitPanel/BranchManager |
| G04 | [History integrity (tab rows, merge linearization)](G04-history-integrity.md) | MEDIUM · silent wrongness | S | `_git.py` |
| G09 | [Locale pinning + failure classification](G09-locale-and-error-classification.md) | MEDIUM | M | `_git.py` |
| G10 | [Dead `/status` surface + `os.getlogin` crasher](G10-dead-status-surface.md) | MEDIUM | S | `_git.py`, routes, client |
| G11 | [Non-git / invalid / divergent state UX (+ `git init` affordance)](G11-nongit-and-invalid-state-ux.md) | MEDIUM | M | BranchIndicator, modals, one new route |
| G12 | [Fetch off the request path](G12-fetch-off-request-path.md) | MEDIUM | M | `_git.py` |
| G13 | [`/show` + comparison robustness (pathspec archive, temp-dir, parse-fail, 3.11.4 floor)](G13-show-compare-robustness.md) | MEDIUM | S-M | `_git.py`, `_helpers.py`, pyproject |
| G14 | [State-file atomicity](G14-state-atomicity.md) | MEDIUM | S | `_git_state.py` |
| G15 | [Docs & fixture truth (README claims, 7 orphan fixtures, doc-rot)](G15-docs-and-fixture-truth.md) | MEDIUM/LOW | S | README, fixtures, `_git.py` |
| G16 | [Polish batch (micro-UX, hygiene, test-only gaps, CI xdist)](G16-polish-batch.md) | LOW | S-M | several |

Rationale: G01 first — later packages reason about end states assuming serialised mutations.
G02/G03 close the reproduced data-safety holes. G07/G08 are small frontend changes with outsized
persona-A payoff. G05/G06 deliver the measured performance promise. G04/G09 are the
silent-wrongness/classification pair. The rest are independent; G15/G16 batch at the end.
Dependencies worth respecting: G09's classifier requires its locale pin; G12 lands easier after
G01; G15's README edit about save-first switching must land with-or-after G08.

**[CLEARED.md](CLEARED.md) lists behaviours adversarially checked and found correct — including
deliberate design decisions (degrade-open fork gate, ungated saves on unreadable status,
move-clears-association). Do not "fix" anything on that list.**

---

## Implementation protocol (binding, per project CLAUDE.md)

1. **Failing test first, always.** Every package has a TDD plan. For performance work use
   *structural* assertions (subprocess counts via a counting wrapper on `_run_git`/`_run_git_ok`,
   scaling deltas) — never wall-clock.
2. **Two agents per package — one developer, one reviewer.** Full pairs are mandatory for the
   silent-wrongness/data-loss packages: **G01, G02, G03, G04, G08, G09, G12** and the parse-fail
   leg of G13. The mechanical packages (G05, G06 backend, G07, G10, G11, G13 rest, G14, G15, G16)
   may use a single batch reviewer.
3. **Fail loud, no fallbacks.** Several fixes deliberately *replace* masking behaviour (silent
   empty graph, swallowed pref failures, sanitized-everything). Do not introduce new masks —
   G09's classifier maps to fixed hand-written strings and keeps raw stderr in the log.
4. **Line numbers are valid at `220bcccd`.** Locate code by the quoted symbols, not line numbers.
   The five source reports use per-reviewer IDs (E-engine, R-routes, P-perf, U-ux, T-tests);
   packages cite them for provenance.
5. **Gates before every commit:** `ruff format --check`, `ruff check`, `mypy`, the focused test
   files for the package; the full suite before a package's final commit. Accumulate work on the
   existing PR; **do not merge** — Ralph reviews independently.
6. **Do not regress the cleared behaviours** — several look like bugs at first glance (preserved
   panel expansion, degrade-open gates, `\x1e` framing). CLEARED.md is the contract.
7. Backend git suites currently run 296 tests green in ~11 min on this machine — G16 item 16
   (pytest-xdist) is the sanctioned way to make that cheaper; never by thinning the suite.
