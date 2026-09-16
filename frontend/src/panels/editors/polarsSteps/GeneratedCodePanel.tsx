import { ChevronDown, ChevronUp, Code, Lock } from "lucide-react"
import { useId, useState } from "react"

import { stepDisplayLabel } from "./catalogue"

/**
 * The locked, line-numbered rendering of the current steps. Keeps the last
 * good program while a render is pending, tints the execution error line,
 * shows validation errors, and carries the confirmed switch to code.
 */
export default function GeneratedCodePanel({
  code,
  pending,
  error,
  errorLine,
  onGoToError,
  switchEnabled,
  switchDisabledReason,
  onSwitchToCode,
}: {
  code: string
  pending: boolean
  /** Latest render failure, if any. */
  error: { stepIndex: number | null; message: string } | null
  /** 1-based line of the last execution failure, from the preview. */
  errorLine?: number | null
  onGoToError?: (stepIndex: number) => void
  switchEnabled: boolean
  switchDisabledReason?: string
  onSwitchToCode: () => void
}) {
  const [open, setOpen] = useState(true)
  const id = useId()
  const lines = code.length > 0 ? code.split("\n") : []

  return (
    <section className="rounded-lg" style={{ background: "var(--bg-input)", border: "1px solid var(--border)" }} aria-labelledby={`${id}-title`}>
      <div className="flex items-center gap-2 px-3 py-2">
        <button
          type="button"
          aria-expanded={open}
          aria-controls={`${id}-body`}
          onClick={() => setOpen((v) => !v)}
          className="focus-ring flex items-center gap-1.5 rounded px-1 -mx-1 text-[11px] font-bold uppercase tracking-[0.08em]"
          style={{ color: "var(--text-secondary)" }}
        >
          <Lock size={11} aria-hidden="true" />
          <span id={`${id}-title`}>Generated code</span>
          {open ? <ChevronUp size={12} aria-hidden="true" /> : <ChevronDown size={12} aria-hidden="true" />}
        </button>
        {pending && (
          <span className="text-[10px]" style={{ color: "var(--text-muted)" }}>
            rendering…
          </span>
        )}
      </div>
      {error && (
        <div role="alert" className="mx-3 mb-2 flex flex-wrap items-center gap-2 text-[11px]" style={{ color: "var(--danger)" }}>
          <span>
            {error.stepIndex != null ? `${stepDisplayLabel(error.stepIndex)}: ` : ""}
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
      )}
      <div id={`${id}-body`} hidden={!open}>
        <pre
          data-testid="polars-generated-code"
          className="mx-3 mb-2 overflow-x-auto rounded-md px-2 py-1.5 text-[11px] font-mono leading-relaxed"
          style={{ background: "var(--bg-panel, transparent)", color: "var(--text-primary)", opacity: pending ? 0.6 : 1, border: "1px solid var(--border-subtle)" }}
          aria-label="Generated Polars code"
        >
          {lines.length === 0 ? (
            <span style={{ color: "var(--text-muted)" }}>No code yet.</span>
          ) : (
            lines.map((line, index) => {
              const number = index + 1
              const tone = number === errorLine ? "var(--warning)" : null
              return (
                <div key={number} className="flex gap-2" style={tone ? { color: tone } : undefined} data-line={number}>
                  <span className="select-none w-5 text-right shrink-0" style={{ color: "var(--text-muted)" }} aria-hidden="true">
                    {number}
                  </span>
                  <span className="whitespace-pre">{line}</span>
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
