import { create } from "zustand"
import type { WriteOutputResponse } from "../api/types"

export type OutputWritePhase =
  | "writing"
  | "success"
  | "error"
  | "confirm_overwrite"

export type OutputWriteState = {
  requestId: number
  requestIdentity: string
  phase: OutputWritePhase
  result?: WriteOutputResponse
  message?: string
}

type OutputWriteStore = {
  /** Each Data Output's latest write, by the node's result key (`resultKey`). */
  writes: Record<string, OutputWriteState | undefined>
  nextRequestId: number
  begin: (nodeKey: string, requestIdentity: string) => number | null
  complete: (
    nodeKey: string,
    requestId: number,
    requestIdentity: string,
    state: Omit<OutputWriteState, "requestId" | "requestIdentity">,
  ) => void
  clear: (nodeKey: string, requestId: number) => void
  /**
   * Forget a deleted node's write, even one still writing: a write already sent
   * finishes on the server, and its answer then finds nothing to complete.
   */
  forget: (nodeKey: string) => void
  resetForTests: () => void
}

const useOutputWriteStore = create<OutputWriteStore>()((set) => ({
  writes: {},
  nextRequestId: 0,
  begin: (nodeKey, requestIdentity) => {
    let requestId = 0
    set((current) => {
      if (current.writes[nodeKey]?.phase === "writing") return current
      requestId = current.nextRequestId + 1
      return {
        nextRequestId: requestId,
        writes: {
          ...current.writes,
          [nodeKey]: { requestId, requestIdentity, phase: "writing" },
        },
      }
    })
    return requestId || null
  },
  complete: (nodeKey, requestId, requestIdentity, state) =>
    set((current) => {
      const active = current.writes[nodeKey]
      if (
        !active ||
        active.requestId !== requestId ||
        active.requestIdentity !== requestIdentity
      ) {
        return current
      }
      return {
        writes: {
          ...current.writes,
          [nodeKey]: { requestId, requestIdentity, ...state },
        },
      }
    }),
  clear: (nodeKey, requestId) =>
    set((current) => {
      const active = current.writes[nodeKey]
      if (!active || active.requestId !== requestId || active.phase === "writing") {
        return current
      }
      const writes = { ...current.writes }
      delete writes[nodeKey]
      return { writes }
    }),
  forget: (nodeKey) =>
    set((current) => {
      if (!(nodeKey in current.writes)) return current
      const writes = { ...current.writes }
      delete writes[nodeKey]
      return { writes }
    }),
  resetForTests:() => set({ writes: {}, nextRequestId: 0 }),
}))

export const resetOutputWriteStoreForTests = () =>
  useOutputWriteStore.getState().resetForTests()

export default useOutputWriteStore
