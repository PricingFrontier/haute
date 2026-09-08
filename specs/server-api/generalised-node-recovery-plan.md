# Generalised node recovery — implementation plan

Status: accepted implementation specification, 7 September 2026. It extends the
currently implemented
[node recovery actions](node-recovery-actions.md).

## 1. Intended behaviour

The primary action is **Recover settings**: reconstruct a node using its current
definition, retain compatible authored settings and user code, replace incompatible
settings with appropriate defaults or unpopulated fields, and explain every change.
The user can finish configuring the node in its normal editor. Recovery is explicit;
merely opening a broken pipeline never rewrites it.

Keep **Reset all settings and code** as a separate, clearly destructive choice.
Keep recognised format updates, including the demo's old submodel registration, as
explicit adapters within the same recovery process. General recovery handles ordinary
field additions/removals/type changes; adapters handle changes whose meaning cannot
be inferred from the current schema.

The recovery promise is to preserve trustworthy information and restore editability.
It is not a promise to preserve behaviour after arbitrary changes or to fix runtime
bugs, missing dependencies, data, credentials or external services.

## 2. Existing foundations and gaps

These are observations of the current checkout, including the recovery work already
present in the working tree:

- `_types.py` defines **19 node types**. The configuration TypedDicts describe fields,
  but are not complete runtime schemas; several use broad `str` or `dict[str, Any]`.
- `_config_validation.py` already owns the field registry and some shared validation.
  Detailed contracts also live in `_api_input_schema.py`, `_polars_io_registry.py`,
  `_rating.py`, `_rating_step_config.py`, the Explore validators, training configuration
  and optimiser validation. Recovery must reuse those authorities.
- `node_defaults.json` is shared by the palette and reset, but defaults still need
  auditing against current contracts. For example, the Live Switch palette default
  contains `mode`, whereas its current typed configuration uses `input_scenario_map`.
  Missing required paths also mean some fresh defaults are not loadable.
- `_pipeline_recovery.py` preserves unavailable nodes, diagnostics and raw-artifact
  revisions. Document mutation/save/execution are currently gated on `ready`.
- `_pipeline_repair_actions.py` currently replaces ordinary-node settings wholesale
  and refuses an action whose target still cannot load. It has no editable partial
  configuration or persistent repair draft.
- `_pipeline_repair.py` already provides confirmed plans, exact-byte comparisons,
  staged writes, conservation checks and rollback. `_python_syntax.py` supplies the
  LibCST boundary for surgical valid-source changes.
- Submodels have one shared file-backed definition and one owner occurrence, plus
  optional copies. Public port names are structural identities. Nested submodels are
  currently unsupported, and `submodelPort` is an editor projection rather than an
  independently authored executable node.

Implementation must also reconcile any older spec wording about occurrence renames
with the current name/alias identity contract. Recovery must not introduce another
interpretation of these identities.

## 3. Make incomplete recovery editable without weakening executable contracts

Use a **persistent recovery draft**, separate from the canonical graph and source.
It contains proposed settings, retained code, original evidence and outstanding issues.
The existing node editor edits this draft through a dedicated adapter.

This avoids writing an incomplete config into source and immediately loading the
same unavailable, read-only node again. It also avoids enabling whole-graph Save on
a degraded graph, where omitted or unresolved source could be lost.

| Draft state | User experience | Source behaviour |
| --- | --- | --- |
| `needs_configuration` | Editable fields with missing/invalid values highlighted; show dependency blockers separately | Save draft only; original source stays unchanged |
| `review_required` | Review defaults, removals, code decisions or effects on other occurrences | Original source stays unchanged |
| `ready_to_apply` | Show validated changes and affected files/nodes | Apply is available after a fresh preview |
| `stale` | Explain source, draft or installed-contract changes; allow comparison and rebuilding the proposal | Apply is blocked; retain the user's draft edits |
| `applying` | Prevent duplicate submission and conflicting edits | Apply through the shared transaction |
| `applied` | Reload the authoritative document and retain a recovery record | Node may be ready or blocked by another unresolved node |
| `manual_action` | Explain missing identity, source damage or unsupported structure; retain salvageable draft values | No speculative source replacement |

