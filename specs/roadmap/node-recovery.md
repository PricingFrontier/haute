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
| REC-R02 | Planned | P2 | Direct recovery and completion in the normal editor; the persistent-draft apparatus is deleted. |

`REC-R01` (loadable partial configs, branch-respecting engine recovery, the
direct `recover` action, and node-scoped saves in degraded documents) was
delivered on 11 September 2026 and is now covered by the
[node recovery actions](../server-api/node-recovery-actions.md) specification
and its regression tests (`test_incomplete_config_loadability.py`,
`test_node_config_recovery.py`, `test_pipeline_repair_actions.py`,
`test_node_scoped_save.py`).

## Planned improvements

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

**Dependencies:** The delivered `REC-R01` contracts — the `recover` action,
the document completeness channel, and the node-scoped save.

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
