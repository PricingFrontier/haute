import { create } from "zustand"
import type { WriteOutputResponse } from "../api/types"

export type OutputWritePhase =
  | "writing"
  | "success"
  | "error"
  | "confirm_overwrite"

export type OutputWriteState = {
  requestId: number
  configIdentity: string
  phase: OutputWritePhase
  result?: WriteOutputResponse
  message?: string
}

type OutputWriteStore = {
  writes: Record<string, OutputWriteState | undefined>
  nextRequestId: number
  begin: (nodeId: string, configIdentity: string) => number | null
  complete: (
    nodeId: string,
    requestId: number,
    configIdentity: string,
    state: Omit<OutputWriteState, "requestId" | "configIdentity">,
  ) => void
  resetForTests: () => void
}

const useOutputWriteStore = create<OutputWriteStore>()((set) => ({
  writes: {},
  nextRequestId: 0,
  begin: (nodeId, configIdentity) => {
    let requestId = 0
    set((current) => {
      if (current.writes[nodeId]?.phase === "writing") return current
      requestId = current.nextRequestId + 1
      return {
        nextRequestId: requestId,
        writes: {
          ...current.writes,
          [nodeId]: { requestId, configIdentity, phase: "writing" },
        },
      }
    })
    return requestId || null
  },
  complete: (nodeId, requestId, configIdentity, state) =>
    set((current) => {
      const active = current.writes[nodeId]
      if (
        !active ||
        active.requestId !== requestId ||
        active.configIdentity !== configIdentity
      ) {
        return current
      }
      return {
        writes: {
          ...current.writes,
          [nodeId]: { requestId, configIdentity, ...state },
        },
      }
    }),
  resetForTests: () => set({ writes: {}, nextRequestId: 0 }),
}))

export const resetOutputWriteStoreForTests = () =>
  useOutputWriteStore.getState().resetForTests()

export default useOutputWriteStore