Discard closes the active draft without modifying source. Applied recovery records
remain available for an explicit restore operation. A corrupt draft is reported as
corrupt; it never silently becomes an empty draft.

Keep draft states separate from the existing document `ready/degraded/source_only`
contract. Do not make draft objects acceptable to `PipelineGraph` or executable API
payloads. The UI can show **Needs configuration** on a selected recovery card without
changing the authoritative source node's availability.

An incomplete upstream node should not prevent editing a downstream draft. References
that cannot yet be verified remain unresolved. Apply a valid repair independently
when safe; use an explicitly scoped atomic repair group when coupled changes cannot
be validated independently.

## 4. One configuration contract per type

Extend the existing configuration registry with a uniform `NodeConfigSpec` interface;
do not build a disconnected recovery-only schema catalogue. Each entry supplies:

- A stable contract revision and fingerprint, current node factory and default factory.
- Structural field definitions, nested object/array shapes, permitted discriminants,
  meaningful absence versus `null`, safe defaults and required fields.
- The existing strict validators, adapted to return structured field/group diagnostics
  as well as supporting the current exception-based callers.
- Conditional dependencies and validation groups, such as provider/format/arguments,
  factor/rules/default, or algorithm/task/hyperparameters.
- Typed reference fields: columns, incoming frames, public ports, node owners and
  artifact locators. Plain strings and arbitrary dictionary keys are not references.
- Declared user-code slots, the matching extractor/generator and any known format
  adapters. Identity, ownership and public interfaces have dedicated policies.

Generate the browser's recovery schema/diagnostic types from this contract. Reuse
the current editors and their controls; do not maintain competing client defaults
or reimplement authoritative validation in TypeScript.

Expose distinct validation results for draft shape, configuration completeness,
static graph/code bindings, and runtime/environment readiness. A missing required
field or a proven invalid public port blocks Apply. An otherwise valid column
reference whose upstream data schema is not yet known is **unverified**, not a
proven error: retain it, report the limitation, and allow normal explicit preview/run
to validate it later. Do not require executing data or contacting MLflow simply to
finish a repair. `ready_to_apply` means a valid source proposal, not guaranteed runtime
success. Recovery does not weaken the normal execution/deployment admission checks.

Audit all 19 entries and all supported provider/mode branches before calling the
feature complete. Defaults must be valid *drafts*, with every unmet completion
requirement explicitly represented. They need not all be executable configurations.

Contract revisions describe installed semantics and invalidate stale repair plans.
Do not inject internal recovery metadata into authored domain config. Existing
unversioned sources can be recovered conservatively against the current schema;
known renames/meaning changes still require explicit adapters or user decisions.
Stored contract metadata for future migrations belongs in a separately specified
metadata envelope if introduced, not guessed from field names or package version.

## 5. Recovery algorithm and preservation rules

1. Reload the authoritative source and raw artifact manifest. Establish the target's
   unambiguous type, source ownership, span and graph context. Preserve original bytes
   before interpreting values, including missing-file sentinels.
2. Read raw config through a recovery-specific reader that reports errors instead of
   invoking the strict loader and losing the original data. Preserve decorator config,
   sidecar config, code and their provenance separately; use existing precedence rules.
   Conflicting authorities require a decision rather than a guessed merge.
3. Apply only explicitly registered, matching format adapters. Record every identity
   or field mapping. Do not map renamed fields by spelling similarity or matching type.
4. Resolve supported discriminants first. A recognised branch keeps its own defaults
   and constraints. An unknown provider, algorithm or mode stays unresolved; do not
   silently turn a database into a file input or a ratebook optimiser into online mode.
5. Construct a candidate from the current branch defaults. Retain each authored field
   that is structurally and semantically valid under its current contract. Preserve
   `false`, `0`, empty collections and explicit `null` where those values are valid.
   Do not coerce strings to numbers, booleans to integers, or invalid enums by accident.
6. Validate nested settings by their declared shapes. Reconcile collections using
   schema-declared identity when one exists; otherwise preserve original order and
   original-index provenance. Never zip old lists with default rows. Never prune dynamic
   rating-factor keys, model parameters or JSON data using a generic key allowlist.
