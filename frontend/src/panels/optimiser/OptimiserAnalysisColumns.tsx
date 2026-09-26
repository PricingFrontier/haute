import { useEffect, useId, useRef, type CSSProperties } from "react"
import type { OnUpdateConfig } from "../editors"
import { MAX_ANALYSIS_COLUMNS, type OptimiserInputs } from "./solveReadiness"

type Column = { name: string; dtype: string }

type OptimiserAnalysisColumnsProps = {
  inputs: OptimiserInputs
  /** The chosen analysis frame's columns; empty while they are not known. */
  frameColumns: readonly Column[]
  quoteIdColumn: string
  analysisColumns: readonly string[]
  onUpdate: OnUpdateConfig
  labelClassName: string
  inputStyle: CSSProperties
}

const HELP =
  "Used only to break results down by segment after the solve; the solver never sees them. " +
  "Each column must hold one value per quote."

function columnsFingerprint(columns: readonly Column[]): string {
  return columns.map((column) => column.name).join("\u0001")
}

/**
 * The Data pane's Analysis columns section (OPT-V09A): which connected frame
 * the analysis columns come from, and which of its columns to keep.
 */
export default function OptimiserAnalysisColumns({
  inputs,
  frameColumns,
  quoteIdColumn,
  analysisColumns,
  onUpdate,
  labelClassName,
  inputStyle,
}: OptimiserAnalysisColumnsProps) {
  const selectId = useId()
  const {
    inputNodes,
    analysisInput,
    missingExplicitAnalysisInput,
    malformedAnalysisInput,
    resolvedDataInputName,
  } = inputs
  const otherInputs = inputNodes.filter((input) => input.name !== resolvedDataInputName)
  const dataInputLabel = resolvedDataInputName
    ? `${resolvedDataInputName} (Objectives & Constraints input)`
    : "Objectives & Constraints input"

  // Columns the newly chosen frame lacks are removed once, in response to the
  // user's switch and only when that frame's columns have arrived. Loading a
  // node never rewrites its configuration.
  const pendingPrune = useRef<{ input: string; staleColumns: string } | null>(null)
  const frameFingerprint = columnsFingerprint(frameColumns)
  useEffect(() => {
    const pending = pendingPrune.current
    if (!pending || pending.input !== analysisInput) return
    if (frameColumns.length === 0 || frameFingerprint === pending.staleColumns) return
    pendingPrune.current = null
    const available = new Set(frameColumns.map((column) => column.name))
    const kept = analysisColumns.filter((column) => available.has(column))
    if (kept.length !== analysisColumns.length) onUpdate("analysis_columns", kept)
  }, [analysisInput, analysisColumns, frameColumns, frameFingerprint, onUpdate])

  const chooseInput = (name: string) => {
    pendingPrune.current = { input: name, staleColumns: frameFingerprint }
    onUpdate("analysis_input", name || undefined)
  }
  const toggleColumn = (name: string) => {
    onUpdate(
      "analysis_columns",
      analysisColumns.includes(name)
        ? analysisColumns.filter((column) => column !== name)
        : [...analysisColumns, name],
    )
  }

  const choices = frameColumns.filter((column) => column.name !== quoteIdColumn)
  const available = new Set(frameColumns.map((column) => column.name))
  const missingColumns = frameColumns.length > 0
    ? analysisColumns.filter((column) => !available.has(column))
    : []
  const atCap = analysisColumns.length >= MAX_ANALYSIS_COLUMNS

  return (
    <div>
      <label className={labelClassName} style={{ color: "var(--text-muted)" }}>Analysis columns</label>
      <p className="mt-1 text-[11px]" style={{ color: "var(--text-muted)" }}>{HELP}</p>
      <div className="mt-1.5">
        <label htmlFor={selectId} className="text-[11px]" style={{ color: "var(--text-muted)" }}>
          Analysis input
        </label>
        <select
          id={selectId}
          value={analysisInput}
          onChange={(event) => chooseInput(event.target.value)}
          className="w-full mt-0.5 px-2.5 py-1.5 rounded-lg text-xs"
          style={inputStyle}
        >
          <option value="">{dataInputLabel}</option>
          {missingExplicitAnalysisInput && analysisInput && (
            <option value={analysisInput}>Missing input</option>
          )}
          {otherInputs.map((input) => (
            <option key={input.name} value={input.name}>{input.label}</option>
          ))}
        </select>
        {missingExplicitAnalysisInput && (
          <div
            className="mt-1 px-2.5 py-1.5 rounded text-[11px]"
            style={{
              color: "var(--warning-strong)",
              background: "var(--warning-soft)",
              border: "1px solid var(--warning-border)",
            }}
          >
            {malformedAnalysisInput
              ? "The configured analysis input must be an input name."
              : "The configured analysis input is not connected."}
          </div>
        )}
      </div>
      <div className="mt-2">
        <div className="flex items-baseline justify-between text-[11px]" style={{ color: "var(--text-muted)" }}>
          <span>Columns</span>
          <span>{analysisColumns.length} of {MAX_ANALYSIS_COLUMNS} chosen</span>
        </div>
        <div role="group" aria-label="Analysis columns" className="mt-1 space-y-0.5">
          {choices.length === 0 ? (
            <div className="text-[11px] py-1" style={{ color: "var(--text-muted)" }}>
              The analysis input's columns are not known yet.
            </div>
          ) : choices.map((column) => {
            const checked = analysisColumns.includes(column.name)
            return (
              <label key={column.name} className="flex items-center gap-2 text-xs font-mono">
                <input
                  type="checkbox"
                  name={column.name}
                  checked={checked}
                  disabled={!checked && atCap}
                  onChange={() => toggleColumn(column.name)}
                />
                {column.name}
              </label>
            )
          })}
        </div>
        {missingColumns.map((column) => (
          <div
            key={column}
            className="mt-1 flex items-center justify-between gap-2 px-2.5 py-1.5 rounded text-[11px]"
            style={{
              color: "var(--warning-strong)",
              background: "var(--warning-soft)",
              border: "1px solid var(--warning-border)",
            }}
          >
            <span>“{column}” is not a column of the analysis input.</span>
            <button
              type="button"
              onClick={() => toggleColumn(column)}
              className="underline"
              aria-label={`Remove analysis column ${column}`}
            >
              Remove
            </button>
          </div>
        ))}
      </div>
    </div>
  )
}
