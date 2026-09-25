import { useState, type ReactNode } from "react"
import { withAlpha } from "../../../utils/color"
import {
  CALENDAR_UNITS,
  generateCalendarBreakpoints,
  generateEvenBreakpoints,
  type CalendarGenerateSettings,
  type CalendarUnit,
  type GenerateSettings,
} from "./bandingUtils"

type GeneratedBreakpoints = { boundary: string; label: string }[]

interface CommonProps {
  onGenerate: (breakpoints: GeneratedBreakpoints) => void
  onClose: () => void
  accentColor: string
}

interface NumericProps extends CommonProps {
  temporal?: false
  dataMin?: number
  dataMax?: number
  /** The settings the field's breakpoints were generated with, to start from. */
  initial?: GenerateSettings | null
}

/** A Date or Datetime column: Start and End are dates, and Step counts calendar units. */
interface CalendarProps extends CommonProps {
  temporal: true
  /** The data's first and last dates, `YYYY-MM-DD`. */
  dataMin?: string
  dataMax?: string
  /** The settings the field's breakpoints imply, to start from. */
  initial?: CalendarGenerateSettings | null
  /** Whether the factor's bands include their "Up to" date, which sets each band's boundary. */
  rightClosed: boolean
}

type GenerateBandsDialogProps = NumericProps | CalendarProps

export function GenerateBandsDialog(props: GenerateBandsDialogProps) {
  return props.temporal ? <CalendarGenerate {...props} /> : <NumericGenerate {...props} />
}

const INPUT_CLASS = "w-full px-1.5 py-1 rounded text-[11px] font-mono focus:outline-none"
const LABEL_CLASS = "text-[11px] font-medium block mb-0.5"
const INPUT_STYLE = {
  background: "var(--bg-panel)",
  border: "1px solid var(--border)",
  color: "var(--text-primary)",
}

function NumericGenerate({ onGenerate, onClose, accentColor, dataMin, dataMax, initial }: NumericProps) {
  const [start, setStart] = useState<number | "">(initial?.start ?? dataMin ?? "")
  const [end, setEnd] = useState<number | "">(initial?.end ?? dataMax ?? "")

  // Auto-suggest step for ~10 bands when data range is known
  const suggestedStep = (dataMin != null && dataMax != null && dataMax > dataMin)
    ? Math.ceil((dataMax - dataMin) / 10)
    : ""
  const [step, setStep] = useState<number | "">(initial?.step ?? suggestedStep)
  const [error, setError] = useState<string | null>(null)

  const handleGenerate = () => {
    setError(null)

    const s = typeof start === "number" ? start : parseFloat(String(start))
    const e = typeof end === "number" ? end : parseFloat(String(end))
    const st = typeof step === "number" ? step : parseFloat(String(step))

    if (isNaN(s) || isNaN(e) || isNaN(st)) {
      setError("All fields must be valid numbers")
      return
    }

    if (st <= 0) {
      setError("Step must be greater than 0")
      return
    }

    if (e <= s) {
      setError("End must be greater than start")
      return
    }

    // Upper-bound boundaries from start + step to end: Start=4000, End=13600,
    // Step=1200 gives 5200, 6400, …, 13600, each band labelled by its range.
    const breakpoints = generateEvenBreakpoints(s, e, st)

    onGenerate(breakpoints)
  }

  return (
    <GenerateFrame onGenerate={handleGenerate} onClose={onClose} accentColor={accentColor} error={error}>
      <div className="grid grid-cols-3 gap-2">
        <div>
          <label htmlFor="gen-start" className={LABEL_CLASS} style={{ color: "var(--text-muted)" }}>
            Start
          </label>
          <input
            id="gen-start"
            type="number"
            value={start}
            onChange={(e) => setStart(e.target.value === "" ? "" : parseFloat(e.target.value))}
            className={INPUT_CLASS}
            style={INPUT_STYLE}
            placeholder="0"
          />
        </div>
        <div>
          <label htmlFor="gen-end" className={LABEL_CLASS} style={{ color: "var(--text-muted)" }}>
            End
          </label>
          <input
            id="gen-end"
            type="number"
            value={end}
            onChange={(e) => setEnd(e.target.value === "" ? "" : parseFloat(e.target.value))}
            className={INPUT_CLASS}
            style={INPUT_STYLE}
            placeholder="100"
          />
        </div>
        <div>
          <label htmlFor="gen-step" className={LABEL_CLASS} style={{ color: "var(--text-muted)" }}>
            Step
          </label>
          <input
            id="gen-step"
            type="number"
            value={step}
            onChange={(e) => setStep(e.target.value === "" ? "" : parseFloat(e.target.value))}
            className={INPUT_CLASS}
            style={INPUT_STYLE}
            placeholder="10"
          />
        </div>
      </div>
    </GenerateFrame>
  )
}