7. For an invalid field, offer its safe default or leave it unpopulated. For a coupled
   validation failure, isolate the smallest declared group that can be identified
   reliably. If several repairs are possible, retain the group as an unresolved draft
   and explain the choices. Do not repeatedly delete arbitrary error paths until a
   validator happens to accept the result.
8. Keep removed/invalid original values in the recovery record. Report outcomes as
   `retained`, `defaulted`, `needs_input`, `removed`, `needs_review` or `blocked`, with
   a structured path and reason. New required fields without safe defaults are blank.
   An invalid rule/constraint must not disappear and produce a seemingly complete
   pricing model: require explicit review of behaviour-changing removals/defaults.
9. Reconcile code, references and connections under the policies below. Incomplete
   dependencies are blockers, not grounds for discarding otherwise valid settings.
10. Validate the completed candidate locally, generate current scaffolding in an
    isolated artifact copy, reparse and check conservation. User confirmation is bound
    to these exact bytes, the draft revision, source revision and installed contract.

“Retain as much as possible” means deterministic preservation of independently valid
information. It does not mean choosing a mathematically maximal subset of conflicting
rules or claiming to infer the user's intended business logic.

## 6. Coverage of every node type

The table describes recovery policy, not permission to execute work during recovery.
All types also inherit the source, reference, ownership and transaction rules.

| Type | Retain where valid | Defaults, incomplete fields and specific boundaries |
| --- | --- | --- |
| `apiInput` | Input path, table/column mappings, labels, selected/emit flags, dtypes, row IDs and categorical levels | Validate JSONPath structure, parent/child table relationships, duplicate/sanitised labels and emitted frame identities with `validate_v2_schema`. Repair the affected table/column without silently dropping frames. A missing sample file is distinct from corrupt mappings. Check document-wide singleton constraints. |
| `dataInput` | Supported provider, format, mode, locator/query/records, arguments and post-load code | Cover file, database, lakehouse, Databricks and inline branches; read/scan capabilities, database locator exclusivity, table/query selection and retired arguments. Unknown branches require selection. Missing paths/queries remain blank; never read a dataset or connect to a database to generate the draft. |
| `dataOutput` | Provider, destination, format, supported sink/write mode and arguments | Cover file, database and lakehouse branches. Preserve destination and overwrite/append intent; never supply a changed destination or destructive write mode silently. Missing destinations are incomplete. Recovery never writes output data. |
| `polars` | Authored transformation code, declared input mappings and shared projection settings | Rebuild the wrapper/signature only from resolved bindings. Empty code requires configuration. Preserve uncertain code for editing; explicit full reset archives it and creates the normal incomplete template. Do not invent a passthrough. |
| `edgeJoin` | Join kind, keys, suffix, coalesce, cardinality validation and order policy | Validate coupled `on` versus left/right keys and join-kind rules. Bind the exact `base` and `join` edges; never infer roles from edge order. Missing keys/inputs remain incomplete. Removed legacy role fields need explicit mapping. |
| `modelScore` | Run/registered-model selection, version, task, output name, feature-contract reference and post-processing code | Keep the source branch and version selection; do not replace a pinned version with `latest`. Missing model/contract artifacts or MLflow access are environment issues. Do not download/load a model during recovery. Retain classification/regression-specific constraints. |
| `banding` | Each factor, column, output, rules, default and boundary policy | Cover continuous, categorical and breakpoints representations. Validate ordering, overlaps, paired breaks/values, duplicate categories and output names. Preserve valid factors when another fails. Removing a pricing rule requires review; do not substitute a neutral rating silently. |
| `ratingStep` | Tables, dynamic factor keys, dtype contracts, entries, defaults, miss policy, combined outputs and post-rating code | Treat factor definitions and rows as a dependency group; validate duplicate keys/outputs and combined-operation references. Preserve row order and exact authored values. Do not change error-on-miss to neutral or silently remove rate rows. |
| `output` | Enabled mappings, source frames/columns, JSON output paths and format | Validate each mapping and output-path conflicts, including arrays and multi-mapping. Preserve intentional disabled rows. Unresolved frames stay visible. Do not manufacture a response mapping. Check singleton constraints. |
| `explore` | User transform, overview cards, chart specs, pivots, formulas, ordering and formatting | Reuse the existing versioned chart/pivot contracts. Repair independent cards separately, retaining IDs and formula dependencies. Missing columns are unresolved until schema evidence is available. Discard derived preview/cache state, never authored formulas. |
| `externalFile` | Artifact path/type, supported model class and custom processing code | Validate type/class combinations. Missing files or packages do not justify changing the artifact type. Never unpickle, load a model or execute custom code as part of recovery. |
| `liveSwitch` | Valid scenario-to-input mappings and authored source selection | Reconcile exact connected frame identities and the pipeline source list. Do not select the first input or replace live/batch routing with a default branch. Unmapped scenarios remain incomplete. Audit the stale palette default and enforce singleton rules. |
| `modelling` | Target/weight/features, algorithm/task, supported parameters, evaluation/tuning, metrics and tracking/output settings | Cover CatBoost and GLM, family/link, terms/interactions, regularisation, folds and feature constraints. Algorithm-specific settings form separate groups. Flag changed training semantics. Do not train, tune, register a model or clear existing artifacts. |
| `optimiser` | Online/ratebook mode, objective/columns, constraints, solver settings, frontier, factor structure and selected inputs | Validate bounds, ranges, counts, tolerances and coupled constraints. Preserve online/ratebook and explicit/auto structure choices. Missing `data_input`/`banding_source` stays unresolved. Do not solve or register artifacts during recovery. |
| `scenarioExpander` | Quote ID, output/index names, range, step count and post-expansion code | Validate the range/count as a group and detect output collisions. Do not choose a new scenario range to make validation pass. Warn on configured expansion limits without materialising rows. |
| `optimiserApply` | File/run/registered artifact selection, pinned version, version/value output columns, optimiser mode and ratebook input | Preserve the selection branch and exact ratebook binding. Missing artifact metadata is an environment blocker, not grounds to assume online mode. Do not load/apply an optimiser to test recovery. |
| `constant` | Named literal values and their valid representations | Validate duplicate names and literal/value rules using existing evaluation rules. Preserve zero, false and null where supported. Repair only invalid entries; do not inject the palette's sample constant when valid authored entries already exist. Never evaluate arbitrary expressions to salvage them. |
| `submodel` | Definition identity/file, owner/copy relationship, occurrence identity/position, public interface, child source/config and bindings | Recover registration, shared definition and individual children as separate scopes. No whole-child-graph reset to `{}`. See section 7. |
| `submodelPort` | Public input/output declarations and their internal routes | Edit the owning definition's contract. This is not a standalone node factory/reset target. Preserve the distinction between a valid unbound input declaration and an invalid bound-but-unrouted input. An output needs one source. |

