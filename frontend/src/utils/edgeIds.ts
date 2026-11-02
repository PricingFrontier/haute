/**
 * Edge ids: an injective encoding of an edge's four-field identity, minted
 * exactly as the server mints it (`haute._graph_utils._edge_id`) and held to
 * the shared fixture `tests/fixtures/ui_contracts/edge_ids.json`. Free of
 * imports, so the end-to-end specs can use it too.
 */

/** An edge's identity: its endpoints and, when present, their handles. */
export interface EdgeIdentity {
  source: string
  target: string
  sourceHandle?: string | null
  targetHandle?: string | null
}

function edgeIdField(value: string): string {
  if (value === "") throw new Error("An edge id cannot be minted from an empty endpoint or handle")
  return value.replaceAll("~", "~0").replaceAll("-", "~1")
}

/**
 * The edge's id. A connection keeps it through save and reload, and two
 * distinct connections never share one. Each present field is escaped (`~` as
 * `~0`, `-` as `~1`) and joined with `-` after `e`; absent trailing handles are
 * left off, and an absent source handle before a target handle is empty.
 */
export function edgeId({
  source,
  target,
  sourceHandle = null,
  targetHandle = null,
}: EdgeIdentity): string {
  const fields = [edgeIdField(source), edgeIdField(target)]
  if (targetHandle !== null) {
    fields.push(sourceHandle === null ? "" : edgeIdField(sourceHandle), edgeIdField(targetHandle))
  } else if (sourceHandle !== null) {
    fields.push(edgeIdField(sourceHandle))
  }
  return ["e", ...fields].join("-")
}

/**
 * *edge* with *change* applied to its identity and its id minted again from
 * the result. Every edit that moves an existing edge to another endpoint or
 * handle goes through it, so an id always names its edge's current identity
 * and a later connection under the edge's old identity cannot take its id.
 */
export function withEdgeIdentity<E extends EdgeIdentity & { id: string }>(
  edge: E,
  change: Partial<EdgeIdentity>,
): E {
  const next = { ...edge, ...change }
  return { ...next, id: edgeId(next) }
}
