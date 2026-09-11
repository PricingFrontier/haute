# Explicit node recovery actions

## Recovery loading and retention

Recovery loading remains read-only. Rejected literal submodel registrations retain an
unavailable submodel card, their authored connections, source span, and saved position.
Their literal child reference and its artifacts participate in the recovery revision even
when the current strict parser rejects the old registration. Ambiguous identities are
diagnosed and cannot be repaired automatically. Downstream nodes remain blocked.

## Loadable incomplete configurations

A loadable configuration is not necessarily a complete one. Data Input and Data Output
accept declared-incomplete required locators (the empty string, equivalent to absence)
at parse, load, and save; the strict io-layer contract still rejects them for execution,
preview, deploy, and caching. The editor document reports these gaps per node in a
bounded document-level `completeness` list (node recovery id, field path, code, safe
message), computed by the document loader from the same authoritative validators.
Completeness entries never mark a node unavailable, never degrade the document, and
never appear in `diagnostics`. Palette-created Data Input/Output nodes therefore save
and reload before a path is chosen, like every other palette default.

## Confirmed recovery actions

The recovery inspector extends removal with three explicitly confirmed actions:

- **Update to current format** supports the recognised legacy submodel registration
  (`definition_id`, `instance_id`, `alias`, `label`) and public port (`portId`, `label`)
  formats. It emits the current file/name registration and retains each old port id as
  its canonical name so existing connections keep their identity. The child's definition
  id must agree with the old registration. Child functions, config files, connection
  statements and custom consumer code are preserved. The parent position key moves from
  the retired instance id to the occurrence name. Shared child definitions are reported
  in the preview. Unsupported or conflicting formats fail explicitly.
- **Reset node** supports known ordinary node types with an unambiguous function span
  and resolvable incoming connections. It uses the same default configuration as adding
  that type from the palette and the current single-node code generator. It preserves
  identity, description, position and authored connections, but replaces that node's
  settings and function body. Existing config references are preserved and their content
  replaced only when exclusively owned by this node. Unknown types, node instances,
  submodels and ambiguous/shared artifacts cannot be reset. Resetting an empty Polars
  node produces the normal explicit incomplete-code template; it never invents a
  passthrough. The preview explains that configuration/code may be needed before running.
  Palette defaults with declared-incomplete required values (for example a file input's
  empty path) persist as loadable incomplete configurations reported through the
  document's completeness list; reset is never blocked by completeness, only by unknown
  types, ambiguous spans, or shared artifacts.
- **Recover settings** supports known ordinary node types with an unambiguous function
  span and resolvable incoming connections. It rebuilds the target's configuration with
  the pure recovery engine: authored fields that are valid under the current contract
  are retained exactly; an invalid present value is replaced with the matching branch's
  audited default only where that default is structurally valid; absent fields stay
  absent — recovery never inserts a palette value for a field the author omitted and
  never fills a default from a different provider/format/mode branch. Unrecoverable
  collection entries are excluded from the candidate and reported with their original
  value, never emitted as null placeholders. Missing required values use the
  declared-incomplete form and surface as completeness, not as a blocked plan. Authored
  code bytes in declared code slots are retained; only recognised generated scaffolding
  is regenerated. The applied node must load (available, or blocked only by an upstream
  failure) — completeness and execution-readiness are explicitly not plan gates.
  Identity, description, position, connections, and exclusively owned config references
  follow the Reset rules. Unknown types, node instances, submodels, and ambiguous or
  shared artifacts cannot be recovered by this action; submodels keep Update to current
  format.

## Consumer error attribution

Updating a submodel may reveal a separately invalid consumer signature. Attribute that
failure to the consumer and make its recovery inspector available. Do not rewrite or
erase custom consumer code as part of a submodel update.

## Recovery dry-run and apply API

`POST /api/pipeline/repair/recover/dry-run` accepts source file/revision, target source
file/recovery id, and `action: update | reset | recover`. Apply uses the same fields plus
the displayed plan hash. Responses use the existing repair change/plan shape with
`repair_kind: update_node | reset_node | recover_node` and `delete_config: false`.
Recover responses additionally carry the engine's field-outcome report (`field_changes`:
path, `outcome: retained | defaulted | needs_input | removed`, reason), the target's
completeness entries, and the exact previous configuration (`previous_config`) for
optional display; dry-run and apply return the same shapes. Existing removal routes
retain their contract. No request accepts replacement bytes or source spans.

## Engine coverage and limits

Configuration recovery preserves valid fields across all 17 ordinary node
types. Submodels use explicit registration/port adapters through Update to
current format and retain definition/public-port identity, children and
connections. Projected ports belong to their definition and require recovery
there. Unknown types, computed or custom source, and ambiguous shared
instances remain explicit manual actions. Completeness checks are static:
successful recovery does not certify runtime data, services or arbitrary user
code. Field-shape reconciliation precedes semantic validation, and malformed
discriminator values (including JSON arrays and objects) produce structured
outcomes, never an unhandled exception or an inferred default branch.

## Node-scoped save in degraded documents

Whole-document mutation and save remain fenced by `PipelineDocumentCapabilities` while
any source is unresolved. Editing one loadable node must not depend on that fence. Each
document node carries server-derived `scoped_editable`: true only for a known, loadable
node (available, or blocked only by an upstream failure) with a trustworthy identity and
source span whose edited artifacts are exclusively owned by that node. `source_only`
documents and nodes inside read-only submodel copies are never eligible.

`POST /api/pipeline/node/save` accepts the document source file and revision, the target
source file and recovery id, and the proposed node configuration (including declared
code slots). The server resolves spans and affected files itself, revalidates
eligibility under the shared save lock, requires the candidate to remain loadable
(completeness gaps allowed), rewrites only that node's settings and code through the
existing LibCST and staged-write boundaries, verifies conservation of every other node,
edge, and artifact byte, rejects stale revisions with HTTP 409, and returns the
authoritative document. Shared or ambiguous artifacts, and edits that would change
unresolved structural bindings, fail with actionable diagnostics instead of widening the
write scope. No persistent draft is created anywhere in this flow.

## Verification, rollback, and save-lock transactions

Plans are computed on the server, bound to the entire raw-artifact revision and exact
before/after bytes, and previewed without changing project files. Application recomputes
the plan under the shared save lock, rejects stale revisions/hashes/artifacts, stages all
edits using the existing rollback boundary, and reloads the authoritative document.
Verification must conserve all node/edge identities except the explicit old-to-current
registration identity mapping; the target must cease being unavailable. Other invalid
nodes may keep the document degraded. Any failed verification restores original bytes.

## Acceptance evidence

Acceptance evidence includes a minimal copy of the demo's legacy registration, output
port and stale Polars signature: both nodes and the connection remain visible; update
preserves the child/config and exposes the consumer error; resetting that consumer uses
its current connected input and returns a ready but deliberately incomplete Polars node.
Also cover unchanged dry-run bytes, stale child/config revisions and plan hashes,
shared/path-escaping artifacts, duplicate identities, rollback, strict transport parsing,
and the UI's action-specific preview/apply and failure behaviour.
