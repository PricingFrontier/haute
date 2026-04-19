/**
 * `<GraphProvider>` — provides graph data (allNodes, edges, submodels,
 * preamble) to NodePanel and its descendants without prop drilling.
 *
 * The raw `React.Context`, its value type, and the `useGraph()` consumer
 * hook all live in `useGraph.ts` so that this `.tsx` file exports only a
 * component (react-refresh/only-export-components).  Value is memoised on
 * its inputs so unrelated parent re-renders don't cascade into consumers.
 */

import { useMemo, type ReactNode } from "react"
import { GraphContext, type GraphContextValue } from "./useGraph"

/**
 * Wraps a subtree so `useGraph()` resolves to the given graph.
 *
 * Value is memoised by input identity so unrelated parent re-renders do
 * not cascade into every descendant consumer.
 */
export function GraphProvider({
  allNodes,
  edges,
  submodels,
  preamble,
  children,
}: GraphContextValue & { children: ReactNode }) {
  const value = useMemo<GraphContextValue>(
    () => ({ allNodes, edges, submodels, preamble }),
    [allNodes, edges, submodels, preamble],
  )
  return <GraphContext.Provider value={value}>{children}</GraphContext.Provider>
}
