import { createContext, useContext } from "react"

/**
 * Whether the active node's work is running, and what stops it. Provided around
 * the active node's preview so its frame's Refresh button can read Stop while
 * the work runs, without every preview component passing it along.
 */
export interface PreviewRun {
  running: boolean
  onStop: () => void
}

export const PreviewRunContext = createContext<PreviewRun | null>(null)

export function usePreviewRun(): PreviewRun | null {
  return useContext(PreviewRunContext)
}
