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
  writes: Record<string, OutputWriteState | undefined>
  nextRequestId: number
  begin: (nodeId: string, requestIdentity: string) => number | null
  complete: (
    nodeId: string,
    requestId: number,
    requestIdentity: string,
    state: Omit<OutputWriteState, "requestId" | "requestIdentity">,
  ) => void
  clear: (nodeId: string, requestId: number) => void
  resetForTests: () => void
}

const useOutputWriteStore = create<OutputWriteStore>()((set) => ({
  writes: {},
  nextRequestId: 0,
  begin: (nodeId, requestIdentity) => {
    let requestId = 0
    set((current) => {
      if (current.writes[nodeId]?.phase === "writing") return current
      requestId = current.nextRequestId + 1
      return {
        nextRequestId: requestId,
        writes: {
          ...current.writes,
          [nodeId]: { requestId, requestIdentity, phase: "writing" },
        },
      }
    })
    return requestId || null
  },
  complete: (nodeId, requestId, requestIdentity, state) =>
    set((current) => {
      const active = current.writes[nodeId]
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
          [nodeId]: { requestId, requestIdentity, ...state },
        },
      }
    }),
  clear: (nodeId, requestId) =>
    set((current) => {
      const active = current.writes[nodeId]
      if (!active || active.requestId !== requestId || active.phase === "writing") {
        return current
      }
      const writes = { ...current.writes }
      delete writes[nodeId]
      return { writes }
    }),
  resetForTests: () => set({ writes: {}, nextRequestId: 0 }),
}))

export const resetOutputWriteStoreForTests = () =>
  useOutputWriteStore.getState().resetForTests()

export default useOutputWriteStore
