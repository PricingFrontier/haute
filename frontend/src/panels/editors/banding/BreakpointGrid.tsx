import { Plus, Trash } from "lucide-react"
import { withAlpha } from "../../../utils/color"

interface BreakpointGridProps {
  breakpoints: { boundary: string; label: string }[]
  onUpdate: (breakpoints: { boundary: string; label: string }[]) => void
  rightClosed?: boolean  // read-only, defaults to true
  accentColor: string
  matchCounts?: number[]
}

export function BreakpointGrid({
  breakpoints,
  onUpdate,
  rightClosed = true,
  accentColor,
  matchCounts,
}: BreakpointGridProps) {
  const showMatches = matchCounts != null

  const updateBreakpoint = (index: number, field: "boundary" | "label", value: string) => {
    const next = breakpoints.map((bp, i) =>
      i === index ? { ...bp, [field]: value } : bp,
    )
    onUpdate(next)
  }

  const addBreakpoint = () => {
    onUpdate([...breakpoints, { boundary: "", label: "" }])
  }

  const removeBreakpoint = (index: number) => {
    onUpdate(breakpoints.filter((_, i) => i !== index))
  }

  return (
    <div className="space-y-2">
      <div
        className="rounded-lg overflow-hidden"
        style={{ border: "1px solid var(--border)", background: "var(--bg-input)" }}
      >
        <div style={{ maxHeight: 300, overflowY: "auto" }}>
          <table className="w-full text-[11px]">
            <thead>
              <tr
                style={{
                  borderBottom: "1px solid var(--border)",
                  background: "var(--bg-elevated)",
                  position: "sticky",
                  top: 0,
                  zIndex: 1,
                }}
              >
                <th className="text-left px-2 py-1.5 font-semibold" style={{ color: "var(--text-muted)", width: 70 }}>
                  Up to <span style={{ fontWeight: "normal", opacity: 0.6 }}>{rightClosed ? "(incl.)" : "(excl.)"}</span>
                </th>
                <th className="text-left px-2 py-1.5 font-semibold" style={{ color: "var(--text-muted)" }}>
                  Band name
                </th>
                {showMatches && (
                  <th
                    className="text-right px-2 py-1.5 font-semibold"
                    style={{ color: "var(--text-muted)", width: 55 }}
                  >
                    Matches
                  </th>
                )}
                <th style={{ width: 28 }} />
              </tr>
            </thead>
            <tbody>
              {breakpoints.length === 0 ? (
                <tr>
                  <td colSpan={showMatches ? 4 : 3} className="px-3 py-4 text-center" style={{ color: "var(--text-muted)" }}>
                    <div className="text-[11px] leading-relaxed">
                      Define boundaries to split values into bands.
                      <br />
                      <span style={{ opacity: 0.7 }}>Example: for age, add boundaries at 25, 35, 45, 65</span>
                    </div>
                  </td>
                </tr>
              ) : (
                breakpoints.map((bp, i) => (
                  <tr key={i} style={{ borderBottom: "1px solid var(--border)" }}>
                    <td className="px-1 py-1">
                      <input
                        type="number"
                        aria-label={`Breakpoint ${i + 1} boundary`}
                        value={bp.boundary}
                        onChange={(e) => updateBreakpoint(i, "boundary", e.target.value)}
                        className="w-full px-1.5 py-1 rounded text-[11px] font-mono focus:outline-none"
                        style={{
                          background: "var(--bg-surface)",
                          border: "1px solid var(--border)",
                          color: "var(--text-primary)",
                        }}
                        placeholder=""
                      />
                    </td>
                    <td className="px-1 py-1">
                      <input
                        type="text"
                        aria-label={`Breakpoint ${i + 1} label`}
                        value={bp.label}
                        onChange={(e) => updateBreakpoint(i, "label", e.target.value)}
                        className="w-full px-1.5 py-1 rounded text-[11px] font-mono font-semibold focus:outline-none"
                        style={{
                          background: "var(--bg-surface)",
                          border: "1px solid var(--border)",
                          color: accentColor,
                        }}
                        placeholder=""
                      />
                    </td>
                    {showMatches && (
                      <td className="px-2 py-1 text-right">
                        <span className="text-[11px] font-mono" style={{ color: "var(--text-secondary)" }}>
                          {matchCounts[i] ?? "—"}
                        </span>
                      </td>
                    )}
                    <td className="px-1 py-1 text-center">
                      <button
                        aria-label="Delete breakpoint"
                        onClick={() => removeBreakpoint(i)}
                        className="p-0.5 rounded transition-colors hover:text-[var(--danger)] focus-visible:text-[var(--danger)]"
                        style={{ color: "var(--text-muted)" }}
                      >
                        <Trash size={11} />
                      </button>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Add button */}
      <button
        aria-label="Add breakpoint"
        onClick={addBreakpoint}
        className="flex items-center gap-1 px-2 py-1 rounded-md text-[11px] font-medium transition-colors hover:brightness-90 focus-visible:brightness-90"
        style={{
          background: withAlpha(accentColor, 0.1),
          color: accentColor,
          border: `1px solid ${withAlpha(accentColor, 0.3)}`,
        }}
      >
        <Plus size={11} /> Add
      </button>
    </div>
  )
}