Universal metadata must also be covered: `instanceOf`, `inputMapping`, selected
columns, renames, categorical levels and explicit column contracts. Preserve semantic
fields only when valid for the current type; recompute internal editor/runtime data.
Do not default structural identity fields through the ordinary settings algorithm.

## 7. Submodels, ordinary instances and shared ownership

### Distinguish the scope of the repair

- **Occurrence registration/bindings:** changes only that occurrence and its relevant
  parent edges, except where an explicit shared interface change is necessary.
- **Definition metadata/interface:** changes the shared definition once. List every
  affected occurrence and parent pipeline discovered in the project before Apply.
- **Child node:** uses the ordinary type policy against the definition's source, with
  all occurrences inheriting the result. Preserve sibling nodes and their config.
- **Boundary card:** edits public ports/routes in the definition; never generates a
  standalone decorated function or resets the entire definition.

Copies stay read-only internally. Offer navigation to the owner for a shared repair.
Do not introduce per-instance overrides, automatically detach a copy, choose a missing
owner, or promote a copy to owner. Those are separate explicit structural decisions.
Apply the same principle to ordinary `@pipeline.instance`/`@submodel.instance` nodes:
preserve their owner and input mapping, repair shared settings at the owner, and
validate all affected mappings. Missing owners, cycles and chains require resolution.

### Interface and graph edge cases

