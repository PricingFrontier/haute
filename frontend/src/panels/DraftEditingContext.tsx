import { createContext, useContext } from "react"

/** True only while a node editor is editing a persisted recovery proposal. */
export const DraftEditingContext = createContext(false)

export function useIsRecoveryDraft(): boolean {
  return useContext(DraftEditingContext)
}
