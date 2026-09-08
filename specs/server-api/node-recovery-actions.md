# Explicit node recovery actions

## Recovery loading and retention

Recovery loading remains read-only. Rejected literal submodel registrations retain an
unavailable submodel card, their authored connections, source span, and saved position.
Their literal child reference and its artifacts participate in the recovery revision even
when the current strict parser rejects the old registration. Ambiguous identities are
diagnosed and cannot be repaired automatically. Downstream nodes remain blocked.

## Confirmed recovery actions

The recovery inspector extends removal with two explicitly confirmed actions:

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
  If a type's palette defaults fail its strict load contract (for example a file input
  requiring a non-empty path), planning reports the required configuration and leaves
  the source untouched. It cannot persist another unavailable default node into the
  read-only recovery document; that node needs its config corrected first.

## Consumer error attribution

Updating a submodel may reveal a separately invalid consumer signature. Attribute that
failure to the consumer and make its recovery inspector available. Do not rewrite or
erase custom consumer code as part of a submodel update.

## Recovery dry-run and apply API

`POST /api/pipeline/repair/recover/dry-run` accepts source file/revision, target source
file/recovery id, and `action: update | reset`. Apply uses the same fields plus the
displayed plan hash. Responses use the existing repair change/plan shape with
`repair_kind: update_node | reset_node` and `delete_config: false`. Existing removal
routes retain their contract. No request accepts replacement bytes or source spans.

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