1. Retain the immutable definition ID and exact canonical occurrence name/alias.
   Duplicate IDs, conflicting files, case/sanitisation collisions, multiple owners,
   self/cross-definition ownership and ambiguous registrations block automatic Apply.
2. Preserve public port names and direction. Known legacy `portId` conversions use the
   explicit adapter. Never derive identity from a label, filename or list position.
3. Preserve input fan-out, ordered targets, single-output sources and named parent
   handles. Duplicate target routes, invalid directions, missing children/handles and
   multiple parent bindings to one input become individual unresolved issues.
4. Invalid endpoints remain visible in the draft; do not silently remove their edges.
   A user can explicitly rebind or remove a port/edge in the scoped recovery editor,
   with a preview of every affected occurrence and route.
5. Unbound input declarations with zero routes remain valid where allowed by the
   existing contract. Binding that input while it remains unrouted is not valid.
   An output with no source remains incomplete; do not generate a fake child source.
6. Changes to output count can change downstream executable names (`Inputs` versus
   `Inputs__port`). Recompute schema-owned references and generated parameter bindings
   only from exact edge/port identity. Custom code using old names is handled separately.
7. Missing/unreadable child files keep the occurrence card, port evidence and external
   edges. Offer relinking to an explicitly selected, compatible definition or restoring
   the file. Do not recreate an empty child graph and label it recovered.
8. Preserve the active pipeline's config base for children under `modules/`; do not
   reinterpret config paths relative to the child folder. If the same definition file
   is referenced by roots with different config bases, include each root's resolution
   in the impact analysis; refuse ambiguous shared edits.
9. Check document-wide `apiInput`, `output` and `liveSwitch` singleton rules after
   occurrence expansion. Recovery must not create extra runtime singletons by copying
   or reassigning a definition.
10. Nested submodels remain unsupported. Retain and diagnose nested/self-recursive or
    cyclic references with bounded traversal; do not flatten, erase or implicitly add
    support for them during recovery.
11. A broken child must not prevent inspecting sound siblings. Partially valid
    definitions remain navigable in recovery, with port and child blockers visible.
12. For definition/config files shared across multiple pipeline roots, build a reverse
    ownership index using supported source discovery, never project execution. If
    relevant ownership cannot be established, require manual resolution before the
    shared write. Validate and refresh every affected root, not just the opened one.

## 8. User code, source and connections

Retain original code bytes in every recovery record, even when an extractor recognises
the generated scaffold. Use the existing extraction matcher registry and LibCST
transform boundary; do not add broad regex replacements or re-emit an entire degraded
pipeline from its recovered graph.

Regenerate only recognised scaffolding. Preserve custom decorators, imports, helper
functions, descriptions, comments and surrounding source. Additional decorators,
computed config, unfamiliar scaffolding or ambiguous boundaries require code review;
they are not permission to replace the whole function.

For node types without a user-code slot, recovery must verify the authored body
against the current generated scaffold before allowing regeneration. A custom or
unrecognised body remains a manual source action; only an explicit full reset may
replace it. Compare syntax independently of formatting, while retaining the authored
config reference and submodel receiver. Ordinary generated bodies remain recoverable.

Custom code has three outcomes: retained unchanged, updated through an explicit and
verifiable binding adapter, or retained in the draft with a code issue for the user.
Static syntax/binding checks cannot prove arbitrary Python behaviour. No eval, module
import, user function call, code execution or external object deserialisation is used
to discover which code to keep. Renaming variables inside strings or unrelated scopes
is prohibited. A deliberate empty Polars template stays incomplete until configured.

Keep edge and handle identities where valid. Preserve unresolved evidence outside
the executable candidate; do not delete it to make validation pass. Validate arity,
duplicate frame names, exact join roles, cycles, multiple frame outputs, dangling
references and generated input mappings. A consumer can retain a valid column
selection while its upstream schema is unavailable; mark it unverified and recheck
when authoritative schema evidence is available.

Source syntax damage may still allow config salvage into a draft. Applying that draft
requires a trustworthy source rewrite boundary. For truncated functions, duplicate
definitions, computed registrations or an unparseable whole file, provide a source
diagnostic and manual repair path. Do not treat parser or application defects as
ordinary invalid settings; unexpected exceptions continue to fail clearly.

