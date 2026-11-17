import { create } from "zustand"

/**
 * What a node's Stop button covers beyond its own preview request: work a
 * consumer of the node started — a shared data-point build, an Import — so the
 * frame can show Stop while it runs and stop all of it.
 *
 * Only work this browser tab started is registered, by every consumer of the
 * node that reads it. A build joined from another tab or client is theirs to
 * stop; this node only stops waiting.
 *
 * Work is kept under the node's result key (`resultKey`): inside a submodel,
 * its occurrence's, so Stop in one occurrence leaves another's work running.
 */
interface NodeWorkState {
  /** Running work, keyed by the registering consumer, to the key of the node it is for. */
  running: Record<string, string>
  setRunning: (workKey: string, nodeKey: string | null) => void
}

const useNodeWorkStore = create<NodeWorkState>((set) => ({
  running: {},
  setRunning: (workKey, nodeKey) =>
    set((state) => {
      if ((state.running[workKey] ?? null) === nodeKey) return {}
      const { [workKey]: _previous, ...rest } = state.running
      void _previous
      return { running: nodeKey === null ? rest : { ...rest, [workKey]: nodeKey } }
    }),
}))

/** Whether any registered work for the node kept under *nodeKey* is running. */
export function useNodeWorkRunning(nodeKey: string | null): boolean {
  return useNodeWorkStore((state) =>
    nodeKey === null ? false : Object.values(state.running).includes(nodeKey),
  )
}

const stopHandlers = new Map<string, Set<() => void>>()

/**
 * Register what stops a consumer's work for the node kept under *nodeKey*. Not
 * React state: Stop is a user event, and the cancellation belongs in that event.
 */
export function registerNodeStop(nodeKey: string, handler: () => void): () => void {
  const handlers = stopHandlers.get(nodeKey) ?? new Set<() => void>()
  handlers.add(handler)
  stopHandlers.set(nodeKey, handlers)
  return () => {
    handlers.delete(handler)
    if (handlers.size === 0) stopHandlers.delete(nodeKey)
  }
}

/** Stop every registered consumer's work for the node kept under *nodeKey*. */
export function stopNodeWork(nodeKey: string): void {
  for (const handler of stopHandlers.get(nodeKey) ?? []) handler()
}

export default useNodeWorkStore
