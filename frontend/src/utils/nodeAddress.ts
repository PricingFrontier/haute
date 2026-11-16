/**
 * How the editor names a node and a frame of the document: by address, not by an id
 * built for a request. A node address is one of the pipeline's own nodes (`occurrence`
 * null; an occurrence of a submodel is itself one) or a child of an occurrence; a frame
 * target is a node's output port, or a declared input or output port of an occurrence.
 */

/** A node of the document. */
export interface NodeAddress {
  /** The occurrence whose child this is, by instance id; null for the pipeline's own nodes. */
  readonly occurrence: string | null
  /** The node's id: in the pipeline, or in the occurrence's definition. */
  readonly node: string
}

/** A frame of the document a request reads. */
export type FrameTarget =
  /** A node's output at `port`, or its one output when `port` is null. */
  | { readonly kind: "node"; readonly address: NodeAddress; readonly port: string | null }
  /** What feeds an occurrence's declared input `port`. */
  | { readonly kind: "occurrenceInput"; readonly occurrence: string; readonly port: string }
  /** An occurrence's declared output `port`. */
  | { readonly kind: "occurrenceOutput"; readonly occurrence: string; readonly port: string }

/** One of the pipeline's own nodes. */
export const rootAddress = (node: string): NodeAddress => ({ occurrence: null, node })

/** A child of occurrence `occurrence`. */
export const childAddress = (occurrence: string, node: string): NodeAddress => ({ occurrence, node })

/** A node's output frame at `port` (its one output when null). */
export const nodeFrame = (address: NodeAddress, port: string | null = null): FrameTarget =>
  ({ kind: "node", address, port })

/** No node id holds it, so a child's key never equals a root node's. */
const CHILD_KEY_PREFIX = "\u0000"

/**
 * An address as one string, for keying results and caches: a root node's id as it is,
 * so stores keyed by the pipeline's node ids keep their keys, and a child's key opening
 * with a character no node id holds. Throws for a root node id that holds it.
 */
export function addressKey(address: NodeAddress): string {
  if (address.occurrence === null) {
    if (address.node.startsWith(CHILD_KEY_PREFIX)) {
      throw new Error(`Node id ${JSON.stringify(address.node)} cannot be keyed: it opens with a control character`)
    }
    return address.node
  }
  return `${CHILD_KEY_PREFIX}${JSON.stringify([address.occurrence, address.node])}`
}

/** Whether two addresses name the same node. */
export const sameAddress = (a: NodeAddress, b: NodeAddress): boolean =>
  a.occurrence === b.occurrence && a.node === b.node