/**
 * Generate on a date column: bands of a whole number of days, weeks, months or
 * years from Start, the last capped at End. Without settings to start from it
 * offers the data's first and last dates in steps of one month.
 */
function CalendarGenerate({ onGenerate, onClose, accentColor, dataMin, dataMax, initial, rightClosed }: CalendarProps) {
  const [start, setStart] = useState(initial?.start ?? dataMin ?? "")
  const [end, setEnd] = useState(initial?.end ?? dataMax ?? "")
  const [step, setStep] = useState<number | "">(initial?.step ?? 1)
  const [unit, setUnit] = useState<CalendarUnit>(initial?.unit ?? "months")
  const [error, setError] = useState<string | null>(null)

  const handleGenerate = () => {
    setError(null)
    let breakpoints: GeneratedBreakpoints
    try {
      breakpoints = generateCalendarBreakpoints({ start, end, step: step === "" ? NaN : step, unit }, rightClosed)
    } catch (err) {
      // The generator refuses settings it cannot use with a message for the user.
      if (!(err instanceof RangeError)) throw err
      setError(err.message)
      return
    }
    onGenerate(breakpoints)
  }

  return (
    <GenerateFrame onGenerate={handleGenerate} onClose={onClose} accentColor={accentColor} error={error}>
      <div className="grid grid-cols-2 gap-2">
        <div>
          <label htmlFor="gen-start" className={LABEL_CLASS} style={{ color: "var(--text-muted)" }}>
            Start
          </label>
          <input
            id="gen-start"
            type="date"
            value={start}
            onChange={(e) => setStart(e.target.value)}
            className={INPUT_CLASS}
            style={INPUT_STYLE}
          />
        </div>
        <div>
          <label htmlFor="gen-end" className={LABEL_CLASS} style={{ color: "var(--text-muted)" }}>
            End
          </label>
          <input
            id="gen-end"
            type="date"
            value={end}
            onChange={(e) => setEnd(e.target.value)}
            className={INPUT_CLASS}
            style={INPUT_STYLE}
          />
        </div>
        <div className="col-span-2">
          <label htmlFor="gen-step" className={LABEL_CLASS} style={{ color: "var(--text-muted)" }}>
            Step
          </label>
          <div className="flex gap-2">
            <input
              id="gen-step"
              type="number"
              min={1}
              step={1}
              value={step}
              onChange={(e) => setStep(e.target.value === "" ? "" : Number(e.target.value))}
              className={INPUT_CLASS}
              style={INPUT_STYLE}
            />
            <select
              aria-label="Step unit"
              value={unit}
              onChange={(e) => setUnit(e.target.value as CalendarUnit)}
              className={INPUT_CLASS}
              style={INPUT_STYLE}
            >
              {CALENDAR_UNITS.map((option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </select>
          </div>
        </div>
      </div>
    </GenerateFrame>
  )
}

function GenerateFrame({
  children,
  error,
  onGenerate,
  onClose,
  accentColor,
}: {
  children: ReactNode
  error: string | null
  onGenerate: () => void
  onClose: () => void
  accentColor: string
}) {
  return (
    <div
      role="dialog"
      aria-label="Generate evenly-spaced bands"
      className="rounded-lg p-3 space-y-2.5"
      style={{
        background: "var(--bg-elevated)",
        border: `1px solid var(--border)`,
        boxShadow: "0 4px 12px rgba(0,0,0,0.15)",
      }}
    >
      <div className="text-xs font-semibold" style={{ color: "var(--text-secondary)" }}>
        Generate even bands
      </div>

      {children}

      {error && (
        <div className="text-[11px] font-medium" style={{ color: "var(--danger)" }}>
          {error}
        </div>
      )}

      <div className="flex items-center gap-2 justify-end">
        <button
          onClick={onClose}
          className="px-2.5 py-1 rounded-md text-[11px] font-medium transition-colors"
          style={{
            background: "var(--bg-panel)",
            border: "1px solid var(--border)",
            color: "var(--text-secondary)",
          }}
        >
          Cancel
        </button>
        <button
          onClick={onGenerate}
          className="px-2.5 py-1 rounded-md text-[11px] font-medium transition-colors"
          style={{
            background: withAlpha(accentColor, 0.15),
            border: `1px solid ${withAlpha(accentColor, 0.4)}`,
            color: accentColor,
          }}
        >
          Generate
        </button>
      </div>
    </div>
  )
}
