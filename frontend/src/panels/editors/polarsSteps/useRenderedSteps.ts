import { useEffect, useRef, useState } from "react"

import { renderPolarsSteps } from "../../../api/client"
import type { Step } from "./types"

export type RenderedStepsState = {
  /** `empty` for an empty list (nothing is requested), `pending` while a
   *  render is in flight, `ok` / `error` for the latest completed render. */
  status: "empty" | "pending" | "ok" | "error"
  code: string
  /** The failing step and message of the latest render, when it failed. */
  error: { stepIndex: number | null; message: string } | null
  /** Steps revision the current `code`/`error` describe. */
  revisionRendered: number
  /** Steps revision the caller is currently editing. */
  revision: number
  /** Inclusive generated-code line ranges for the successful current render. */
  stepLines: number[][]
}

const DEBOUNCE_MS = 250

/**
 * Render `steps` through the backend, debounced, tagging every request with
 * the steps revision it was made for so an older response can never replace a
 * newer one. `onRendered` fires with the code of a successful render for the
 * current revision only.
 */
export function useRenderedSteps(
  steps: Step[],
  inputNames: string[],
  onRendered?: (code: string) => void,
): RenderedStepsState {
  const revisionRef = useRef(0)
  const onRenderedRef = useRef(onRendered)
  useEffect(() => {
    onRenderedRef.current = onRendered
  })
  const stepsKey = JSON.stringify(steps)
  const namesKey = JSON.stringify(inputNames)
  const [state, setState] = useState<RenderedStepsState>({
    status: steps.length === 0 ? "empty" : "pending",
    code: "",
    error: null,
    revisionRendered: 0,
    revision: 0,
    stepLines: [],
  })

  useEffect(() => {
    revisionRef.current += 1
    const revision = revisionRef.current
    if (steps.length === 0) {
      setState({ status: "empty", code: "", error: null, revisionRendered: revision, revision, stepLines: [] })
      return
    }
    setState((prev) => ({ ...prev, status: "pending", revision, stepLines: [] }))
    const controller = new AbortController()
    const timer = setTimeout(() => {
      renderPolarsSteps({ steps, inputNames, signal: controller.signal })
        .then((response) => {
          if (revision !== revisionRef.current) return
          if (response.ok) {
            setState({
              status: "ok",
              code: response.code,
              error: null,
              revisionRendered: revision,
              revision,
              stepLines: response.step_lines,
            })
            onRenderedRef.current?.(response.code)
          } else {
            setState((prev) => ({
              ...prev,
              status: "error",
              error: { stepIndex: response.step_index, message: response.message },
              revisionRendered: revision,
              revision,
              stepLines: [],
            }))
          }
        })
        .catch((err: unknown) => {
          if (revision !== revisionRef.current) return
          if (err instanceof DOMException && err.name === "AbortError") return
          const message = err instanceof Error ? err.message : String(err)
          setState((prev) => ({
            ...prev,
            status: "error",
            error: { stepIndex: null, message: `Could not render steps: ${message}` },
            revisionRendered: revision,
            revision,
            stepLines: [],
          }))
        })
    }, DEBOUNCE_MS)
    return () => {
      clearTimeout(timer)
      controller.abort()
    }
    // The serialised keys are the change signal; `steps`/`inputNames` are read
    // from the closure of the same render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stepsKey, namesKey])

  return state
}
