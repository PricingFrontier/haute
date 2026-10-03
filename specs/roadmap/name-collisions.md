# Name collisions roadmap

## Scope

The names a user chooses that become Python bindings, parameters, columns, files
or storage keys. The naming rule is specified in
[the codegen specification](../codegen/high-level.md) ("One function per node" and
"Names cannot collide with support code"), the document's name violations and the
editor identity request in [the server API specification](../server-api/high-level.md),
and their editor surfaces in
[the graph canvas specification](../frontend-graph-canvas/high-level.md).

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| NAME-09 | Planned | P3 | Editors that set an input binding show its name violations as it is edited. |

## Planned improvements

### NAME-09 — Input-binding editors show their violations inline
**Why:** An input binding that is not a node's own name (an API Input frame label,
an `inputMapping` alias, a submodel input port) can be reserved or collide with a
support-code helper. A reserved frame label is refused inline
(`reserved_api_input_frame_labels`), but every other such violation reaches the user
only when save or a strict parse refuses it, without a link to the editor that made
it.

**Plan:** After an edit commits one of these bindings, the editor sends the editor
identity request with the document's naming context and no candidate nodes, and
shows inline the returned violations of kind `reserved_input` or `support_input`
whose origin is the edited binding. The violation result gains the binding's source
(node id and handle, or mapping key) so the editor can match it without parsing the
message.

**Specification:** the editor identity request's violation shape in `server-api`;
the API Input, instance mapping and submodel port editors in `frontend-node-editors`.

**Acceptance:** Setting an API Input frame label to the name of a preamble helper
shows the support-code collision under that frame's label field; setting an
`inputMapping` alias to `pl` shows the reserved-input violation in the instance
mapping editor; a submodel input port named like a utility helper shows it in the
port editor. Fixing the name clears each.

**Dependencies:** None.

**Evidence:** `src/haute/_executable_names.py::input_binding_violations`;
`src/haute/_support_code_names.py::support_code_violations`;
`frontend/src/utils/apiInputPorts.ts::apiInputLabelIssue`;
`frontend/src/utils/editorIdentities.ts`.
