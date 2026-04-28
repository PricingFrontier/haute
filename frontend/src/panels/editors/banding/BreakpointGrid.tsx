import { AlertTriangle, Copy, Plus, Trash } from "lucide-react"
import { withAlpha } from "../../../utils/color"
import useToastStore from "../../../stores/useToastStore"
import { buildTsv, parsePastedGrid, writeClipboardText } from "../shared/tableClipboard"

type Breakpoint = { boundary: string; label: string }

interface BreakpointGridProps {
  breakpoints: Breakpoint[]
  onUpdate: (breakpoints: Breakpoint[]) => void
  rightClosed?: boolean  // read-only, defaults to true
  accentColor: string
  matchCounts?: number[]
}

const BREAKPOINT_FIELDS = ["boundary", "label"] as const
const BREAKPOINT_COPY_HEADERS = ["Up to", "Band name"] as const
const CELL_CLASS = "px-0.5 py-0.5"
const DELETE_CELL_CLASS = `${CELL_CLASS} text-center`
const MATCH_CELL_CLASS = `${CELL_CLASS} text-right`
const BOXED_INPUT_CLASS = "w-full px-1 py-0.5 rounded text-[11px] font-mono focus:outline-none"
const BOXED_LABEL_INPUT_CLASS = `${BOXED_INPUT_CLASS} font-semibold`
const BOXED_CELL_STYLE = { background: "var(--bg-surface)", border: "1px solid var(--border)", color: "var(--text-primary)" }
const WARNING_BOUNDARY_STYLE = { ...BOXED_CELL_STYLE, background: "var(--warning-soft)", border: "1px solid var(--warning-border-emphasis)" }
const DELETE_BUTTON_CLASS = "p-0.5 rounded transition-colors hover:text-[var(--danger)] focus-visible:text-[var(--danger)]"
const ACTION_BUTTON_CLASS = "accent-hover-btn flex size-6 items-center justify-center rounded"

function isHeaderRow(cols: string[], expected: readonly string[]): boolean {
  if (cols.length < expected.length) return false
  return expected.every((header, index) => cols[index]?.trim().toLowerCase() === header.toLowerCase())
}

function isBreakpointHeaderRow(cols: string[]): boolean {
  return isHeaderRow(cols, BREAKPOINT_COPY_HEADERS) || isHeaderRow(cols, BREAKPOINT_FIELDS)
}

function dropRecognizedHeaderRow(matrix: string[][], fieldIndex: number): string[][] {
  if (fieldIndex !== 0 || matrix.length === 0) return matrix
  return isBreakpointHeaderRow(matrix[0]) ? matrix.slice(1) : matrix
}

function applyPastedBreakpointRange(
  breakpoints: Breakpoint[],
  rowIndex: number,
  fieldIndex: number,
  matrix: string[][],
): Breakpoint[] {
  const next = [...breakpoints]

  for (let rowOffset = 0; rowOffset < matrix.length; rowOffset++) {
    const targetRow = rowIndex + rowOffset
    if (!next[targetRow]) {
      next[targetRow] = { boundary: "", label: "" }
    } else {
      next[targetRow] = { ...next[targetRow] }
    }

    for (let colOffset = 0; colOffset < matrix[rowOffset].length; colOffset++) {
      const field = BREAKPOINT_FIELDS[fieldIndex + colOffset]
      if (!field) continue
      next[targetRow][field] = matrix[rowOffset][colOffset]
    }
  }

  return next
}

function breakpointsToTsv(breakpoints: Breakpoint[]): string {
  return buildTsv([
    BREAKPOINT_COPY_HEADERS,
    ...breakpoints.map((breakpoint) => [breakpoint.boundary ?? "", breakpoint.label ?? ""]),
  ])
}

function parseBoundaryValue(boundary: string): number | null {
  const trimmed = boundary.trim()
  if (!trimmed) return null
  const parsed = Number(trimmed)
  return Number.isFinite(parsed) ? parsed : null
}

function boundaryOrderWarnings(breakpoints: Breakpoint[]): Map<number, string> {
  const warnings = new Map<number, string>()
  let highestEarlierBoundary: number | null = null

  for (let index = 0; index < breakpoints.length; index++) {
    const boundary = parseBoundaryValue(breakpoints[index].boundary)
    if (boundary === null) continue

    if (highestEarlierBoundary !== null && boundary <= highestEarlierBoundary) {
      warnings.set(index, `Breakpoint ${index + 1} is out of order; enter a value greater than ${highestEarlierBoundary}.`)
    }

    if (highestEarlierBoundary === null || boundary > highestEarlierBoundary) {
      highestEarlierBoundary = boundary
    }
  }

  return warnings
}