## 9. Storage, API, concurrency and restoration

Persist drafts and original bytes under a dedicated, application-owned
`<project>/.haute/recovery/` namespace, separate from disposable caches, source sidecars
and deployment assets. Reuse the existing artifact ownership/path protections.
Ensure project discovery, watchers, packaging and cleanup exclude draft internals.
Project templates must ignore this namespace; the current repository already ignores
its root `.haute/` directory. Hosted sessions must follow the existing project-storage
policy and advertise durability accurately before this feature is enabled there.

A draft records: format version; draft ID/revision; root and target source identities;
base raw-artifact revision; node-contract fingerprint; proposed settings/code; field
provenance/outcomes; unresolved bindings; affected owners; and immutable original
artifact hashes/bytes, including files that did not exist. Backups may contain secrets;
keep them local to the project's existing access boundary, mask diagnostics/logs, and
never add them to ordinary graph payloads or telemetry.

Proposed API surface, using a separate typed draft DTO rather than extending executable
graphs with optional recovery fields:

| Operation | Responsibility |
| --- | --- |
| Create/list/read recovery drafts | Reload authoritative evidence; create a durable candidate when explicitly requested; allow resumption after reload |
| Patch draft with `draft_revision` | Accept typed settings, code-slot edits and explicit reference/repair choices; reject unknown operations and arbitrary source paths/spans |
| Preview draft | No source/config writes; revalidate and return field changes, source diffs, affected nodes/files, completion status and plan hash |
| Apply draft | Require draft revision, source revision and plan hash; recompute under the shared mutation lock; commit exact server-produced bytes |
| Discard draft | End draft editing without source changes; explain any record-retention choice |
| Preview/apply restore | Reuse exact-byte planning to restore the recorded artifacts only when current state still matches the applied recovery |

The installed schema/default/generator/adapter fingerprint and relevant installed
I/O capability contract are part of plan validity, even
when no project file changed. A library upgrade while a dialog is open invalidates
the old proposal. Rebase is explicit: compute a new candidate and show conflicts;
never silently replay stale user choices onto a different schema.

Reuse the existing source revision, save lock and transaction helpers. Add a durable
apply journal before the first source write so process termination can be diagnosed
and reconciled on restart. Stage backups, validate the staged result and check exact
node/edge/ownership conservation against the approved repair scope. A valid repair
may leave other nodes degraded; it cannot introduce unacknowledged losses or new
errors in previously sound nodes outside that scope.

After commit, publish one authoritative update for each affected root, invalidate
preview/schema/trace caches and mark intersecting drafts stale. Retain unrelated
selection/viewport where its identity still exists. Two tabs cannot apply an old draft
revision; retry after a lost response returns the recorded result for the same apply
operation rather than performing another write.

Rollback/restore must never overwrite a concurrent external edit. Compare current
bytes with this operation's staged bytes before restoring. On startup, finish a fully
committed verified journal or roll back an incomplete one only when ownership and
all expected hashes agree; otherwise surface the exact conflict and preserve both
versions. Do not promise filesystem-wide atomic visibility to arbitrary external tools.
If multiple Haute processes can mutate one project, use a project-scoped interprocess
mutation lock shared by all relevant writers; the current in-process lock alone is
insufficient.

No age-based deletion of active drafts or originals needed by a pending/failed repair.
Use explicit discard/retention controls and bounded storage errors rather than losing
the only copy of discarded settings. Restore only touches files recorded by that
operation, including safely removing a file that the operation originally created.

## 10. Edge-case acceptance matrix

