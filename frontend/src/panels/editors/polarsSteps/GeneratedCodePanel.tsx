import { ChevronDown, ChevronUp, Code, Lock } from "lucide-react"
import { useEffect, useId, useMemo, useState } from "react"

import type { StepStart } from "../../../utils/polarsStepInputs"
import { stepDisplayLabel } from "./catalogue"
import { highlightPython } from "./codeHighlight"

/** How long a render may be pending before the code fades, so typing does not flicker. */
export const PENDING_FADE_MS = 400

/**
 * The locked, line-numbered rendering of the current steps, highlighted like
 * code mode. Keeps the last good program while a render is pending (fading it
 * only when the render is slow); while the latest render fails the code is
 * dimmed and labelled out of date, with a neutral note of what is unfinished
 * until a run has failed, when the failure shows as an error. Lines are
 * linked to their steps: the pointed-at step's lines are tinted, pointing at a
 * line points at its step, and clicking a line opens that step's card.
 */
export default function GeneratedCodePanel({
  start,
  code,
  pending,
  stale = false,
  note = null,
  error,
  errorLine,
  onGoToError,
  stepLines = [],
  activeStep = null,
  onPointStep,
  onOpenStep,
  switchEnabled,
  switchDisabledReason,
  onSwitchToCode,
}: {
  /** Names the failing step the way the surface numbers its cards. */
  start: StepStart
  code: string
  pending: boolean
  /** The latest render failed, so `code` is the last good program. */
  stale?: boolean
  /** What the unfinished steps still need, shown neutrally while no run has failed. */
  note?: { stepIndex: number | null; message: string } | null
  /** Latest render failure, shown as an error once a run has failed. */
  error: { stepIndex: number | null; message: string } | null
  /** 1-based line of the last execution failure, from the preview. */
  errorLine?: number | null
  onGoToError?: (stepIndex: number) => void
  /** Inclusive 1-based line range of each step, by schema index, for the current render. */
  stepLines?: number[][]
  /** The step whose lines to tint. */
  activeStep?: number | null
  onPointStep?: (stepIndex: number | null) => void
  onOpenStep?: (stepIndex: number) => void
  switchEnabled: boolean
  switchDisabledReason?: string
  onSwitchToCode: () => void
}) {
  const [open, setOpen] = useState(true)
  const [slow, setSlow] = useState(false)
  const id = useId()
  const lines = useMemo(() => (code.length > 0 ? highlightPython(code) : []), [code])
  useEffect(() => {
    if (!pending) return
    const timer = setTimeout(() => setSlow(true), PENDING_FADE_MS)
    return () => {
      clearTimeout(timer)
      setSlow(false)
    }
  }, [pending])
  const stepOf = (line: number): number | null => {
    const index = stepLines.findIndex(([first, last]) => line >= first && line <= last)
    return index < 0 ? null : index
  }
  const activeRange = activeStep !== null ? stepLines[activeStep] : undefined
  const dimmed = stale || slow

  return (
    <section className="rounded-lg" style={{ background: "var(--bg-elevated)", border: "1px solid var(--border)" }} aria-labelledby={`${id}-title`}>
      <div className="flex items-center gap-2 px-3 py-2">
        <button
          type="button"
          aria-expanded={open}
          aria-controls={`${id}-body`}
          onClick={() => setOpen((v) => !v)}
          className="focus-ring flex items-center gap-1.5 rounded px-1 -mx-1 text-[11px] font-semibold"
          style={{ color: "var(--text-secondary)" }}
        >
          <Lock size={11} aria-hidden="true" />
          <span id={`${id}-title`}>Generated code</span>
          {open ? <ChevronUp size={12} aria-hidden="true" /> : <ChevronDown size={12} aria-hidden="true" />}
        </button>
        {slow && (
          <span className="text-[10px]" style={{ color: "var(--text-muted)" }}>
            rendering…
          </span>
        )}
        {stale && !slow && lines.length > 0 && (
          <span className="text-[10px]" style={{ color: "var(--text-muted)" }}>
            out of date
          </span>
        )}
      </div>
      {error ? (
        <div role="alert" className="mx-3 mb-2 flex flex-wrap items-center gap-2 text-[11px]" style={{ color: "var(--danger)" }}>
          <span>
            {error.stepIndex != null ? `${stepDisplayLabel(error.stepIndex, start)}: ` : ""}
            {error.message}
          </span>
          {error.stepIndex != null && onGoToError && (
            <button
              type="button"
              onClick={() => onGoToError(error.stepIndex as number)}
              className="focus-ring underline rounded px-0.5"
            >
              Go to error
            </button>
          )}
        </div>
      ) : (
        note && (
          <p role="status" data-testid="polars-code-note" className="mx-3 mb-2 mt-0 text-[11px] leading-snug" style={{ color: "var(--text-secondary)" }}>
            {note.stepIndex != null ? `${stepDisplayLabel(note.stepIndex, start)} isn't finished: ${note.message}` : note.message}
          </p>
        )
      )}
      <div id={`${id}-body`} hidden={!open}>
        <pre
          data-testid="polars-generated-code"
          data-stale={stale ? "true" : undefined}
          className="mx-3 mb-2 overflow-x-auto rounded-md py-1.5 text-[11px] font-mono leading-relaxed transition-opacity"
          style={{ background: "var(--bg-input)", color: "var(--text-primary)", opacity: dimmed ? 0.5 : 1, border: "1px solid var(--border-subtle)" }}
          aria-label="Generated Polars code"
          onMouseLeave={onPointStep ? () => onPointStep(null) : undefined}
        >
          {lines.length === 0 ? (
            <span className="px-2" style={{ color: "var(--text-muted)" }}>No code yet.</span>
          ) : (
            lines.map((tokens, index) => {
              const number = index + 1
              const step = stepOf(number)
              const active = activeRange !== undefined && number >= activeRange[0] && number <= activeRange[1]
              const failing = number === errorLine
              return (
                <div
                  key={number}
                  className={`flex gap-2 px-2 ${active ? "generated-code-line--active" : ""} ${step !== null && onOpenStep ? "cursor-pointer" : ""}`}
                  style={failing ? { background: "var(--warning-soft-emphasis)" } : undefined}
                  data-line={number}
                  data-step={step ?? undefined}
                  data-active={active ? "true" : undefined}
                  onMouseEnter={onPointStep ? () => onPointStep(step) : undefined}
                  onClick={step !== null && onOpenStep ? () => onOpenStep(step) : undefined}
                >
                  <span className="select-none w-5 text-right shrink-0" style={{ color: failing ? "var(--warning)" : "var(--text-muted)" }} aria-hidden="true">
                    {number}
                  </span>
                  <span className="whitespace-pre">
                    {tokens.map((token, i) => (
                      <span key={i} style={token.color ? { color: token.color } : undefined}>
                        {token.text}
                      </span>
                    ))}
                  </span>
                </div>
              )
            })
          )}
        </pre>
      </div>
      <div className="flex items-center justify-end gap-2 px-3 pb-2">
        <button
          type="button"
          onClick={onSwitchToCode}
          disabled={!switchEnabled}
          title={switchEnabled ? "Replace the steps with editable code" : switchDisabledReason}
          className="focus-ring inline-flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs font-medium disabled:opacity-50 disabled:cursor-not-allowed"
          style={{ color: "var(--text-secondary)", border: "1px solid var(--border)" }}
        >
          <Code size={12} aria-hidden="true" />
          Switch to code
        </button>
      </div>
    </section>
  )
}
