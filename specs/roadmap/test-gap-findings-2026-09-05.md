# Why the existing tests missed the eight code findings

This report preserves the 5 September 2026 review at commit
`06c377e1fb0c5643d5c2bc781de73044daacdbb1`. Findings, source line references,
and execution results describe that snapshot, not a fresh verification run.
The [implementation plan](engineering-quality.md) owns current delivery work.
Raw logs, probe programs, and review-coverage artifacts remain outside this
repository; references to them below are provenance, not runnable checkout paths.
Repository source links are relative so the report can be read from any clone.

The main gap is in **which outcomes the tests prove**. There are tests near every defect, but they often verify a component in isolation, a single chosen interleaving, or a metadata update. The failures appear when those behaviours are composed into a real workflow: edit then save, rename then execute, switch branch then restart, or change a helper between jobs.

This is supported by execution, not just test names: **10 selected existing backend tests and two existing frontend tests passed** on the same reviewed code. The review reproducers fail on the corresponding missing outcomes. The original analysis changed no implementation or repository test files.

Snapshot: `06c377e1fb0c5643d5c2bc781de73044daacdbb1`, 5 September 2026. [Consolidated defect report](bug-findings-2026-09-05.md).

## Findings mapped to their test gaps

| Finding | What nearby tests establish | What they miss |
|---|---|---|
| F9: stale Save overwrites work | Older **responses** cannot replace a newer frontend baseline; the backend holds its save lock | A request based on old source must not replace newer **disk bytes** |
| F12: optimiser uses obsolete utility code | Preview refreshes after helper edits; optimiser setup reuses cached frames | Two optimiser jobs in one process, with a real helper edit between them and the real preamble compiler |
| F10: external write suppressed | Self-writes are skipped; another **path** is still broadcast | Server and external writes to the **same path** coalescing before processing |
| F11: rename breaks Python | Edge names, structured mappings, undo history and generated signatures change | Downstream Python still executes and produces the same rows after the rename |
| F2: UC pointer overwrite | A competing pointer written during bundle upload is detected by the final read | A competing pointer arriving **after the final read and before pointer upload** |
| F4: wrong branch after restore | Bind/save/publish/restart works on the original working branch | Changing the selected branch after binding, then saving and restarting |
| F3: private-child connections disappear | Public alias/port connections survive; a completely missing endpoint is rejected | An endpoint that exists inside a child definition but is illegal in the parent namespace |
| F1: sandbox capability escape | Forbidden builtins/imports/attributes and explicit path-validation helpers reject chosen examples | Capabilities reachable through the actual injected Polars object, including its own file-writing methods |

## F9 — The concurrency tests assert acknowledgement and locking, not lost-update prevention

