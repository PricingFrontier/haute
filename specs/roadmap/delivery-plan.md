# Delivery plan

The order for the scheduled roadmap packages, delivered from one checkout
without parallel worktrees. Each package's entry in its component roadmap
owns the problem, plan, acceptance and evidence; this file owns only the
grouping and the order. Remove a round when its packages are complete.
Round numbers remain stable when earlier rounds are removed. The table is
not an inventory of all active work; see [Not scheduled](#not-scheduled).

## Working method

- **One phase, one branch, one PR.** Rounds are grouped into phases (the
  *Phase* column). Cut each phase's branch from a fresh `origin/main`, deliver
  its rounds in order and each round's packages in the listed order, one
  commit per package with the package ID in the message, and open one PR for
  the phase. A phase starts only after the phases holding its rounds' *Needs*
  have merged. While a phase's CI runs, another phase may start on its own
  branch from `origin/main` if it needs nothing unmerged; never build on an
  unmerged branch.
- **Per package.** Follow the [working protocol](README.md#working-protocol):
  reverify against `HEAD` and retire the package if its outcome already
  holds; update the owning specification first; add the smallest failing
  regression; implement; run the affected tests once; then commit. Remove
  the package from its component roadmap in the same commit,
  and delete a component roadmap that is left empty, with its README row and
  its `_EXPECTED_ACTIVE_COMPONENT_ROADMAPS` entry in
  `tests/test_docs_accuracy.py`.
- **Before the phase's PR.** Follow "Before a pull request" in `AGENTS.md`:
  one Codex review of the whole branch diff, with its findings resolved,
  before the PR is opened.
- **CI is the full run.** Push, watch `gh pr checks` until they finish, read a
  failing job's log with `gh`, and fix and push again. Rerun an environmental
  flake with `gh run rerun <id> --failed` instead of pushing a change.
- **Measurements decide, decisions go up.** A measurement-gated package
  records its numbers in the PR and decides by the rule its entry states. A
  package that turns out to need a product decision stops at that point and
  goes to the user with options and a recommendation.

## Rounds

| Phase | Round | Theme | Packages, in order | Needs | Size | Why here |
|---|---:|---|---|---|---|---|
| B | 8 | Worker primitive | `ROAD-WORKER-05` | — | L | One worker primitive and one failure family, mapped once to HTTP and job states, after the estimate has moved and services raise domain errors. |
| C | 9 | Project context | `PCFG-R04` | 8 | L | The worker request format changes once, after the primitive. Two or more PRs: the context, accessor, fixture and ratchet with the first modules, then the remaining `chdir` migration and the deletion of the fallbacks. |
| D | 10 | Generated contracts | `API-R03`: one shared execution-metrics validator | 8 | M | The node-data, cache, input-cache and output groups are generated (29 October 2026, ahead of this round); a job failure record that round 8 changes is one model edit and a regeneration. |
| D | 11 | Reuse | `SUB-R01` | — | L | Node-level instances go before the editor-state move and the typed configs would have to model them. Its first step proves the one-node submodel form and stops if an instance use is not covered. |
| E | 12 | Editor state | `PCFG-R08`, `CACHE-S25` | 11 | M | `CACHE-S25` keys caches on the whole config once editor state has left it. |
| E | 13 | Typed node configs | `PCFG-R07`, then `API-R03`: pipeline load and save, preview, trace, submodel, recovery and repair, and JSON-cache inference | 10, 12 | L | These `API-R03` groups carry node configs or the editor document, so they follow the typed models. The existing Model Scoring file source supplies its config seam; round 21 extends the same models with external-family selection and pyfunc trust. Completes `API-R03`. |
| E | 14 | Node specification and results store | `PCFG-R09`, `FSH-R03` | 13 | M | Both build on the typed models and the complete generated contract. |
| F | 15 | Cache measurements | `CACHE-S17`, `CACHE-S22`, `CACHE-S18`, `CACHE-S13` | 12 | M | Each runs its measurement and builds only past its gate, once the planner and config changes above have landed. |
| F | 17 | Dead code | `ENGQ-R01` | 9 | M | After the refactors have deleted what they replace; adds knip to the frontend lint. |
| F | 18 | Test organisation | `ENGQ-R05` | 17 | L | Last, so the suite is reorganised once, under the risk-based coverage rule. Several PRs, by component. |
| I | 21 | Models trained outside Haute | `MSC-06`, then `MSC-05` | — | M | Generic suffixes and the XGBoost missing-value sentinel first; pyfunc files after, on the same file source. |
| J | 23 | Remaining Polars 2 evidence | `PL2-17`, `PL2-18` | — | L | Reverify the training overhead and width calibration, operator evidence at `10m`, and worker baselines. Apply only the policy changes those measurements justify. |

Sizes are rough: M is a day or so, L several days or more than one PR.

## User-visible changes in this plan

These packages change what a user sees or relies on. Each was decided on the
date its entry records; raise an objection before its round starts.

- `SUB-R01`: `@pipeline.instance` and `instanceOf` are rejected; **Create
  Instance** makes a one-node submodel with a second occurrence.
- `MSC-06`: Model Scoring reads XGBoost and LightGBM files saved under their
  libraries' generic suffixes (`.json`, `.txt`), and a contract can declare an
  XGBoost model's missing-value sentinel.
- `MSC-05`: Local pyfunc packages are executable imports requiring explicit
  trust of their content digest. Inspection does not execute package code;
  changed contents require a new trust decision.

## Not scheduled

The component roadmaps remain the complete inventory. Packages outside the
rounds above, including the [25 October audit](codebase-review-2026-10-25.md)
and [28 October functional review](codebase-review-2026-10-28.md)
packages, have not been assigned a round; that does not defer their stated
priority. Decision packages need the choice in their entry before scheduling.

Deferred packages start only when their trigger is met. For example, `CACHE-S19` ([caching](caching.md)), `EDA-E18`, `EDA-E23` and
`EDA-E24` ([Explore and EDA](explore-eda.md)) and `MSC-07`
([model scoring](model-scoring.md)) wait for the evidence or demand their
entries name.
