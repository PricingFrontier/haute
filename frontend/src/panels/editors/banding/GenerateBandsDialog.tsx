import { useState } from "react"
import { withAlpha } from "../../../utils/color"

interface GenerateBandsDialogProps {
  onGenerate: (breakpoints: { boundary: string; label: string }[]) => void
  onClose: () => void
  accentColor: string
  dataMin?: number
  dataMax?: number
}

export function GenerateBandsDialog({
  onGenerate,
  onClose,
  accentColor,
  dataMin,
  dataMax,
}: GenerateBandsDialogProps) {
  const [start, setStart] = useState<number | "">(dataMin ?? "")
  const [end, setEnd] = useState<number | "">(dataMax ?? "")

  // Auto-suggest step for ~10 bands when data range is known
  const suggestedStep = (dataMin != null && dataMax != null && dataMax > dataMin)
    ? Math.ceil((dataMax - dataMin) / 10)
    : ""
  const [step, setStep] = useState<number | "">(suggestedStep)
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

    // Generate upper-bound boundary values from (start + step) to end.
    // Each boundary represents "values up to this number go in this band".
    // Example: Start=4000, End=13600, Step=1200 produces boundaries:
    //   5200, 6400, 7600, ..., 13600
    // Which gives bands: "4000-5200", "5200-6400", ..., "13600+"
    const boundaries: number[] = []
    for (let v = +(s + st).toFixed(10); v <= e; v = +(v + st).toFixed(10)) {
      boundaries.push(v)
      if (boundaries.length > 10000) break
    }
    // Always include the end value as the last boundary if not already there
    if (boundaries.length === 0 || boundaries[boundaries.length - 1] < e) {
      boundaries.push(e)
    }

    // Build labels showing the full range for each band.
    // Since intervals are right-closed (prev, boundary], for integer boundaries
    // the lower bound is prev+1, giving clean labels like "28–34".
    // For non-integer boundaries, use ">prev – high" to avoid ambiguity.
    const allInteger = Number.isInteger(s) && Number.isInteger(st) && boundaries.every(Number.isInteger)
    const breakpoints: { boundary: string; label: string }[] = []
    for (let i = 0; i < boundaries.length; i++) {
      const high = boundaries[i]
      if (i === 0) {
        breakpoints.push({ boundary: String(high), label: `${s}–${high}` })
      } else {
        const prev = boundaries[i - 1]
        if (allInteger) {
          breakpoints.push({ boundary: String(high), label: `${prev + 1}–${high}` })
        } else {
          breakpoints.push({ boundary: String(high), label: `>${prev}–${high}` })
        }
      }
    }

    onGenerate(breakpoints)
  }

  const inputStyle = {
    background: "var(--bg-panel)",
    border: "1px solid var(--border)",
    color: "var(--text-primary)",
  }

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

      <div className="grid grid-cols-3 gap-2">
        <div>
          <label htmlFor="gen-start" className="text-[11px] font-medium block mb-0.5" style={{ color: "var(--text-muted)" }}>
            Start
          </label>
          <input
            id="gen-start"
            type="number"
            value={start}
            onChange={(e) => setStart(e.target.value === "" ? "" : parseFloat(e.target.value))}
            className="w-full px-1.5 py-1 rounded text-[11px] font-mono focus:outline-none"
            style={inputStyle}
            placeholder="0"
          />
        </div>
        <div>
          <label htmlFor="gen-end" className="text-[11px] font-medium block mb-0.5" style={{ color: "var(--text-muted)" }}>
            End
          </label>
          <input
            id="gen-end"
            type="number"
            value={end}
            onChange={(e) => setEnd(e.target.value === "" ? "" : parseFloat(e.target.value))}
            className="w-full px-1.5 py-1 rounded text-[11px] font-mono focus:outline-none"
            style={inputStyle}
            placeholder="100"
          />
        </div>
        <div>
          <label htmlFor="gen-step" className="text-[11px] font-medium block mb-0.5" style={{ color: "var(--text-muted)" }}>
            Step
          </label>
          <input
            id="gen-step"
            type="number"
            value={step}
            onChange={(e) => setStep(e.target.value === "" ? "" : parseFloat(e.target.value))}
            className="w-full px-1.5 py-1 rounded text-[11px] font-mono focus:outline-none"
            style={inputStyle}
            placeholder="10"
          />
        </div>
      </div>

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
          onClick={handleGenerate}
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