| Case family | Required behaviour and evidence |
| --- | --- |
| Missing/new/removed/wrong-type settings | Retain independent valid fields; use declared defaults or explicit missing values; show removals and provenance; no coercion or fabricated required values |
| Nested lists and relational config | Preserve order/identity and dynamic keys; duplicate IDs and dependent entries remain unresolved; no index-based accidental row matching |
| Coupled and semantic failures | Validate the relevant group; require review for changes to rates, constraints, selection, destinations and source branches |
| Absent/corrupt/duplicate-key config | Missing file can yield a new draft; malformed JSON or duplicate keys preserve raw bytes and require an explicit full replacement or manual correction; never accept a last-key-wins guess |
| Unknown keys and future formats | Archive obsolete fields; distinguish supported extensions from truly unknown keys; unsupported schema versions need an adapter or user review |
| Missing type/dependency/service | Unknown type stays visible with restore/install or explicit replacement guidance; a missing package, model, data file or service does not erase a valid config |
| Runtime-only failure | Recovery is available explicitly where useful, but does not automatically rewrite settings for exceptions, permissions, network failures or changed data; no success claim from parse alone |
| Custom source | Preserve BOM/line endings/Unicode/comments and unrelated spans; reject ambiguous rewrites; retain original code after full reset |
| References and graph changes | Validate frame/column/port identity, input mapping, arity and cycles; retain unresolved edges; distinguish unavailable schema evidence from a proven missing column |
| Shared config and instances | No invisible shared writes or silent detachment; show owners and validate the whole approved impact set |
| Submodel variants | Owner/copy, zero/multiple ports, fan-out, missing definitions, broken child, singleton restrictions, conflicting roots and unsupported nesting follow section 7 |
| Concurrency | Changes to root, child, config, sidecar, membership or draft/schema revision invalidate preview; double click, two tabs, duplicate requests and source changes mid-apply are covered |
| Crash and filesystem failure | Disk full, permissions, missing directory, locked files and partial writes preserve originals; journal restart/restore avoids clobbering external edits |
| Path and ownership | Reject traversal, escaping symlinks/junctions, Windows reserved names, case collisions and aliasing with another managed artifact; detect unsafe shared/hard-linked targets rather than overwriting another node's data |
| Scale | Bound source/config bytes, nesting, collection counts and reference traversal; paginated/capped display diffs disclose omissions while complete originals remain retained; cancellation never reports a partial plan as ready |
| UI lifecycle | Refresh/restart resumes the draft; switching nodes/pipelines, undo/redo, Escape/cancel, stale responses and apply-in-flight are fenced; normal Save/Run never consumes a draft overlay |
| Transport and trust | Strict versioned DTOs; existing local authentication; source paths and byte spans remain server-owned; masked values in summaries; no dependency installation or external execution during planning |

Performance checks must include large rating tables and API schemas. Read each shared
artifact once per plan, cache immutable contract descriptions, and avoid reparsing the
entire project on every field keystroke. Debounced draft validation must have request
generation fences so an older response cannot overwrite a newer edit.

## 11. Implementation sequence and verification gates

The implementation seams are:

| Area | Existing boundary to extend or new responsibility |
| --- | --- |
| Config contracts | Extend `_config_validation.py` and the node-specific validators; introduce a focused registry/reconciliation module if needed, keeping domain validation in its current owner |
| Draft persistence/service | New `_pipeline_recovery_drafts.py` and focused draft DTOs; reuse artifact ownership, project-storage and transaction boundaries |
| Source actions | Extend `_pipeline_repair_actions.py` and `_pipeline_repair.py`; use `_python_syntax.py`, `_code_extraction.py` and current codegen rather than new source emitters |
| Structural scopes | Extend `_submodel_recovery.py`, `_parser_submodels.py` and the existing identity/reference utilities; preserve the canonical submodel models |
| HTTP | A focused recovery-draft route module registered with the existing API, sharing the same mutation lock/auth/error contracts as `routes/pipeline.py` |
| Frontend | A dedicated recovery-draft store/controller and editor adapter; integrate `NodePanel`, `PipelineRepairDialog`, `useDocumentStatusStore`, recovery card rendering and submodel navigation |
| Transport generation | Extend the generated browser contract workflow for the new DTOs and per-type draft field metadata; preserve strict parsing of the existing removal responses |

New module names are proposed ownership boundaries, not a request for an unrelated
refactor. Keep the current node types, project layout and normal save/execution flows.

1. **Specify the contracts and default inventory.** Update server API, expression
   parsing, codegen, frontend shared/editor/canvas and submodel specs before code.
   Record every node/branch, cross-field group and reference kind. Resolve stale
   defaults and contradictory spec wording. Gate: all 19 types accounted for, with
   explicit required fields and no unresolved design about source versus draft state.
