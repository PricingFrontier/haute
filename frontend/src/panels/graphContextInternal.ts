/**
 * Raw React context for graph data, split from `GraphContext.tsx` so the
 * `.tsx` file exports only the provider component.  The `react-refresh`
 * plugin flags any non-component export living alongside a component
 * export in the same file — moving the bare context here silences that
 * rule without changing behaviour.
 *
 * Consumers should prefer the `useGraph()` hook (see `useGraph.ts`).  The
 * context and its value type are exported here for the hook's benefit
 * and for TypeScript consumers of the hook's return type.
 */

import { createContext } from "react"
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
