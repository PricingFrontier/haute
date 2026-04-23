/**
 * `useGraph()` hook + the context it consumes.
 *
 * The provider component lives in `GraphContext.tsx` so that `.tsx` file only
 * exports a component (react-refresh/only-export-components).  Because this
 * file is `.ts` (no component export), it's free to export the raw context
 * and the hook together — they're inseparable in practice.
 */

import { createContext, useContext } from "react"
import type { SimpleNode, SimpleEdge } from "./editors"

/** Shape exposed by `useGraph()`. */
export type GraphContextValue = {
  allNodes: SimpleNode[]
  edges: SimpleEdge[]
  submodels?: Record<string, unknown>
  preamble?: string
}

// `undefined` distinguishes "no provider" from "provider with empty graph".
// Consumers outside a provider get a loud error from useGraph() instead of a
// silent empty graph.
export const GraphContext = createContext<GraphContextValue | undefined>(undefined)

/**
 * Reads graph data from the nearest `GraphProvider`.
 *
 * Throws if called outside a provider so mis-mounted consumers surface in
 * the ErrorBoundary rather than rendering against a silent empty graph
 * (which would hide broken references and stale column sets).
 */
export function useGraph(): GraphContextValue {
  const ctx = useContext(GraphContext)
  if (ctx === undefined) {
    throw new Error(
      "useGraph() was called outside of a <GraphProvider>. " +
        "Wrap NodePanel (or its ancestor) in <GraphProvider allNodes={...} edges={...}>.",
    )
  }
  return ctx
}