[The frontend response-ordering test](../../frontend/src/hooks/__tests__/usePipelineAPI.test.ts#L918) resolves two mocked Save promises in reverse order. Its final assertions check the dirty baseline and `sourceRevisionRef`. There is no backend or filesystem in that test, so it passes even if an older request overwrites a newer file.

[The concurrent-save test](../../frontend/src/hooks/__tests__/usePipelineAPI.gaps.test.ts#L209) asserts that two API calls occur. That establishes request dispatch, not which version is persisted.

[The backend lock test](../../tests/test_save_lock_contract.py#L94) correctly proves that the lock is held and Save runs off the event-loop thread. However, it replaces `SavePipelineService.save` with a spy that returns success without touching disk. A lock can serialise two stale saves perfectly and still lose the newer version.

**Missing witness:** load revision A; externally commit valid revision B; submit the stale A-based request; assert conflict and unchanged B bytes. Then cover two application clients from the same base revision. These belong beside the real route/save tests, with the actual persistence path enabled.

## F12 — The helper-refresh test exercises the other execution mode

[The preview regression](../../tests/test_executor.py#L3469) really does edit a utility module and assert new output. It invokes `execute_graph`, whose normal preamble path refreshes dependencies. [The compiler-level test](../../tests/test_executor.py#L287) likewise uses the default refresh mode.

By contrast, [the optimiser cache-reuse test](../../tests/test_optimiser_routes.py#L13473) patches `_compile_preamble` to return `{}` in both runs and patches the node builders. It verifies dataframe-cache reuse while removing the dependency-refresh mechanism from the experiment. The production optimiser caller uses `force_refresh=False`, so a correct preview test cannot prove that caller is correct.

**Missing witness:** run optimiser setup; edit `utility.py`; run another setup in the same process; compare real materialised values with the new expected result. Keep normal builders, preamble compilation and cache identity in that test. No solver invocation is required. The review probe already demonstrates `preview=200` while the next optimiser input remains `10`.

## F10 — Watcher tests separate paths or supply the classification as a mock

[The self-write test](../../tests/test_server.py#L2220) forces `is_self_write=True`, then asserts no broadcast. It verifies what the watcher does after classification; it cannot validate the classifier's correctness.

[The stronger mixed-writer test](../../tests/test_server.py#L2257) uses the real marker but writes `server_saved.py` and `test_pipeline.py`. That proves path-specific suppression is better than global suppression. It does not distinguish two writers touching the same path.

**Missing witness:** mark/write a file as the server; externally replace its contents; deliver one coalesced event; assert the external version is broadcast. Retain a passing control without the mark and a genuine unchanged self-write suppression case. This is a deterministic event-sequence test, not a timing-dependent sleep test.

## F11 — Even the rename integration test mocks away execution

[The ordinary-node rename integration test](../../frontend/src/__tests__/App.integration.test.tsx#L1651) checks labels, `input_scenario_map`, instance mappings and undo history. At line 1653 it replaces preview with a promise that never resolves. An unchanged `config.code` containing an obsolete input name cannot fail any of these assertions.

The browser suite has a related [frame rename/save/reload test](../../frontend/e2e/persistence/api-input-frame-alignment.spec.ts#L652). It performs real persistence and checks handles/names/signatures, but installs [a synthetic successful preview response](../../frontend/e2e/persistence/api-input-frame-alignment.spec.ts#L176). A generated function signature can be correct while its function body refers to an undefined variable.

**Missing witness:** execute a small connected graph, rename its source using the real update path, then execute the resulting graph and compare rows. Add a save/reload step where persistence is relevant. The existing UI tests remain useful; they need one real execution witness for this contract.

## F2 — The race injection occurs before the check that is supposed to detect it

[The existing mid-flight publication test](../../tests/test_project_storage.py#L1378) installs the competing `HEAD.json` when a `/bundles/` upload occurs. The publisher's final HEAD read happens afterwards and detects the change. This is a useful race test, but represents only one ordering.

The uncovered ordering installs the competing HEAD immediately before this writer uploads its own HEAD, after its last read. The implementation has no conditional write at that boundary. Both cases can use the same in-memory Files API and deterministic hook; the problem is not that the test transport is fake, but that the injected ordering stops before the vulnerable interval.

**Missing witness:** cover publication immediately before and immediately after the final read, initial competing publication, and a stalled writer resuming after takeover. Assert the authoritative pointer as well as the response.

## F4 — The restart test never changes the branch recorded at binding

[The UC lifecycle test](../../tests/test_project_storage.py#L1167) exercises a substantial bind/save/publish/container-replacement/restore sequence. It saves on `WORKING` and asserts that restored selection is also `WORKING`. That is exactly the value recorded when binding, so a stale binding record satisfies the test.

**Missing witness:** insert a branch switch between bind and save, then assert the selected branch and its contents after replacement. Merely checking that the new branch exists in the bundle would still miss the incorrect active selection.

## F3 — The invalid endpoint sits between the two categories already tested

[The canonical-boundary test](../../tests/test_parser_conservation.py#L568) authors connections through the public alias and port labels. [The dangling-edge test](../../tests/test_parser_conservation.py#L784) connects to an entirely absent `missing` node.

The defective case names a real private child node directly from the parent. It is neither a valid public alias nor absent from the collected child-ID set. The parser filters its connections out while the conservation check still recognises the endpoint as known.

The [root round-trip property suite](../../tests/test_codegen_roundtrip_property.py#L837) explicitly excludes submodel container types and delegates their behaviour to the dedicated multi-file tests. Valid canonical round trips cannot generate this illegally authored parent connection.

**Missing witness:** feed raw parent source with private-child endpoints to the public parser and require rejection. The broader invariant is that every authored connection is represented after parsing or receives an explicit diagnostic; validity of the resulting smaller graph is insufficient.

## F1 — The tests validate the denylist and path helper, not all exposed capabilities

[Sandbox tests](../../tests/test_sandbox.py#L57) check explicit `__import__`, `open`, `eval` and related restrictions. [Path tests](../../tests/test_sandbox.py#L127) invoke `validate_project_path` directly. Those tests pass and the helpers do reject the selected inputs.

The escape does not use those entry points: `pl.io.csv.functions.os` exposes OS functionality, and `df.write_csv` writes through Polars without invoking Haute's path validator. Also, [the main executor test module](../../tests/test_executor.py#L45) opts into [a fixture that widens the project root to the filesystem root](../../tests/conftest.py#L203), which is convenient for data fixtures but does not establish project confinement.

**Missing witness:** run node text through the production execution boundary with a tightly bounded temporary project, a synthetic environment marker and an outside-project sentinel. Assert forbidden reads/writes do not succeed through the capabilities actually supplied to user code. Keep the direct validator tests, but do not use their success as proof of whole-runtime containment.

## Two tests particularly overstate what they prove

- [test_save_during_preview_does_not_corrupt_files](../../tests/test_partial_failure.py#L702) invokes neither preview nor Save. It starts two threads calling `Path.write_text`, then accepts either complete output. It cannot detect a regression in any Haute save/preview implementation and explicitly accepts last-writer-wins data loss.
- [test_save_service_is_not_thread_safe_by_design](../../tests/test_partial_failure.py#L735) asserts that the service has no `_lock` attribute. Absence of one attribute is not a behavioural concurrency contract; a correct redesign could fail that test while an unsafe implementation passes.

Replace these with tests of the named public behaviour, or name and scope them honestly if their lower-level guarantee is still needed. Assertions that only describe the current implementation should not serve as correctness evidence.

## Why the quality gates do not close these gaps

**The relevant ordinary tests are configured to run.** [CI runs two shards over `tests/`](../../.github/workflows/ci.yml#L195), with [pytest discovering that directory and excluding `perf` by default](../../pyproject.toml#L221). [Frontend CI runs preflight and browser E2E](../../.github/workflows/ci.yml#L465). The observed explanation is test content, not these neighbouring tests being absent from collection. This analysis inspected the checked-in configuration and ran selected tests; it does not claim to have checked historical hosted CI results.

**Coverage measures execution, not the completeness of the contract.** The [90% global gate, critical per-file gates and 100% changed execution-code gate](../../.github/workflows/ci.yml#L243) are useful. But a missing revision comparison has no branch to mark uncovered. Executing a watcher suppression branch once does not prove its classification for two writers, and exercising a cache hit does not prove that its namespace and dependency digest describe the same code.

**Mutation coverage is selective.** [Eight mutation targets are configured](../../mutation/targets.json). Most defect-owning modules here are outside them: `_user_exec.py`, `_uc_transport.py`, `_parser_submodels.py`, `_project_storage.py`, `routes/pipeline.py`, `routes/_helpers.py`, `server.py`, `routes/_optimiser_service.py`, and the frontend update path. [The executor target](../../mutation/cosmic-ray.executor.toml#L1) covers `executor.py`, but its listed test command does not include the optimiser route suite. The [executor survival ceiling is 15%](../../mutation/targets.json#L57), and [the gate rejects a rate above the configured ceiling](../../scripts/run_mutation_suite.py#L822). These are configuration facts, not a claim that a particular mutant survived in CI. Mutation testing cannot supply an absent workflow or expected-outcome assertion on its own.

**Isolation needs deliberate stateful tests alongside it.** [Per-test cache resets](../../tests/conftest.py#L31) are appropriate; removing them would introduce order dependence. Repeated jobs and transitions need to happen explicitly inside one test with the relevant caches alive. Likewise, a fresh project that always uses its initial branch cannot reveal a post-bind branch-selection error.

## Recommended changes to the test strategy

1. Promote the review reproducers into the existing owning test modules as each fix is made. Start with stale Save, real repeated optimiser setup, same-path watcher coalescing and rename-then-execute. Preserve their real disk/result assertions.
2. For each cross-component operation, maintain one small workflow witness with its decisive dependency real: actual persistence for Save; actual code execution for rename; real namespace compilation for optimiser refresh. Keep mocks in the focused unit tests where they clarify the scope.
3. Make race tests enumerate the critical orderings around the final irreversible operation. Use explicit hooks/barriers rather than probabilistic thread timing.
4. Extend parser tests beyond valid generated graphs: exercise raw authored syntax that is valid Python but violates graph namespace/interface rules, and assert conservation-or-rejection.
5. Review mutation ownership when code moves into extracted modules. A filename-specific target on an old orchestrator does not automatically follow the moved behaviour. Treat survivor changes as a review signal; do not equate passing a percentage ceiling with a complete contract.
6. Judge new tests by the defect they distinguish and the final observable outcome they assert. More cases that only check call counts, labels, or implementation attributes would increase volume without closing these particular holes.

## Verification evidence

- Adjacent backend tests (local review artifact: `adjacent-backend-tests.txt`): **10 passed**, one dependency deprecation warning, 5.52 seconds. Selected sandbox/path, parser, UC publication/restore, watcher, preview utility-refresh, and Save-lock tests.
- Adjacent frontend tests (local review artifact: `adjacent-frontend-tests.txt`): **2 passed**, 115 deselected by the explicit test-name filter, 19.78 seconds. Ordinary source rename and out-of-order Save acknowledgement.
- First-pass reproducers (local review artifact: `regressions-final.txt`): five expected failures for F1–F4, including two sandbox probes.
- Second-pass reproducers (local review artifact: `pass2-regressions.txt`): four expected failures for F9–F12 and one passing watcher control.
- The review probes remain outside repository test discovery because this is still a review before fixes. They are executable evidence, not yet permanent CI protection. The original review changed no repository files.
