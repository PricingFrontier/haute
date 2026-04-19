/**
 * `useGraph()` hook — reads the graph context populated by `<GraphProvider>`.
 *
 * Split from `GraphContext.tsx` so the `.tsx` file exports only the provider
 * component, keeping react-refresh happy (the plugin warns when a `.tsx`
 * file exports both a component and a non-component).  See GraphContext.tsx
 * for the provider and the rationale for the refactor.
 */

import { useContext } from "react"
import { GraphContext, type GraphContextValue } from "./graphContextInternal"

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
