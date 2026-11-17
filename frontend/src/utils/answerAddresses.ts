/**
 * Reading the nodes an answer names (specs/frontend-shared/low-level.md, "Request gateway"
 * item 5). An answer names nodes by an opaque runtime key and gives each its address in
 * `node_addresses`; the browser never reads a key for a node. This module is the one place a
 * key is read as text (to look it up in the answer it came in), and places an address on the
 * canvas.
 */
import type {
  NodeAddressEntry,
  OccurrencePortProvenance,
  RuntimeNodeKey,
} from "../generated/api-contracts.generated"
import type { FrameTarget } from "./nodeAddress"

/** An answer's addresses, by the text of each key it names. */
export type NodeAddresses = Readonly<Record<string, NodeAddressEntry>>

/** A key as the text the answer's maps and `node_addresses` are keyed by. */
export function keyText(key: RuntimeNodeKey): string {
  return key as unknown as string
}

/** Where an answer's node sits on the canvas, and whether it shows there through its occurrence's card. */
export interface CanvasPlace {
  /** The canvas node: the node itself, or the collapsed occurrence whose child it is. */
  readonly id: string
  /** Whether the node is a child of a collapsed occurrence, shown through its card. */
  readonly viaCard: boolean
}

/** A boundary card of the open occurrence, and the pipeline's nodes its ports reach. */
export interface BoundaryCard {
  readonly id: string
  readonly direction: "input" | "output"
  /** What feeds the occurrence's inputs (the Input card), or reads its outputs (the Output card). */
  readonly externalNodeIds: readonly string[]
}

/**
 * The one placement of an address on the canvas, by the occurrence open on it (null for
 * the main pipeline):
 *
 * | The address | Canvas at the root | Canvas inside occurrence O |
 * |---|---|---|
 * | A pipeline node `{null, n}` (an occurrence's card when `n` is one) | node `n` | the boundary card reaching `n`, else not on the canvas |
 * | A child `{O, c}` of the open occurrence | O's card | node `c` |
 * | A child `{P, c}` of another occurrence | P's card | the boundary card reaching `P`, else not on the canvas |
 *
 * Inside an occurrence, a node outside it shows on a boundary card only for a reader that
 * passes the cards (the trace, whose path runs through them): on the Input card when it
 * feeds the occurrence, else on the Output card when it reads from it.
 */
export function placeAddress(
  address: Pick<NodeAddressEntry, "occurrence" | "node_id">,
  openOccurrence: string | null,
  boundaries: readonly BoundaryCard[] = [],
): CanvasPlace | null {
  if (openOccurrence === null) {
    return address.occurrence === null
      ? { id: address.node_id, viaCard: false }
      : { id: address.occurrence, viaCard: true }
  }
  if (address.occurrence === openOccurrence) return { id: address.node_id, viaCard: false }
  const outside = address.occurrence ?? address.node_id
  const card = boundaries.find((candidate) => candidate.direction === "input" && candidate.externalNodeIds.includes(outside))
    ?? boundaries.find((candidate) => candidate.direction === "output" && candidate.externalNodeIds.includes(outside))
  return card ? { id: card.id, viaCard: true } : null
}

/**
 * The keys of an answer placed directly on a canvas node (not through a card), by canvas id:
 * the key whose maps describe that node.
 */
export function keysOnCanvas(addresses: NodeAddresses, openOccurrence: string | null): Map<string, string> {
  const keys = new Map<string, string>()
  for (const [key, address] of Object.entries(addresses)) {
    const place = placeAddress(address, openOccurrence)
    if (place && !place.viaCard) keys.set(place.id, key)
  }
  return keys
}

/**
 * The key an answer runs `target` under: the node's, found by its address, or the endpoint
 * an occurrence's port resolves to in the answer's frame provenance. Null when the answer
 * names neither (an error answer names no node).
 */
export function answerTargetKey(
  answer: { readonly node_addresses: NodeAddresses; readonly occurrence_ports?: readonly OccurrencePortProvenance[] },
  target: FrameTarget,
): string | null {
  if (target.kind === "node") {
    const { occurrence, node } = target.address
    const found = Object.entries(answer.node_addresses)
      .find(([, address]) => address.occurrence === occurrence && address.node_id === node)
    return found ? found[0] : null
  }
  const direction = target.kind === "occurrenceInput" ? "input" : "output"
  const entry = answer.occurrence_ports?.find((port) =>
    port.occurrence === target.occurrence && port.direction === direction && port.port === target.port)
  return entry?.endpoint ? keyText(entry.endpoint.key) : null
}
