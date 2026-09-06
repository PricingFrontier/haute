# Haute code review — consolidated findings

This report preserves the 5 September 2026 review at commit
`06c377e1fb0c5643d5c2bc781de73044daacdbb1`. Findings, source line references,
and execution results describe that snapshot, not a fresh verification run.
The test expansion programme that answered these findings (ENG-T01 to ENG-T12)
is complete; its witnesses are recorded in `tests/workflow_coverage.toml` and
the owning component Testing sections, and the [submodels roadmap](submodels.md)
owns the naming work that follows F13.
Raw logs, probe programs, and review-coverage artifacts remain outside this
repository; references to them below are provenance, not runnable checkout paths.
Repository source links are relative so the report can be read from any clone.

The two passes found **eight reproducible implementation defects: five P1 and three P2**. The second pass adds four code findings: stale Save requests overwrite external edits, the watcher suppresses a later external write, renaming an upstream node breaks downstream Python, and successive optimiser jobs reuse obsolete utility code. The four specification findings from the first pass are retained separately below.

Review date: 5 September 2026. HEAD: `06c377e1fb0c5643d5c2bc781de73044daacdbb1`. The original review changed no repository files.

| ID | Priority | Finding | Evidence |
|---|---|---|---|
| F1 | P1 | Node code reaches unrestricted OS capabilities through Polars | Two failing regression probes |
| F2 | P1 | UC pointer publication has a read/write race | Failing deterministic interleaving probe |
| F3 | P1 | Parser silently drops connections to private submodel children | Failing parser regression probe |
| F4 | P2 | Hosted restore selects the branch recorded at bind time | Failing save/publish/restart probe |
| F9 | P1 | Save overwrites unseen valid external changes | Failing route + filesystem probe; new in pass 2 |
| F10 | P2 | Watcher suppresses a later external write to the same path | Failing coalesced-event probe with passing control; new in pass 2 |
| F11 | P2 | Upstream rename breaks downstream Python input bindings | Actual frontend update plan + failing executor probe; new in pass 2 |
| F12 | P1 | New optimiser jobs reuse obsolete utility code | Failing repeated-job probe; preview gives the correct new value; new in pass 2 |
| F13 | P1 | Parser accepts unbound function parameters and Save rewrites the signature | Failing parse/execute/codegen probe; found 6 September 2026 by the ENG-T11 generated family, after the snapshot |

The following entries concern specification accuracy, retained from the first pass:

| ID | Priority | Finding | Evidence |
|---|---|---|---|
| F5 | P2 | Execution spec understates assistant verification | Conflicting original specs and current source |
| F6 | P2 | JSON cache specifications disagree about lock scope and worker lifecycle | Conflicting original specs and current source |
| F7 | P2 | Explore high-level spec requires exposing unexpected exception text | Conflicting original specs and current source |
| F8 | P2 | Modelling low-level spec misclassifies dependency ValueError | Internal spec contradiction and current source |

P1 means fix promptly because it threatens security, persisted work, or computed results. P2 means a material correctness, UX, or maintenance issue. Finding IDs are stable across passes. Documentation findings identify incorrect contracts; they are not claims that the corresponding current implementation has the same defect.

**Fix order.** Address F9 and F12 early because ordinary editing can lose work or compute obsolete results. F1–F3 are the other P1 boundaries. F10 should accompany the Save work, followed by F11 and F4. Each finding below includes the failure mechanism, evidence, and acceptance criteria; no fixes have been applied.

**Test-suite follow-up:** the companion test-gap analysis (retired with the programme it supported) mapped every implementation finding to its neighbouring tests and recorded 12 existing tests that passed despite the reproduced defects; the durable answer is the ledger record and Testing-section entry for each finding.

## F1 — P1: The node-code sandbox exposes unrestricted operating-system capabilities

**Trigger and result.** A transform with no imports and no preamble can access `pl.io.csv.functions.os`. A harmless probe read a synthetic environment marker through that object. A second probe wrote a CSV outside the configured project root through `DataFrame.write_csv`. Both passed the AST validator and executed. No actual secrets were read and no external process was launched.

