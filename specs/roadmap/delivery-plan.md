# Delivery plan

The order in which the active roadmap packages are delivered from one
checkout, without parallel worktrees. Written on 24 September 2026, after
the parallel lanes' second rounds merged (PRs #236 to #250). Each package's
entry in its component roadmap owns the problem, plan, acceptance and
evidence; this file owns only the grouping and the order. When a phase's PR
merges, remove its rounds from the table.

## Working method

- **One phase, one branch, one PR.** Rounds are grouped into phases (the
  *Phase* column). Cut each phase's branch from a fresh `origin/main`, deliver
  its rounds in order and each round's packages in the listed order, one
  commit per package with the package ID in the message, and open one PR for
  the phase. A phase starts only after the phases holding its rounds' *Needs*
  have merged. While a phase's CI runs, another phase may start on its own
  branch from `origin/main` if it needs nothing unmerged (phases C and D are
  independent of each other); never build on an unmerged branch.
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
| B | 6 | Optimiser frontier | `OPT-P06`, `OPT-P12`, `OPT-P14` | — | L | The benchmark decides `OPT-P06`; `OPT-P12` and `OPT-P14` finish splitting the optimiser service. |
| B | 7 | Domain errors | `API-R02` | 6 | L | Services stop raising HTTP types after the optimiser service has been split, so its errors are converted once, in their final modules. |
| B | 8 | Worker primitive | `ROAD-WORKER-05` | 7 | L | One worker primitive and one failure family, mapped once to HTTP and job states, after the estimate has moved and services raise domain errors. |
| C | 9 | Project context | `PCFG-R04` | 8 | L | The worker request format changes once, after the primitive. Two or more PRs: the context, accessor, fixture and ratchet with the first modules, then the remaining `chdir` migration and the deletion of the fallbacks. |
| D | 10 | Generated contracts | `API-R03`: node data, cache, JSON-cache status and input cache; output write, destination and assemble dry run; one shared execution-metrics validator | 8 | L | The groups that carry no node config or editor document, generated once the job failure records have settled. |
| D | 11 | Reuse | `SUB-R01` | — | L | Node-level instances go before the editor-state move and the typed configs would have to model them. Its first step proves the one-node submodel form and stops if an instance use is not covered. |
| E | 12 | Editor state | `PCFG-R08`, `CACHE-S25` | 11 | M | `CACHE-S25` keys caches on the whole config once editor state has left it. |
| E | 13 | Typed node configs | `PCFG-R07`, then `API-R03`: pipeline load and save, preview, trace, submodel, recovery and repair, and JSON-cache inference | 10, 12 | L | These `API-R03` groups carry node configs or the editor document, so they follow the typed models. Completes `API-R03`. |
| E | 14 | Node specification and results store | `PCFG-R09`, `FSH-R03` | 13 | M | Both build on the typed models and the complete generated contract. |
| F | 15 | Cache measurements | `CACHE-S17`, `CACHE-S22`, `CACHE-S18`, `CACHE-S13` | 12 | M | Each runs its measurement and builds only past its gate, once the planner and config changes above have landed. |
| F | 16 | Containment | `SBX-R01` | 9 | M | The remaining path comparisons, after the project context has deleted the resolvers that held many of them. |
| F | 17 | Dead code | `ENGQ-R01` | 16 | M | After the refactors have deleted what they replace; adds knip to the frontend lint. |
| F | 18 | Test organisation | `ENGQ-R05` | 17 | L | Last, so the suite is reorganised once, under the risk-based coverage rule. Several PRs, by component. |

Sizes are rough: M is a day or so, L several days or more than one PR.

## User-visible changes in this plan

These packages change what a user sees or relies on. Each was decided on
24 September 2026 and is recorded in its entry; raise an objection before its
round starts.

- `SUB-R01`: `@pipeline.instance` and `instanceOf` are rejected; **Create
  Instance** makes a one-node submodel with a second occurrence.

## Not scheduled

Deferred packages start only when their trigger is met: `ROAD-WORKER-04`
([background jobs](background-jobs-api.md)) needs versioned solver
persistence; `CACHE-S19` ([caching](caching.md)) and `EDA-E18`, `EDA-E23` and
`EDA-E24` ([Explore and EDA](explore-eda.md)) wait for the evidence their
entries name.
