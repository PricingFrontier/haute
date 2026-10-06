import { create } from "zustand"

/**
 * What a node's Stop button covers beyond its own preview request: work a
 * consumer of the node started — a shared data-point build, an Import — so the
 * frame can show Stop while it runs and stop all of it.
 *
 * Only work this browser tab started is registered. A build joined from
 * another tab or consumer is theirs to stop; this node only stops waiting.
 */
interface NodeWorkState {
  /** Running work, keyed by the registering consumer, to the node it is for. */
  running: Record<string, string>
  setRunning: (workKey: string, nodeId: string | null) => void
}

const useNodeWorkStore = create<NodeWorkState>((set) => ({
  running: {},
  setRunning: (workKey, nodeId) =>
    set((state) => {
      if ((state.running[workKey] ?? null) === nodeId) return {}
      const { [workKey]: _previous, ...rest } = state.running
      void _previous
      return { running: nodeId === null ? rest : { ...rest, [workKey]: nodeId } }
    }),
}))

/** Whether any registered work for *nodeId* is running. */
export function useNodeWorkRunning(nodeId: string | null): boolean {
  return useNodeWorkStore((state) =>
    nodeId === null ? false : Object.values(state.running).includes(nodeId),
  )
}

const stopHandlers = new Map<string, Set<() => void>>()

/**
 * Register what stops a consumer's work for *nodeId*. Not React state: Stop is
 * a user event, and the cancellation belongs in that event.
 */
export function registerNodeStop(nodeId: string, handler: () => void): () => void {
  const handlers = stopHandlers.get(nodeId) ?? new Set<() => void>()
  handlers.add(handler)
  stopHandlers.set(nodeId, handlers)
  return () => {
    handlers.delete(handler)
    if (handlers.size === 0) stopHandlers.delete(nodeId)
  }
}

/** Stop every registered consumer's work for *nodeId*. */
export function stopNodeWork(nodeId: string): void {
  for (const handler of stopHandlers.get(nodeId) ?? []) handler()
}

export default useNodeWorkStore
