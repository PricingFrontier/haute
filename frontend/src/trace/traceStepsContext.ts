import { createContext } from "react"
import type { TraceStep } from "../types/trace"

/**
 * Every step of the trace on display, for details that explain a value by
 * following it into other steps (a derivation tree). Empty outside a trace
 * panel, where such details show only their own step.
 */
export const TraceStepsContext = createContext<TraceStep[]>([])
