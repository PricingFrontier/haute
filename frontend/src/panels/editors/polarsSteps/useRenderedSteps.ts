import { useEffect, useMemo, useRef, useState } from "react"

import { renderPolarsSteps, resolveFreeCodeColumns } from "../../../api/client"
import type { StepStart } from "../../../utils/polarsStepInputs"
import type { ColumnInfo } from "./derivedColumns"
import type { Step } from "./types"

/** The columns the editor knows: each input's by name, and a frame-mode surface's `df`. */
export type KnownColumns = { inputs: Readonly<Record<string, ColumnInfo[]>>; frame: ColumnInfo[] }

/** The columns after one free-code step, or why they could not be resolved. */
export type FreeCodeColumns = { columns: ColumnInfo[] | null; message: string }

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
  /**
   * By step id, the columns last resolved after each free-code step whose
   * earlier steps and known columns are still the ones that request carried,
   * so later renders (pending, or failing on a half-built later step) keep them.
   */
  freeCode: ReadonlyMap<string, FreeCodeColumns>
}

type Resolved = { signature: string; id: string; columns: FreeCodeColumns }

const DEBOUNCE_MS = 250

/** What a free-code step's columns depend on: the steps up to it and the known columns. */
const signatureOf = (steps: Step[], index: number, knownKey: string) => `${knownKey}\u0000${JSON.stringify(steps.slice(0, index + 1))}`

/**
 * Render `steps` through the backend, debounced, tagging every request with
 * the steps revision it was made for so an older response can never replace a
 * newer one. `inputNames` are the surface's eligible input names and `start`
 * its start mode; both travel with every request. `onRendered` fires with the
 * code of a successful render for the current revision only, at once: the
 * columns after each free-code step are a second request, for `nodeId` with
 * the `known` columns of the eligible inputs (and, in `frame` mode, of `df`),
 * made only after that render and only for a list with free code, because
 * resolving them runs the snippet.
 */
export function useRenderedSteps(
  steps: Step[],
  inputNames: string[],
  start: StepStart,
  known: KnownColumns,
  nodeId: string,
  onRendered?: (code: string) => void,
): RenderedStepsState {
  const revisionRef = useRef(0)
  const onRenderedRef = useRef(onRendered)
  useEffect(() => {
    onRenderedRef.current = onRendered
  })
  const stepsKey = JSON.stringify(steps)
  const namesKey = JSON.stringify(inputNames)
  const inputColumns = useMemo(
    () => Object.fromEntries(inputNames.flatMap((name) => (known.inputs[name]?.length ? [[name, known.inputs[name]]] : []))),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [namesKey, known.inputs],
  )
  const frameColumns = start === "frame" ? known.frame : []
  const knownKey = JSON.stringify([inputColumns, frameColumns])
  const [state, setState] = useState<Omit<RenderedStepsState, "freeCode">>({
    status: steps.length === 0 ? "empty" : "pending",
    code: "",
    error: null,
    revisionRendered: 0,
    revision: 0,
    stepLines: [],
  })
  const [resolved, setResolved] = useState<Resolved[]>([])

  useEffect(() => {
    revisionRef.current += 1
    const revision = revisionRef.current
    if (steps.length === 0) {
      setState({ status: "empty", code: "", error: null, revisionRendered: revision, revision, stepLines: [] })
      setResolved([])
      return
    }
    setState((prev) => ({ ...prev, status: "pending", revision, stepLines: [] }))
    const controller = new AbortController()
    const timer = setTimeout(() => {
      const current = () => !controller.signal.aborted && revision === revisionRef.current
      renderPolarsSteps({ steps, inputNames, start, signal: controller.signal })
        .then((response) => {
          if (!current()) return
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
            if (!steps.some((s) => s.kind === "free_code")) {
              setResolved([])
              return
            }
            const resolvedAt = (index: number, columns: FreeCodeColumns): Resolved => ({
              signature: signatureOf(steps, index, knownKey),
              id: steps[index].id,
              columns,
            })
            resolveFreeCodeColumns({ nodeId, steps, inputNames, start, inputColumns, frameColumns, signal: controller.signal })
              .then((columns) => {
                if (!current()) return
                setResolved(
                  columns.free_code_columns.flatMap((entry) =>
                    steps[entry.step_index] === undefined ? [] : [resolvedAt(entry.step_index, { columns: entry.columns, message: entry.message })],
                  ),
                )
              })
              .catch((err: unknown) => {
                if (!current()) return
                if (err instanceof DOMException && err.name === "AbortError") return
                // A failed request is each free-code step's reason, never a silent gap.
                const message = `the request failed: ${err instanceof Error ? err.message : String(err)}`
                setResolved(steps.flatMap((s, index) => (s.kind === "free_code" ? [resolvedAt(index, { columns: null, message })] : [])))
              })
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
          if (controller.signal.aborted || revision !== revisionRef.current) return
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
    // The serialised keys are the change signal; `steps`/`inputNames` and the
    // known columns are read from the closure of the same render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stepsKey, namesKey, start, knownKey, nodeId])

  const freeCode = useMemo(() => {
    const current = new Map<string, FreeCodeColumns>()
    for (const entry of resolved) {
      const index = steps.findIndex((s) => s.id === entry.id)
      if (index >= 0 && signatureOf(steps, index, knownKey) === entry.signature) current.set(entry.id, entry.columns)
    }
    return current
    // `stepsKey` stands for `steps`.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resolved, stepsKey, knownKey])

  return { ...state, freeCode }
}
