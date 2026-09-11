# Node recovery roadmap

## Scope

Recovery of authored nodes and submodels that no longer load after a Haute
upgrade. Owns the pure configuration-recovery engine, the document repair
actions (remove, update, reset, recover), incomplete-node loading and scoped
editing contracts, and the recovery surface on broken nodes. Current
behaviour is specified in
[node recovery actions](../server-api/node-recovery-actions.md) and the
[generalised node recovery plan](../server-api/generalised-node-recovery-plan.md);
`REC-R02` retires the latter.

Direction, agreed 11 September 2026 after the post-merge review of PR #209:
the persistent-draft apparatus inverts the feature's goal. Every recovery
issue defaults to error severity, any error blocks Apply, and the dialog's
embedded editor has no graph context, so the common post-upgrade case — a
node missing a newly required field — cannot be applied at all. Recovery
must instead write a loadable partial configuration in one action and let
the user finish in the normal editor, exactly as palette-created nodes
start incomplete. Review, approval, and durable undo belong to git and
pull requests, not to the application. The recovery engine and the existing
repair transaction are kept; the draft lifecycle, its storage, journals,
restore records, and its dialog are removed. Persistent drafts, grouped
multi-node recovery, and interactive submodel port/relink editing are
non-goals; cases the engine cannot recover automatically remain manual
source edits with clear diagnostics. A loadable node can still need
configuration or be blocked from execution by another node. Its editability
must be determined independently of both configuration completeness and the
document-wide mutation/save permissions.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| REC-R01 | Planned | P2 | Loadable partial configs, defaults for the current configuration branch, and safe per-node saves in degraded documents. |
| REC-R02 | Planned | P2 | Direct recovery and completion in the normal editor; the persistent-draft apparatus is deleted. |

## Planned improvements

### REC-R01 — Direct recover repair action

**Why:** Recovering a broken node must yield an editable node, not a perfect
one. The draft flow refuses to write until every issue is resolved inside a
modal editor that lacks upstream schema, so partial recovery — the feature's
purpose — is unreachable.

**Plan:** Add `recover` to the document repair actions beside remove, update,
and reset, reusing the existing dry-run, plan-hash, and staged-write
transaction. This requires changes to the loading, recovery, and save
contracts as well as adding the action:

1. **Separate loadability from completeness.** Extend the authoritative
   configuration contracts with explicit representations for missing or
   unpopulated required fields. Parsing, code generation, and save/reload
   must accept these declared incomplete forms and preserve them without
   invented values. Report field-level completeness diagnostics separately
   from load failures, so they do not make an otherwise loadable node
   unavailable or its document degraded. Malformed configured values,
   unknown branches, and ambiguous source still fail clearly. Execution and
   deployment continue to validate completeness before executing affected
   nodes. Recovery and loading must not execute user code or probe external
   data/services to establish completeness. Audit palette defaults against
   this contract: Data Input and Data Output currently reject their own
   empty-path defaults, so the palette is not evidence of loadability.
2. **Respect configuration branches and preserve valid absence.** Audit
   the engine against each supported type's provider, format, mode, and
   other declared discriminants and coupled validation groups. Retain valid
   authored fields and valid omissions; an absent optional field is not an
   instruction to insert a palette value. Use a default only when the
   current branch's contract declares it safe and valid. In particular, a
   JSON file input with omitted mode must remain unchanged; it must never
   acquire Parquet's `scan` default. Missing required values without a safe
   default use the declared incomplete form. Unknown branches or coupled
   failures without an unambiguous repair remain manual actions with
   diagnostics; never switch provider/format or discard valid siblings to
   make a palette fallback pass. Audit and correct the retained engine as
   part of this package.
3. **Generate and verify the direct repair.** Retain authored code bytes
   for declared code slots and regenerate only recognised scaffolding.
   Validate the temporary copy against the loadability contract and verify
   target editability and conservation of unrelated source, node identities,
   and connections. A target may still need configuration or be blocked by
   an unresolved upstream node. Return the field-outcome report (retained,
   defaulted, needs-input, removed), completeness diagnostics, and previous
   configuration in both dry-run and apply responses. Keep source revisions,
   exact plan hashes, and rollback for failed verification.
4. **Support per-node editing while the document remains degraded.** Expose
   server-derived edit/save eligibility for a known, loadable node with a
   trustworthy identity, source span, and exclusively owned edited artifacts.
   Eligibility is separate from document `can_mutate`/`can_save` and includes
   structurally loadable nodes blocked by upstream failures. Keep whole-graph
   mutation and save disabled while unresolved source remains. Provide a
   node-scoped settings/code save through the existing source-edit and
   staged-write transaction abstractions: accept the target identity, source
   revision, and proposed settings/code; resolve spans and affected files on
   the server; and revalidate under the shared mutation/file lock. Validate
   the candidate as loadable, conserve unrelated artifacts and graph
   structure, reject stale revisions, and return the authoritative document.
   Use ordinary transient unsaved editor state and its normal save flow;
   create no persistent draft and require no recovery review dialog for each
   edit. Shared/ambiguous artifacts or edits requiring unresolved structural
   bindings fail with actionable diagnostics instead of widening the write
   scope. `source_only` documents remain ineligible.

Update the owning server-api, pipeline-config, expression-parsing, and
codegen specifications before changing code. Define the node-scoped save
request/response and eligibility contract there; the existing whole-graph
save request must not become a way to overwrite a degraded document.

**Acceptance:**

- Recovering a broken node with missing required fields succeeds in one
  apply and produces a loadable node with field-level completeness
  diagnostics. Data Input and Data Output with blank required paths survive
  save/reload without becoming unavailable; execution/deployment reject
  those missing settings until completed. Invalid populated values still
  fail their contracts.