export function BreakpointGrid({
  breakpoints,
  onUpdate,
  rightClosed = true,
  accentColor,
  matchCounts,
}: BreakpointGridProps) {
  const addToast = useToastStore(s => s.addToast)
  const showMatches = matchCounts != null
  const orderWarnings = boundaryOrderWarnings(breakpoints)

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

  const handleCellPaste = (
    e: React.ClipboardEvent<HTMLInputElement>,
    rowIndex: number,
    fieldIndex: number,
  ) => {
    const text = e.clipboardData.getData("text/plain")
    if (!text.includes("\t") && !text.includes("\n") && !text.includes("\r")) return

    e.preventDefault()
    e.stopPropagation()

    const matrix = dropRecognizedHeaderRow(parsePastedGrid(text), fieldIndex)
    if (matrix.length === 0) return

    onUpdate(applyPastedBreakpointRange(breakpoints, rowIndex, fieldIndex, matrix))
  }

  const copyBanding = () => {
    void writeClipboardText(breakpointsToTsv(breakpoints)).catch((error: unknown) => {
      const detail = error instanceof Error ? error.message : String(error)
      addToast("error", `Could not copy banding TSV: ${detail}`)
    })
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
                breakpoints.map((bp, i) => {
                  const orderWarning = orderWarnings.get(i)
                  const orderWarningId = `breakpoint-${i + 1}-order-warning`

                  return (
                    <tr key={i}>
                      <td className={CELL_CLASS}>
                        <div className="relative">
                          <input
                            type="number"
                            aria-label={`Breakpoint ${i + 1} boundary`}
                            aria-invalid={orderWarning ? true : undefined}
                            aria-describedby={orderWarning ? orderWarningId : undefined}
                            title={orderWarning ?? undefined}
                            value={bp.boundary}
                            onChange={(e) => updateBreakpoint(i, "boundary", e.target.value)}
                            onPaste={(e) => handleCellPaste(e, i, 0)}
                            className={`${BOXED_INPUT_CLASS}${orderWarning ? " pr-5" : ""}`}
                            style={orderWarning ? WARNING_BOUNDARY_STYLE : BOXED_CELL_STYLE}
                            placeholder=""
                          />
                          {orderWarning && (
                            <span
                              id={orderWarningId}
                              role="img"
                              aria-label={orderWarning}
                              title={orderWarning}
                              className="absolute right-1 top-1/2 -translate-y-1/2"
                              style={{ color: "var(--warning-strong)" }}
                            >
                              <AlertTriangle size={12} aria-hidden="true" />
                            </span>
                          )}
                        </div>
                    </td>
                    <td className={CELL_CLASS}>
                      <input
                        type="text"
                        aria-label={`Breakpoint ${i + 1} label`}
                        value={bp.label}
                        onChange={(e) => updateBreakpoint(i, "label", e.target.value)}
                        onPaste={(e) => handleCellPaste(e, i, 1)}
                        className={BOXED_LABEL_INPUT_CLASS}
                        style={{
                          background: "var(--bg-surface)",
                          border: "1px solid var(--border)",
                          color: accentColor,
                        }}
                        placeholder=""
                      />
                    </td>
                    {showMatches && (
                      <td className={MATCH_CELL_CLASS}>
                        <span className="text-[11px] font-mono" style={{ color: "var(--text-secondary)" }}>
                          {matchCounts[i] ?? "—"}
                        </span>
                      </td>
                    )}
                    <td className={DELETE_CELL_CLASS}>
                      <button
                        aria-label="Delete breakpoint"
                        onClick={() => removeBreakpoint(i)}
                        className={DELETE_BUTTON_CLASS}
                        style={{ color: "var(--text-muted)" }}
                      >
                        <Trash size={11} />
                      </button>
                    </td>
                  </tr>
                  )
                })
              )}
            </tbody>
          </table>
        </div>
        <div className="flex items-center justify-end px-2 py-1.5" style={{ background: "var(--bg-elevated)", borderTop: "1px solid var(--border)" }}>
          <button
            type="button"
            aria-label="Copy banding as TSV"
            title="Copy banding as TSV"
            onClick={copyBanding}
            className={ACTION_BUTTON_CLASS}
            style={{ color: "var(--text-secondary)", ["--node-accent" as string]: accentColor }}
          >
            <Copy size={13} aria-hidden="true" />
          </button>
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
