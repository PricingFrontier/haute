/**
 * GraphContext — provides graph data (allNodes, edges, submodels, preamble)
 * to NodePanel and all of its descendants without prop drilling.
 *
 * Before this context: `allNodes`, `edges`, `submodels`, `preamble` were
 * threaded through NodePanel → InstancePanel / SinkEditor / OutputEditor /
 * RatingStepEditor / ModellingConfig / OptimiserConfig as explicit props.
 * That cost ~15 prop declarations and made every new consumer a rewrite
 * of the entire chain.  With `GraphProvider` at the app boundary, any
 * descendant calls `useGraph()` to read the current graph directly.
 *
 * Module layout (split to satisfy react-refresh):
 *   - `graphContextInternal.ts` — the raw `React.Context` + value type.
 *   - `useGraph.ts`             — the consumer hook.
 *   - `GraphContext.tsx` (this) — the `<GraphProvider>` component only.
 *
 * The provider's value is memoised on its input props so consumers only
 * re-render when graph data actually changes, not on every parent render.
 */

import { useMemo, type ReactNode } from "react"
import { GraphContext, type GraphContextValue } from "./graphContextInternal"

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
