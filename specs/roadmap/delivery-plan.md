# Delivery plan

The order in which the active roadmap packages are delivered from one
checkout, without parallel worktrees. Written on 24 September 2026, after
the parallel lanes' second rounds merged (PRs #236 to #250). Each package's
entry in its component roadmap owns the problem, plan, acceptance and
evidence; this file owns only the grouping and the order. When a round's PR
merges, remove that round from the table.

## Working method

- **One round, one branch, one PR.** Cut each round's branch from a fresh
  `origin/main`. Deliver its packages in the listed order, one commit per
  package with the package ID in the message, and open one PR for the round.
  A round starts only after the rounds in its *Needs* column have merged.
  While a round's CI runs, the next round may start on its own branch from
  `origin/main` if it does not need the open round; never build on an unmerged
  branch.
- **Per package.** Follow the [working protocol](README.md#working-protocol):
  reverify against `HEAD` and retire the package if its outcome already
  holds; update the owning specification first; add the smallest failing
  regression; implement; run the targeted verification in `AGENTS.md`; have
  Codex review the diff (`codex-code-review`) and resolve its findings; then
  commit. Remove the package from its component roadmap in the same commit,
  and delete a component roadmap that is left empty, with its README row and
  its `_EXPECTED_ACTIVE_COMPONENT_ROADMAPS` entry in
  `tests/test_docs_accuracy.py`.
- **CI is the full run.** Push, watch `gh pr checks` until they finish, read a
  failing job's log with `gh`, and fix and push again. Rerun an environmental
  flake with `gh run rerun <id> --failed` instead of pushing a change.
- **Measurements decide, decisions go up.** A measurement-gated package
  records its numbers in the PR and decides by the rule its entry states. A
  package that turns out to need a product decision stops at that point and
  goes to the user with options and a recommendation.

## Rounds

| Round | Theme | Packages, in order | Needs | Size | Why here |
|---:|---|---|---|---|---|
| 1 | Fail-loud fixes | `JSON-R03`, `SBX-R03`, `CACHE-S28`, `DEP-R05`, `SUB-R02` | — | M | Small, independent fixes for silent drops, a planning gap and a deploy that always fails. |
| 2 | Explore and trace | `EDA-E25`, `TRACE-R03` | — | M | Two small user-facing changes; `EDA-E25` regenerates the Explore chart contract. |
| 3 | Tests and gates | `ENGQ-R06`, `API-R05`, `ENGQ-R04` | — | M | Removes CI noise and narrows the coverage gate before the large refactors, which then work under the lighter rule. |
| 4 | Optimiser routes | `API-R01`, `MLF-R02`, `OPT-P13` | — | M | All three change `routes/optimiser.py` and `_optimiser_service.py`; `OPT-P13` opens the optimiser chain. |
| 5 | Optimiser estimate | `OPT-P16` | 4 | M | Moves the last pipeline read out of the server process, onto the warm worker pool. |
| 6 | Optimiser frontier | `OPT-P06`, `OPT-P12`, `OPT-P14` | 4 | L | The benchmark decides `OPT-P06`; `OPT-P12` and `OPT-P14` finish splitting the optimiser service. |
| 7 | Domain errors | `API-R02` | 4, 6 | L | Services stop raising HTTP types after the optimiser service has been split, so its errors are converted once, in their final modules. |
| 8 | Worker primitive | `ROAD-WORKER-05` | 5, 7 | L | One worker primitive and one failure family, mapped once to HTTP and job states, after the estimate has moved and services raise domain errors. |
| 9 | Project context | `PCFG-R04` | 8 | L | The worker request format changes once, after the primitive. Two or more PRs: the context, accessor, fixture and ratchet with the first modules, then the remaining `chdir` migration and the deletion of the fallbacks. |
| 10 | Generated contracts | `API-R03`: node data, cache, JSON-cache status and input cache; output write, destination and assemble dry run; one shared execution-metrics validator | 8 | L | The groups that carry no node config or editor document, generated once the job failure records have settled. |
| 11 | Reuse | `SUB-R01` | 1 | L | Node-level instances go before the editor-state move and the typed configs would have to model them. Its first step proves the one-node submodel form and stops if an instance use is not covered. |
| 12 | Editor state | `PCFG-R08`, `CACHE-S25` | 11 | M | `CACHE-S25` keys caches on the whole config once editor state has left it. |
| 13 | Typed node configs | `PCFG-R07`, then `API-R03`: pipeline load and save, preview, trace, submodel, recovery and repair, and JSON-cache inference | 1, 10, 12 | L | These `API-R03` groups carry node configs or the editor document, so they follow the typed models (and `SUB-R02`'s change to the recovery responses). Completes `API-R03`. |
| 14 | Node specification and results store | `PCFG-R09`, `FSH-R03` | 13 | M | Both build on the typed models and the complete generated contract. |
| 15 | Cache measurements | `CACHE-S17`, `CACHE-S22`, `CACHE-S18`, `CACHE-S13` | 12 | M | Each runs its measurement and builds only past its gate, once the planner and config changes above have landed. |
| 16 | Containment | `SBX-R01` | 9 | M | The remaining path comparisons, after the project context has deleted the resolvers that held many of them. |
| 17 | Dead code | `ENGQ-R01` | 16 | M | After the refactors have deleted what they replace; adds knip to the frontend lint. |
| 18 | Test organisation | `ENGQ-R05` | 3, 17 | L | Last, so the suite is reorganised once, under the coverage rule `ENGQ-R04` sets. Several PRs, by component. |

Sizes are rough: M is a day or so, L several days or more than one PR.

## User-visible changes in this plan

These packages change what a user sees or relies on. Each was decided on
24 September 2026 and is recorded in its entry; raise an objection before its
round starts.

- `SUB-R01`: `@pipeline.instance` and `instanceOf` are rejected; **Create
  Instance** makes a one-node submodel with a second occurrence.
- `SUB-R02`: the recovery action **Update to current format** is removed; a
  legacy submodel registration is rejected with a targeted message.
- `DEP-R05`: `haute deploy` to Azure Container Apps, AWS ECS or GCP Cloud Run
  succeeds after the image is pushed instead of failing at the service update.
- `SBX-R03`: preamble imports of `os`, `sys`, `shutil` and the like reach node
  code instead of being dropped.
- `JSON-R03`: an OUTPUT mapping whose frame cannot be nested under its parents
  is rejected instead of silently losing that frame.
- `TRACE-R03`: a step among identical rows shows their values, labelled one
  of N identical rows, instead of a trace gap.
- `ENGQ-R04`: the changed-code coverage gate blocks only the safety-critical
  modules.

## Not scheduled

Deferred packages start only when their trigger is met: `ROAD-WORKER-04`
([background jobs](background-jobs-api.md)) needs versioned solver
persistence; `CACHE-S19` ([caching](caching.md)) and `EDA-E18`, `EDA-E23` and
`EDA-E24` ([Explore and EDA](explore-eda.md)) wait for the evidence their
entries name.
