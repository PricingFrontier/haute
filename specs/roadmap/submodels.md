# Submodels roadmap

## Scope

Submodel definitions and occurrences, and the other mechanisms for reusing
logic in a pipeline. Current behaviour is specified in
[the submodels specification](../submodels/high-level.md). These packages
come from the [23 September 2026 codebase review](codebase-review-2026-09-23.md)
and the config-lane round that settled the canonical-input rule.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| SUB-R02 | Planned | P3 | A legacy submodel registration is rejected with a targeted message instead of migrated. |
| SUB-R01 | Planned | P3 | One mechanism reuses a node or group of nodes. |

## Planned improvements

`SUB-R02` is small and independent. `SUB-R01` is best taken before
`PCFG-R08` and `PCFG-R07` (pipeline config), so the editor-state move and the
typed config models never have to carry node-level instances.

### SUB-R02 — Reject legacy submodel registrations
**Why:** The recovery inspector's **Update to current format** action rewrites
the legacy submodel registration (`definition_id`, `instance_id`, `alias`,
`label`) and public port (`portId`, `label`) formats into the current
file/name registration. That is a migration, and the canonical-input rule
decided for `PCFG-R06` on 24 September 2026 allows none: a removed form gets a
targeted "X was removed; use Y" rejection, with no migration, silent drop or
passthrough. The config-lane round kept the action only because neither
recovery decision named it.

**Plan:** Replace the action with a targeted rejection: loading a legacy
registration reports which keys were removed and what the current
registration looks like, and the node is unavailable until the author rewrites
it (or removes it through the existing remove-node action). Delete the
update action from the recovery engine, the recovery apply API, the repair
dialog and the node panel, and update the server-api node-recovery-actions
and frontend-node-editors specifications first.

**Acceptance:** A pipeline with a legacy registration loads degraded with a
diagnostic naming the removed keys and the current form; no code path
rewrites a legacy registration; the recovery apply API has no update action;
the repair dialog and node panel offer no "Update to current format".

**Dependencies:** None.

**Evidence:** `specs/server-api/node-recovery-actions.md`;
`specs/frontend-node-editors/high-level.md`;
`frontend/src/components/PipelineRepairDialog.tsx`;
`frontend/src/panels/NodePanel.tsx`;
`src/haute/_pipeline_repair_actions.py::_update_submodel`;
`src/haute/_submodel_recovery.py`.

### SUB-R01 — One reuse mechanism
**Why:** Four mechanisms overlap. Node-level instances (`@pipeline.instance`,
`instanceOf` with `inputMapping`) reuse one node's logic. Submodel
definitions with instance occurrences reuse a group of nodes, with read-only
copies and port minting that suffixes `_2` and `_3` on collision. Plain
Polars transforms carry `inputMapping` to keep logical input names across
rewires. Project `utility` modules reuse functions. Together they account
for 258 `instanceOf`/`instance_of` references across the backend and
frontend. Files named after submodels hold 6,300 source lines and 11,700 test
lines, and 25 of the 48 submodel commits since May 2026 were fixes or
hardening of identity and aliasing.

**Decided (24 September 2026):** the review's recommendation. A node
instance is a one-node submodel occurrence, so node-level instances go as a
separate mechanism. Submodel definitions with their occurrences and project
`utility` modules are the reuse mechanisms. `inputMapping` on a plain Polars
transform stays: it names a node's inputs across rewires and reuses no logic.
The capability users have today stays: **Create Instance** on a Polars or
Model Score node wraps the node in a one-node submodel definition (its own
`modules/<name>.py`) and places a second occurrence, so editing the
definition still updates every copy.

**Plan:** Specify the kept mechanisms, the one-node submodel form of an
instance and the targeted rejection of `@pipeline.instance` and `instanceOf`
(the canonical-input rule) in the submodels and pipeline-config
specifications. First prove that a one-node submodel carries every current
instance use (Polars and Model Score, with remapped inputs) through preview,
trace, training, codegen round-trip and deploy; if one cannot, stop and bring
the gap back as a decision. Then make **Create Instance** produce the
submodel form, remove the node-instance parser, codegen, executor, flattening
and editor paths, and rewrite `docs/building-models/nodes/instances.md` as
"reusing a node" through submodels.

**Acceptance:** The submodels specification names the supported reuse
mechanisms; `@pipeline.instance` and `instanceOf` are rejected with a message
naming the submodel form; no parser, codegen, executor or editor path handles
node-level instances; **Create Instance** yields a working one-node submodel
occurrence for Polars and Model Score nodes; the documentation describes only
supported mechanisms.

**Dependencies:** None. The canonical-input rule that applies to removed forms
is in the [specification README](../README.md#canonical-only-format-policy).

**Evidence:** `src/haute/pipeline.py::instance`;
`src/haute/_submodel_instances.py`; `src/haute/_flatten.py`;
`docs/building-models/nodes/instances.md`;
`docs/building-models/nodes/submodel.md`;
`frontend/src/utils/canonicalSubmodelBoundaryEditing.ts`.