**Cause.** [The execution namespace](../../src/haute/_user_exec.py#L71) receives the complete Polars module. Restricting builtins and selected attribute names does not restrict capabilities exposed by objects already in that namespace. Polars I/O also bypasses Haute's path-resolution helpers. The observed `os` object exposes capabilities substantially beyond dataframe transformation.

**Contract conflict.** [The security threat model](../../specs/sandbox-security/high-level.md#L9) treats copied/untrusted project inputs as attacker-shaped. [The two-layer execution claim](../../specs/sandbox-security/high-level.md#L74) and the deliberate distinction between privileged preambles and per-node text at line 98 overstate the protection actually provided to node text. The probe does not depend on the documented privileged-preamble exception.

**Correction.** Define and enforce the intended trust boundary before promising hostile-code containment. If node text is untrusted, confine its filesystem, credentials, process, and network capabilities outside the Python object graph. A separate process with the same privileges and environment is not enough. Adding this one attribute path to the denylist would leave the underlying capability problem intact. If the product instead trusts all executable project code, the specification and execution UX must say that explicitly.

**Acceptance evidence.** Retain the two probes, add a witness through the actual production execution entry point, and verify that a worker cannot read an injected secret or modify a path outside its permitted workspace while ordinary Polars transformations remain usable.

## F2 — P1: UC publication can silently overwrite another writer's generation

**Trigger and result.** Another writer updates `HEAD.json` after this writer's final read but before its write. A fault-injection probe at the Files API upload boundary installs a successor pointer in that exact interval. The original publisher returns successfully and replaces the successor pointer with its own.

**Cause.** [The final fence](../../src/haute/_uc_transport.py#L799) compares a read of the pointer with the initial snapshot; [the later publication](../../src/haute/_uc_transport.py#L823) is an unconditional write. These are separate operations. The advisory claim does not close this interval: the specification explicitly allows overlapping writers after lease loss/takeover and says the pointer fence provides correctness.

**Contract conflict.** [The storage invariant](../../specs/hosted-project-storage/low-level.md#L100) promises that every hazardous interleaving terminates loudly at the pointer, never with silent overwrite. The implementation does not establish that guarantee. The displaced bundle may still exist; the demonstrated loss is the authoritative latest-generation pointer, not proof that all historical bytes have immediately been erased.

**Correction.** Publication needs an atomic conditional update tied to the observed generation, or an exclusive writer authority whose fencing is enforced by the storage operation itself. Another pre-write read only moves the race. Apply the same publication rule to every path creating or replacing the authoritative pointer.

**Acceptance evidence.** Test the interval after the final read, a stalled predecessor resuming after takeover, initial publication with competing writers, and a rejected publisher retaining its local saves. Assert both returned status and the final remote pointer. The existing mid-flight test injects movement before the final read and therefore does not cover this interval.

## F3 — P1: The parser silently discards authored connections to private submodel children

**Trigger and result.** A parent registers a canonical submodel whose private function is `transform`, then authors `connect("source", "transform")` and `connect("transform", "sink")`. Parsing succeeds with three parent nodes and **zero edges**. It neither preserves nor rejects the two authored connections.

**Cause.** [Submodel merging](../../src/haute/_parser_submodels.py#L468) skips a connection when neither endpoint is an occurrence alias. [The conservation gate](../../src/haute/_parser_conservation.py#L105) then includes private child IDs in its set of permitted parent endpoints, so these discarded connections pass the dangling-edge check. The local-edge comparison also excludes non-root connections.

**Contract conflict.** [Pipeline-config](../../specs/pipeline-config/low-level.md#L263) says cross-boundary child references are accepted. [Expression parsing](../../specs/expression-parsing/low-level.md#L197) requires explicit public interfaces and conservation; [codegen](../../specs/codegen/low-level.md#L366) rejects private child endpoints. Current parsing satisfies neither preservation nor rejection. A later save of the accepted graph can omit the authored connections entirely.

**Correction.** Make parent connection validation use only root nodes and registered occurrence aliases with valid public ports. Reject a private child endpoint before an edge can be filtered out. Conservation must account for every authored connection through its final representation. Align pipeline-config with the canonical public-interface contract.

**Acceptance evidence.** Keep the failing two-edge probe. Cover each endpoint direction, child-to-child connections, and the same child definition used by two occurrences. Verify valid alias/port connections retain their identities through parse, flatten, and save.

## F4 — P2: Hosted restore reopens the branch recorded at bind time

**Trigger and result.** Bind an empty UC location on `pricing-dev`; create/select `pricing-alt`; save changed pipeline contents; publish successfully; simulate a replacement container. Restore selects `pricing-dev` and returns the old pipeline contents. The newly published work remains in the bundle on the other lineage, but it is not the session resumed by the application.

**Cause.** [Binding creation](../../src/haute/_project_storage.py#L875) records the initial working branch. [Publication](../../src/haute/_project_storage.py#L708) never refreshes it, and [restore](../../src/haute/_project_storage.py#L798) subsequently adopts that stale value. The maintained `write_binding` call sites are bind-time operations. Binding a populated remote records no branch, so later branch selection also has no persistence path through this record.

**Contract conflict.** [The hosted reopen and usability requirements](../../specs/hosted-project-storage/high-level.md#L63) promise a usable restored working lineage. Recording only the initial selection does not preserve the current published session.

**Correction.** Define the branch to resume as part of durable session state and update it consistently with successful publication. Ensure the recorded branch actually exists in the durable generation before advertising it as the restart target. Handle populated binds, subsequent branch changes, and branch removal under the same rule.

**Acceptance evidence.** Preserve the UC reproduction and add the equivalent Git-remote lifecycle test. Assert restored contents and the active save ledger as well as the branch name. The reproduction here exercised UC; the shared binding logic also warrants verification for Git bindings.

## F9 — P1: Save overwrites an unseen valid external edit

**Trigger and result.** The browser loads a graph that generates `rate = 1`. Another editor changes the persisted pipeline to `rate = 2`. Before the browser receives that change, it saves its earlier graph. The route returns `status="saved"` and the file contains `rate = 1` again. The reproduction verifies that the external file parses successfully and has a different document revision before invoking the real Save route.

**Cause.** [SavePipelineRequest](../../src/haute/schemas.py#L643) carries no expected/base revision. [The route](../../src/haute/routes/pipeline.py#L771) locks, loads the current document, and checks only that it is ready; it then saves the submitted graph without comparing it with the version the browser loaded. [The frontend captures its revision](../../frontend/src/hooks/usePipelineAPI.ts#L1174), but [the request omits it](../../frontend/src/hooks/usePipelineAPI.ts#L1184). The later request-sequence/revision checks govern UI acknowledgement after the write; they do not protect disk contents.

**Impact.** A delayed watcher notification, two browser tabs, or an older request reaching the server after a newer save can overwrite intervening work. The save lock serialises writes but does not distinguish fresh and stale snapshots. This is independent of F10, which increases the chance that the browser never learns of an external edit.

**Correction.** Send the revision on which the edit is based and require it to match the current document inside the write transaction. Reject stale saves with a conflict before modifying any artifact. Make the revision cover the document's owned source/config/recovery artifacts and define initial creation separately. This protects clients using the application protocol; external file writers also require an explicit policy for changes arriving during the multi-file transaction rather than an unsupported claim of filesystem-wide atomicity.

**Acceptance evidence.** The probe (local review artifact: `test_review_pass2.py:25`) currently fails because the external bytes are replaced. Add two-tab saves from the same base revision, delayed older requests, config-only edits, deletion/recreation, and the normal current-revision save. Assert both the conflict response and preservation of every owned artifact.

## F10 — P2: The watcher suppresses a later external write to the same path

**Trigger and result.** The server marks and writes `main.py`; an external editor then changes that file before the watcher processes its coalesced event. The watcher treats the path as a self-write and sends no document update. Running the same external modification without the server mark correctly broadcasts a `pipeline_document_update`.

**Cause.** [mark_self_write](../../src/haute/routes/_helpers.py#L269) records only a path and timestamp. [is_self_write](../../src/haute/routes/_helpers.py#L287) tests membership, without identifying which bytes were written. [The watcher](../../src/haute/server.py#L781) consumes that marker and drops every event for the path in the batch before reading the actual contents. Coalescing by path therefore conflates two writers.

**Impact.** The editor can keep displaying the earlier graph without the external-change notification. A later Save can lose the external edit through F9. Even with a correct Save precondition, the live view remains stale until another notification or reload.

**Correction.** Associate suppression with the committed content/revision, and suppress only while the current file still represents that server write. Account for failed writes and rollback writes when managing these records. Increasing or decreasing a time window cannot establish writer identity.

**Acceptance evidence.** The parameterised probe (local review artifact: `test_review_pass2.py:50`) uses the real watcher with an injected coalesced filesystem event. The unmarked control passes; the marked case fails. Add modifications, replacements, deletions, failed writes, and rollback events arriving within the debounce interval; verify the published document describes the final on-disk contents.

## F11 — P2: Renaming an upstream node breaks downstream Python

**Trigger and result.** A working transform executes `df = old_source.with_columns(...)`. Rename its upstream node from `old_source` to `new_source`. The frontend returns a successful update plan and changes the incoming edge name to `new_source`, but leaves the transform text referencing `old_source`. The real executor changes from `status="ok"` before the rename to `status="error", error="name 'old_source' is not defined"` afterwards.

**Cause.** [The rename handler](../../frontend/src/hooks/useGraphCommitController.ts#L194) resolves identities for the renamed node and commits [prepareNodeUpdate](../../frontend/src/utils/nodeUpdatePlan.ts#L351). [Edge reconciliation](../../frontend/src/utils/nodeUpdatePlan.ts#L144) changes input names. [collectMappingChanges](../../frontend/src/utils/nodeUpdatePlan.ts#L241) updates structured mapping fields but does not update `config.code`. [Runtime input binding](../../src/haute/_user_exec.py#L39) uses the new name only, so the original reference is unbound. The successful plan contains no removed-edge warning or other indication that it has broken executable code.

**Correction.** Treat executable input references as part of the rename transaction. Preserve the binding through an explicit stable mapping, or perform a Python-aware refactor of references that resolve to that input. Reject the operation with an actionable explanation where preservation cannot be established. A string replacement would incorrectly rewrite literals, attributes, or shadowed local names.

**Acceptance evidence.** The TypeScript probe (local review artifact: `probe_rename.ts`) runs the actual frontend preparation function; its output (local review artifact: `rename-result.json`) feeds the executor regression (local review artifact: `test_review_pass2.py:82`). The initial source identity fields match the identity service's ordinary-node contract. Add multiple consumers, name sanitisation, nested Python scopes, and strings/comments containing the old name. API-frame renames traverse related rebinding code and should receive an equivalent witness.

## F12 — P1: New optimiser jobs execute obsolete utility code

**Trigger and result.** Run optimiser setup with a preamble importing `VALUE = 10` from `utility.py`. Edit the utility to `VALUE = 200`, leaving the preamble text unchanged. Preview now produces `200`, proving the file change and normal dependency refresh work. Start another optimiser setup in the same server process: its input still contains `10`. The reproduction changes file size as well as mtime, so it does not rely on a timestamp-resolution edge case.

**Cause.** [Optimiser setup](../../src/haute/routes/_optimiser_service.py#L4581) calls `_compile_preamble(..., force_refresh=False)` for separate jobs. [The compiler](../../src/haute/executor.py#L556) substitutes the process-lifetime `"no-refresh"` marker for the dependency fingerprint, and [returns the existing namespace](../../src/haute/executor.py#L569). Stability within one batch does not make a cache entry valid across later jobs. A normal preview refresh populates a different cache key and does not replace this old namespace.

**Impact.** The optimiser receives obsolete features/objectives after an ordinary helper edit even while preview displays the corrected values. In the probe, the second setup also materialises a dataframe-cache artifact under the new graph fingerprint using the obsolete namespace; its logs show both the fingerprint change and materialisation. The solver itself was not invoked: the wrong input exists before that boundary.

**Correction.** Resolve and pin the dependency fingerprint/namespace at the beginning of each operation, then reuse that exact snapshot within its chunks or iterations. Tie the namespace and dataframe-cache key to the same dependency snapshot. Audit [the streaming auto-range call](../../src/haute/routes/_optimiser_service.py#L4212) and [the data-output preparation call](../../src/haute/executor.py#L2021), which use the same no-refresh mechanism. The concrete reproduction here covers successive optimiser setups.

**Acceptance evidence.** The regression (local review artifact: `test_review_pass2.py:98`) invokes `OptimiserSolveService._execute_pipeline` twice with real builders and caching, and compares the second input with a correct intervening preview. Add separate estimate/solve operations after a helper edit, direct repeated output preparations, unchanged-dependency cache hits, and a mid-operation edit that must not mix namespaces between chunks.

## F13 — P1: The parser accepts unbound function parameters and Save rewrites the signature

Found on 6 September 2026 by the ENG-T11 generated submodel-endpoint family, after the review snapshot; recorded here so the catalogue stays the single list.

**Trigger and result.** A hand-authored consumer whose parameter name matches no connected input parses cleanly: `def sink(foo)` fed by `pipeline.connect("source", "sink")`, or `def sink_a(a)` fed by an occurrence registered as `a`. Execution then fails with `NameError` because the executor binds the frame under the connected input name only. Saving the document rewrites the signature to that connected name while leaving the body untouched (`def sink(source)` with `df = foo`), so the saved file is no longer executable and the authored name is lost.

**Cause.** [Edge inference](../../src/haute/_graph_builders.py) matched parameters to upstream node ids and silently ignored every other parameter, and the structural acceptance gate compared authored and parsed identities without asking whether each parameter had an input at all. For an occurrence output the executable input name was the definition's public output port label, so the name the author connects by (`a`) and the name the code had to use (`Result`) differed, and nothing said so until execution.

**Contract conflict.** [The node-editor contract](../../specs/frontend-node-editors/high-level.md) promises that connected inputs are the exact argument names of the node's code, one-to-one with the generated signature; the parser did not enforce that promise for authored files, and [the codegen contract](../../specs/codegen/high-level.md) let the generated signature diverge from the authored body.

**Correction.** The parser now enforces the promise with no inference: every positional parameter must equal a connected input (or an `inputMapping` entry), every connected input must be consumed, and any other shape is a `ParseError` carried into the editor as a degraded document, so Save is refused and the file is untouched. An occurrence output contributes the occurrence's own name (`a`, or `a__<portId>` when the definition declares several output ports) instead of the port label, because occurrence names are unique in the parent while port labels of unrelated definitions collide (every example definition labels its output `Result`). Inside a definition the same principle applies on the way in: a node fed by a public input port binds its parameter to the port id, the name the parent connects by, and port labels are display only.

**Acceptance evidence.** `tests/test_parser_conservation.py::TestPolarsParameterBinding` (unbound and unconsumed cases with exact diagnostics, the mapped form executing and round-tripping byte-identically, the occurrence-name form executing and the port-label form rejected, the degraded document and the refused save, definition input ports), the executable-name unit tests, and the generated family in `tests/test_submodel_endpoint_properties.py`, which now authors consumers by occurrence name and computes rows through flattening.

## Retained specification findings

### F5 — P2: The execution specification understates assistant verification

[Execution-engine low-level](../../specs/execution-engine/low-level.md#L1655) says the v1 post-save tier is structural and stronger plan verification is future work. [Assistant high-level](../../specs/assistant/high-level.md#L519) says executable-flow changes use exact lazy-schema evidence, with structural-only verification reserved for changes without an executable target.

[The application service](../../src/haute/assistant/_application.py#L320) currently resolves affected schemas and selects `schema` when evidence exists. This is an implemented obligation, not future work. Following the execution spec during a refactor could remove a required verification step while still appearing compliant with that document.

**Correction.** Update the execution component's interaction section to state the actual tiers and link to the assistant-owned planning and verification contract. Keep explicit that schema evidence is not row-level execution or model-quality proof. Existing [assistant application tests](../../tests/test_assistant_application.py#L564) exercise this distinction; no new parallel schema implementation is needed.

### F6 — P2: JSON cache specs disagree about lock scope and worker lifecycle

[JSON-shredding low-level](../../specs/json-shredding/low-level.md#L633) says the build lock is process-local. [Server-api low-level](../../specs/server-api/low-level.md#L428) specifies a cross-process lock, an isolated child preparing staging, and parent-owned validation, cancellation, publication, and cleanup. [Caching low-level](../../specs/caching/low-level.md#L112) still compresses this into blocking shred work with a response timeout, omitting the lifecycle guarantees.

The implementation supports the server contract: [the lock uses native file locking](../../src/haute/_json_shred/_publication.py#L133), [the parent holds it around the worker transaction](../../src/haute/routes/json_cache.py#L192), and [the route awaits the cancellable transaction](../../src/haute/routes/json_cache.py#L620). A maintainer following the process-local description could weaken cross-process publication safety or release resources before the child exits.

**Correction.** State the native cross-process lock and per-process reentrancy separately. Describe the library in-process path and HTTP worker path explicitly, with one owning description of publication and cancellation ordering. The absence of a separate GUI cancel endpoint remains true; it must not be confused with the server's cancellation/timeout guarantees.

### F7 — P2: Explore's high-level spec requires disclosing unexpected exception text

[Explore high-level](../../specs/explore-eda/high-level.md#L402) says unexpected materialisation/summarisation failures put `str(exc)` in the job message. [Its low-level contract](../../specs/explore-eda/low-level.md#L380) says unexpected worker and parent details are diagnostic-only and public jobs store the fixed internal-error detail.

[The current service](../../src/haute/routes/_explore_service.py#L1325) follows the redacted policy. The high-level requirement is therefore both inaccurate and unsafe guidance for future changes: arbitrary dependency text can contain paths and other internal details. This review did not reproduce an Explore disclosure in the current implementation.

**Correction.** Make the high-level failure model preserve typed, explicitly public errors and describe unexpected text as server-side diagnostics only. Link to the owning public-error policy rather than repeating a different catch-all rule.

### F8 — P2: Modelling's low-level spec misclassifies dependency ValueError

[Modelling low-level](../../specs/modelling/low-level.md#L661) says a bare `ValueError` from training becomes `contract_error`. The same file at [line 679](../../specs/modelling/low-level.md#L679) explicitly says only `HauteValidationError` has trusted validation provenance; a dependency's plain `ValueError`, including Pydantic validation errors, receives a type-only unexpected-error treatment. The dispersion summary at line 746 repeats the broad `ValueError` claim.

[The worker classifier](../../src/haute/routes/_training_worker.py#L351) checks `HauteValidationError`, then lets ordinary `ValueError` reach the generic error path. Implementing the earlier paragraph would mislabel dependency/programming failures as user contract errors and could reopen the message-disclosure channel that the later paragraph explicitly closes.

**Correction.** Use the marker type consistently in the training and dispersion descriptions. Explain that inheritance from `ValueError` alone is insufficient provenance. Keep the distinction between safe public validation wording and private diagnostic fields.

## Verification and review scope

- Second pass: `uv run pytest <local-review-artifacts>/test_review_pass2.py -q --tb=short` produced **four expected failures and one passing control in 2.62 seconds**. Each failure is at its intended behavioural assertion, establishing F9–F12. Failure log (local review artifact: `pass2-regressions.txt`).
- F11's frontend preparation was bundled with the repository's installed esbuild and executed with Node before the Python test. To regenerate it: run `node frontend/node_modules/esbuild/bin/esbuild <local-review-artifacts>/probe_rename.ts --bundle --platform=node --format=cjs --outfile=<local-review-artifacts>/probe_rename.cjs '--define:import.meta.env={}'`, then run the generated file and write stdout to `rename-result.json` beside the tests. The quoted define argument is required in PowerShell.
- The second pass checked code paths for stale writes and request ordering, watcher coalescing, rename propagation, rollback handling, utility reload/cache identity, repeated optimiser setup, and rating/join key and projection handling. Four additional implementation findings survived source tracing and reproduction. Areas inspected without a finding are not treated as test passes. Read-range record (local review artifact: `pass2-ranges.json`).
- `uv run pytest tests/test_docs_accuracy.py -q`: **61 passed**, one dependency deprecation warning. Original log (local review artifact: `docs-accuracy.txt`).
- Five review-only regression probes (local review artifact: `test_review_regressions.py`) fail at the expected assertions, establishing F1–F4. F1 has two probes. Combined failure log (local review artifact: `regressions-final.txt`). These failures are findings, not a passing validation claim.
- Probes use temporary projects, synthetic markers, and an in-memory Files API stand-in. No production remote was contacted. No repository implementation or specification was edited, and no full CI/browser suite was run.
- The snapshot contains **35 high/low pairs, two supplemental documents, four governance files, and four roadmap files: 80 files total, including 31,044 Markdown lines**. The working-tree corpus digest is `c0e445bc7b1f570941a70be98247cca54514ea85d3622b1ce23969e6f1f0ca37`.
- First-pass specification coverage is preserved in the coverage summary (local review artifact: `coverage.md`), ledger (local review artifact: `coverage.toml`), and inventory (local review artifact: `inventory.json`). The original report (local review artifact: `first-pass-report.md`) remains available. The second pass concentrates on implementation defects and retains its own read-range and reproduction evidence.

The strongest second-pass pattern is that identities are recorded without being enforced at the operation that needs them: the Save revision is absent from the request, a write marker identifies a path rather than its content, a rename updates the edge rather than every consumer, and an optimiser cache key changes independently of its executable namespace. The corrections should make each identity govern the actual write, notification, refactor, or computation.
