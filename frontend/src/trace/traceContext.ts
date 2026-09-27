import { createContext } from "react"
import type { TraceStep } from "../types/trace"

/**
 * Every step of the trace on display, for details that explain a value by
 * following it into other steps (a derivation tree). Empty outside a trace
 * panel, where such details show only their own step.
 */
export const TraceStepsContext = createContext<TraceStep[]>([])

export interface TraceNavigation {
  /** Open the step's card, scroll to it, and centre its node on the canvas. */
  focusStep: (nodeId: string) => void
  /** Ring the step's node on the canvas while pointed at; `null` clears it. */
  hoverStep: (nodeId: string | null) => void
}

/** How a trace detail points at another step. Inert outside a trace panel. */
export const TraceNavigationContext = createContext<TraceNavigation>({
  focusStep: () => {},
  hoverStep: () => {},
})