- Valid authored settings and code survive byte-for-byte. Reconciliation
  of a currently valid configuration is an identity operation, including
  valid omissions. Cover the JSON input with no mode and representative
  non-palette providers/formats, plus one coupled invalid group with valid
  siblings. Defaults never come from an incompatible branch.
- With two broken nodes, recover one, save edits to it, and reload while
  the other remains unavailable. The second node's authored source span,
  configuration bytes, and all unrelated connections remain unchanged;
  whole-graph mutation/save stay disabled. Also cover editing a loadable
  node blocked by an upstream
  failure, and rejection of shared/ambiguous write ownership.
- Stale revisions reject both repair and node-scoped saves; stale plan
  hashes reject repair Apply. Failed staged writes or verification restore
  original bytes. No draft record is created.

**Dependencies:** The authoritative per-type configuration validators,
parser/codegen/save loadability checks, configuration-recovery engine,
document capability model, and existing staged-write repair transaction
and shared mutation/file lock. These contract changes are part of REC-R01.

**Evidence:** `src/haute/_pipeline_repair_actions.py`;
`src/haute/_node_config_recovery.py`; `src/haute/_recovery_sources.py`;
`src/haute/_pipeline_repair.py`; `src/haute/_config_validation.py`;
`src/haute/_config_builder.py`; `src/haute/_polars_io_registry.py`;
`src/haute/node_defaults.json`; `src/haute/_pipeline_recovery.py`;
`src/haute/routes/_save_pipeline.py`; `src/haute/routes/pipeline.py`;
`tests/test_pipeline_repair_actions.py`; `tests/test_node_config_recovery.py`;
`tests/test_pipeline_recovery.py`; `tests/test_config_validation.py`;
`tests/test_parser_roundtrip.py`.

### REC-R02 — Minimal recovery UI and draft-apparatus removal

**Why:** One recovery currently costs four round-trips and a review
attestation inside a modal with six footer buttons, a grouped-draft
inventory of every node, a history dropdown, and an embedded editor without
graph context. Drafts go stale whenever any file in the installed package
changes, and every record stores base64 snapshots of all authored artifacts
— a private version-control system duplicating git.

**Plan:** Route the broken-node **Recover settings** button through the
existing single-confirm repair dialog (source diff collapsed by default) to
the `recover` action. After apply, adopt the authoritative document and keep
the recovered node selected with its normal panel open, restoring its
containing submodel view where necessary. Show a dismissible summary in the
node panel — retained, defaulted, needs-input, and removed counts, with
expanders for the field details, the previous configuration, and the source
diff. Keep this summary in transient session state through document adoption
and panel reselection; it is not a persisted recovery record.

Wire the normal editor and save flow to REC-R01's per-node eligibility and
scoped save contract when the document remains degraded. Highlight the
server's completeness diagnostics on the corresponding fields. Loadable
nodes blocked by upstream failures keep their editor and show those
execution blockers separately. Preserve the document-wide fences on graph
changes and whole-graph saves; do not unlock the entire canvas merely to
edit one node. After a scoped save, reload the authoritative state while
retaining the target selection and reporting any remaining missing values.

Remove the permanent Recover button from healthy-node headers. Submodels
keep their existing update and remove actions; reset remains restricted to
supported ordinary nodes and does not reset a submodel or its child graph.
Unsupported submodel recovery continues to require manual source edits.

Delete the recovery-draft dialog, drafts API client and types, generated
draft contracts, the draft service, draft storage, draft routes and
draft-specific schemas, and startup journal reconciliation. Existing
`.haute/recovery/` contents are ignored without migration. Keep the project
mutation lock and file lock, which ordinary saves and json-shred publication
now share, and move any shared helpers out of the deleted storage module.
Retire the generalised recovery plan document and fold the surviving
configuration, field-outcome, and repair contracts into
[node recovery actions](../server-api/node-recovery-actions.md) and the
frontend component specifications before changing code.

**Acceptance:** A broken node is recovered from the canvas in at most two
clicks (action, confirm); an incomplete recovered node is immediately
editable in the normal panel, including while another node remains
unavailable; editing, saving, and reloading that node use the scoped save
path without enabling whole-graph saves; the previous configuration and
diff remain viewable on demand during the session after apply; submodels
offer update/remove and never reset; no draft route, storage namespace,
state machine, or dialog remains in the codebase. Replace draft-only tests
with targeted coverage of direct recovery, field diagnostics, scoped saves,
selection retention (including submodel children), and stale-save failures;
retain transaction and lock regression coverage for the surviving shared
infrastructure. Backend and frontend suites pass.

**Dependencies:** REC-R01.

**Evidence:** `frontend/src/components/RecoveryDraftDialog.tsx`;
`frontend/src/components/PipelineRepairDialog.tsx`;
`frontend/src/panels/NodePanel.tsx`; `frontend/src/api/recoveryDrafts.ts`;
`frontend/src/App.tsx`; `frontend/src/hooks/usePipelineAPI.ts`;
`frontend/src/stores/useDocumentStatusStore.ts`;
`src/haute/_pipeline_recovery_drafts.py`; `src/haute/_recovery_storage.py`;
`src/haute/routes/recovery.py`; `src/haute/server.py`;
`tests/test_recovery_drafts.py`; `tests/test_recovery_draft_routes.py`;
`tests/test_recovery_transactions.py`;
`frontend/src/components/__tests__/pipelineRecoverySurfaces.test.tsx`;
`frontend/src/panels/__tests__/NodePanel.test.tsx`;
`frontend/src/hooks/__tests__/usePipelineAPI.test.ts`.
