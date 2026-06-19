/**
 * Explodability registry (node-explosion design §3.2).
 *
 * Maps a node type to a {@link PeekDescriptor}: a quick sync test for whether
 * a node has internals worth peeking at, plus the body component that renders
 * those internals inside the peek window. New internal-structure node types
 * (banding → fast-follow F1; polars-notebook → later) plug in here with one
 * registry entry and one Body component — nothing else changes.
 *
 * v1 ships the submodel entry only. `edgeJoin` (the join-node marker) never
 * gets an entry: it has no internal structure to peek at.
 */
import type { Node, Edge } from "@xyflow/react"
import { NODE_TYPES, type NodeTypeValue } from "../utils/nodeTypes"
import { nodeData } from "../types/node"
import SubmodelPeekBody from "./SubmodelPeekBody"

/** Props every peek body receives. */
export interface PeekBodyProps {
  /** The node being peeked at (on the current canvas). */
  node: Node
  /** Accent colour for the node type, for body theming. */
  accent: string
  /**
   * Navigate into the node's internals (submodel drill-in), optionally
   * selecting a specific child once there. Header "Open" calls with no arg;
   * a mini-node click calls with that child's id.
   */
  onDrillIn?: (selectChildId?: string) => void
  /**
   * The parent (current) canvas's nodes — used by the submodel body to resolve
   * the labels of its derived I/O ports. Optional: bodies without a boundary
   * (or peeks rendered without graph context) simply omit it.
   */
  parentNodes?: Node[]
  /**
   * The parent (current) canvas's edges — the submodel body derives its I/O
   * boundary (ports + dashed links) from the edges to/from the peeked node.
   */
  parentEdges?: Edge[]
  /**
   * Reported once after the body lays its internals out: the panel size (px)
   * that frames the whole graph at a balanced zoom. The peek window opens at
   * this size (clamped) unless the user has already resized it.
   */
  onPreferredSize?: (size: { width: number; height: number }) => void
}

export interface PeekDescriptor {
  /** Quick sync test: does this node have internals worth peeking at? */
  isExplodable: (node: Node) => boolean
  /** Renders the peek body. May fetch (submodel). */
  Body: React.ComponentType<PeekBodyProps>
}

export const PEEK_REGISTRY: Partial<Record<NodeTypeValue, PeekDescriptor>> = {
  [NODE_TYPES.SUBMODEL]: {
    // A submodel is always explodable: its internals are determined when the
    // pipeline was built. Zero children render an explicit empty state in the
    // body rather than hiding the affordance (suppression is not fine).
    isExplodable: () => true,
    Body: SubmodelPeekBody,
  },
  // [NODE_TYPES.BANDING] lands in fast-follow F1 (design §3.8): one entry +
  // one BandingPeekBody, nothing else.
}

/** Resolve the peek descriptor for a node, or undefined if not explodable. */
export function getPeekDescriptor(node: Node): PeekDescriptor | undefined {
  const nodeType = nodeData(node).nodeType
  if (!nodeType) return undefined
  const descriptor = PEEK_REGISTRY[nodeType as NodeTypeValue]
  if (!descriptor) return undefined
  return descriptor.isExplodable(node) ? descriptor : undefined
}

/** Does this node have a peek body to show? */
export function isNodeExplodable(node: Node): boolean {
  return getPeekDescriptor(node) !== undefined
}