2. **Build the pure configuration recovery engine.** Extend the existing validation
   registry, expose structured diagnostics and implement deterministic preservation.
   Keep node-specific constraints in their existing modules. Gate: focused fixtures
   for ordinary/nested/conditional/dynamic-key settings prove outcomes and idempotence;
   recovering a valid current config does not alter its authored values or meaning.
3. **Add durable drafts and typed transport.** Implement draft revision checks, storage,
   original-byte records and lifecycle APIs. Adapt two representative editors first
   (Polars and Data Input) to prove custom code and required blanks can be edited and
   resumed. Gate: draft edits never alter source or enter execution payloads.
4. **Integrate source generation and transactional Apply/Restore.** Reuse LibCST,
   code extractors, revision manifests and write/rollback services. Add journal and
   retry handling, impact validation and cache invalidation. Gate: preview is read-only;
   apply/reload/restore conserve exact approved scope; injected failures preserve data.
5. **Complete every ordinary type and branch.** Wire the remaining editors through the
   same draft adapter; add the node-specific cases in section 6. Gate: no registered
   executable type uses a generic “drop unknown keys and hope” fallback; incomplete
   defaults remain editable and completion errors are accurate.
6. **Integrate structural/shared recovery.** Add submodel registration adapters,
   definition/child/boundary scopes, owner navigation, reverse ownership checks and
   explicit binding decisions. Support the minimum atomic group needed for coupled
   interface/consumer changes. Gate: shared definitions survive multi-occurrence
   round trips and no public interface or child content disappears silently.
7. **Verify the complete user flow and document the limits.** Cover the real demo in an
   isolated fixture, all acceptance families and resumable UI flows. Keep full reset
   separate and update legacy action labels/contracts without breaking existing
   removal callers. Gate: every supported recovery ends in editable missing fields,
   a reviewable valid proposal, or a precise manual-action reason—never data loss or
   a false runnable state.

The stages are dependencies, not separate claims that general recovery is complete.
Do not advertise all-node support before phases 5 and 6 pass. Generic schema recovery
does not remove the need for explicit migration adapters where semantics changed.

Use the repository's red/green workflow. Extend `tests/test_pipeline_repair_actions.py`
and `tests/test_pipeline_recovery.py`, add focused pure-engine/draft-storage modules,
and extend the existing API contract, node-specific validator, codegen and submodel
tests. Frontend coverage should extend `PipelineRepairDialog`, `NodePanel`,
`useTracing`, the editor tests and a dedicated draft-store/controller module.

Test independent properties rather than copying the implementation: preservation of
valid values and source bytes, no execution during recovery, deterministic proposals,
idempotence, conflict rejection, complete change accounting and post-apply graph
conservation. Add one successful partial recovery per type and focused cases for each
distinct provider/mode/group; avoid an exhaustive Cartesian product of all fields.

During implementation, run the new failing test, then its affected module and touched
static checks. Run the affected cross-stack/browser tests for this feature. Use CI for
the full compatibility, coverage, mutation, performance and browser gates rather than
recreating the whole suite locally.

## 12. Demo-specific final acceptance

Use a fixture copied from `haute-demo`, keeping the user's original files unchanged:

- Both Inputs and Polars_3 remain visible with the original positions and connection.
- Recovering Inputs recognises the legacy registration/port format and preserves its
  child functions, config, definition identity and parent binding.
- Recovering Polars_3 uses the current connected input identity. Its old
  `df = live_switch` code remains available, but is not silently treated as valid for
  the new `Inputs` signature. The user can correct it or explicitly reset the code.
- A config-backed child with one invalid setting retains its other settings; a newly
  required setting stays blank in an editable draft across reloads.
- Applying a completed proposal reopens the authoritative node/editor. An incomplete
  template or unresolved child/consumer never gains an executable status merely
  because its Python can be parsed.
- Restoring a completed recovery returns the recorded source/config/position bytes
  when no later edits conflict. Targeted execution of the configured fixture is a
  separate final check; recovery itself performs no pipeline execution.
