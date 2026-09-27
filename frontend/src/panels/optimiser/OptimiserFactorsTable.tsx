import { useEffect, useId, useRef, useState, type CSSProperties } from "react"
import { Search } from "lucide-react"
import type { OnUpdateConfig } from "../editors"
import { getDtypeColor } from "../../utils/dtypeColors"
import { MAX_ANALYSIS_COLUMNS, type OptimiserInputs } from "./solveReadiness"

type Column = { name: string; dtype: string }

type OptimiserFactorsTableProps = {
  mode: string
  inputs: OptimiserInputs
  /** The validation frame's columns; empty while they are not known. */
  frameColumns: readonly Column[]
  quoteIdColumn: string
  /** The Rating Factor Source's factors and their levels. */
  bandingLevels: Record<string, string[]>
  factorColumns: readonly string[][]
  analysisColumns: readonly string[]
  onToggleRatebookFactor: (name: string) => void
  onUpdate: OnUpdateConfig
  accentColor: string
  labelClassName: string
  inputStyle: CSSProperties
}

type FactorRow = { name: string; dtype: string | null; levels: number | null }

const GRID = "grid grid-cols-[minmax(0,1fr)_4.5rem_4rem_4rem] items-center gap-2"

const VALIDATION_HELP =
  "Validation factors break the results down by segment after the solve; the solver never sees them. " +
  "Each must hold one value per quote."

function columnsFingerprint(columns: readonly Column[]): string {
  return columns.map((column) => column.name).join("\u0001")
}

/**
 * The Factors pane's factor list: one row per rating factor or validation
 * column, ticked for ratebook optimisation, validation, or both. Ratebook
 * choices exist only in ratebook mode.
 */
export default function OptimiserFactorsTable({
  mode,
  inputs,
  frameColumns,
  quoteIdColumn,
  bandingLevels,
  factorColumns,
  analysisColumns,
  onToggleRatebookFactor,
  onUpdate,
  accentColor,
  labelClassName,
  inputStyle,
}: OptimiserFactorsTableProps) {
  const selectId = useId()
  const [filter, setFilter] = useState("")
  const {
    inputNodes,
    analysisInput,
    missingExplicitAnalysisInput,
    malformedAnalysisInput,
    resolvedDataInputName,
  } = inputs
  const ratebook = mode === "ratebook"
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
  const toggleValidation = (name: string) => {
    onUpdate(
      "analysis_columns",
      analysisColumns.includes(name)
        ? analysisColumns.filter((column) => column !== name)
        : [...analysisColumns, name],
    )
  }

  // Rating factors lead in ratebook mode; the validation frame's columns follow.
  const dtypes = new Map(frameColumns.map((column) => [column.name, column.dtype]))
  const bandingNames = ratebook ? Object.keys(bandingLevels).sort() : []
  const bandingNameSet = new Set(bandingNames)
  const rows: FactorRow[] = [
    ...bandingNames.map((name) => ({
      name,
      dtype: dtypes.get(name) ?? null,
      levels: bandingLevels[name].length,
    })),
    ...frameColumns
      .filter((column) => column.name !== quoteIdColumn && !bandingNameSet.has(column.name))
      .map((column) => ({ name: column.name, dtype: column.dtype, levels: null })),
    // Until the frame's columns load, the saved validation factors still show.
    ...(frameColumns.length === 0
      ? analysisColumns
        .filter((name) => !bandingNameSet.has(name))
        .map((name) => ({ name, dtype: null, levels: null }))
      : []),
  ]
  const needle = filter.trim().toLowerCase()
  const visible = rows.filter((row) => row.name.toLowerCase().includes(needle))
  const missingColumns = frameColumns.length > 0
    ? analysisColumns.filter((column) => !dtypes.has(column))
    : []
  const atCap = analysisColumns.length >= MAX_ANALYSIS_COLUMNS
  const validationCount = `${analysisColumns.length} of ${MAX_ANALYSIS_COLUMNS} validation`

  const ratebookUnavailable = (row: FactorRow) =>
    !ratebook
      ? "Ratebook factors apply only in Ratebook mode."
      : row.levels === null
        ? "Only the Rating Factor Source's factors can be optimised."
        : null
  const frameUnknown = frameColumns.length === 0
  const validationUnavailable = (row: FactorRow, checked: boolean) =>
    row.dtype === null && !(frameUnknown && checked)
      ? frameUnknown
        ? "The validation input's columns are not known yet."
        : "Not a column of the validation input."
      : !checked && atCap
        ? `Choose at most ${MAX_ANALYSIS_COLUMNS} validation factors.`
        : null

  return (
    <section aria-labelledby="optimiser-factors-heading">
      <div className="flex items-end justify-between gap-3">
        <h3 id="optimiser-factors-heading" className={labelClassName} style={{ color: "var(--text-muted)" }}>
          Factors
        </h3>
        <span className="text-xs tabular-nums" style={{ color: "var(--text-secondary)" }}>
          {ratebook ? `${factorColumns.length} ratebook · ${validationCount}` : validationCount}
        </span>
      </div>
      <p className="mt-1 text-[11px]" style={{ color: "var(--text-muted)" }}>{VALIDATION_HELP}</p>

      <div className="mt-2">
        <label htmlFor={selectId} className="text-[11px]" style={{ color: "var(--text-muted)" }}>
          Validation input
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
              ? "The configured validation input must be an input name."
              : "The configured validation input is not connected."}
          </div>
        )}
      </div>

      <div className="sticky top-0 z-10 mt-2 py-1" style={{ background: "var(--bg-panel)" }}>
        <div className="relative">
          <Search
            aria-hidden="true"
            className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2"
            size={13}
            style={{ color: "var(--text-muted)" }}
          />
          <input
            aria-label="Search factors"
            className="w-full rounded-lg py-2 pl-8 pr-2.5 text-xs outline-none"
            style={inputStyle}
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
            placeholder="Search factors"
          />
        </div>
      </div>

      {frameUnknown && rows.length > 0 && (
        <p className="mt-1 text-[11px]" style={{ color: "var(--text-muted)" }}>
          The validation input's columns are not known yet.
        </p>
      )}
      <div className={`mt-2 ${GRID} text-[11px]`} style={{ color: "var(--text-muted)" }}>
        <span>Factor</span>
        <span>Type</span>
        <span
          className={`text-center${ratebook ? "" : " opacity-40"}`}
          title={ratebook ? undefined : "Ratebook factors apply only in Ratebook mode."}
        >
          Ratebook
        </span>
        <span className="text-center">Validation</span>
      </div>
      <div role="group" aria-label="Factors" className="mt-1 divide-y divide-[var(--border)]">
        {visible.map((row) => {
          const ratebookChecked = ratebook && factorColumns.some((group) => group.length === 1 && group[0] === row.name)
          const validationChecked = analysisColumns.includes(row.name)
          const ratebookReason = ratebookUnavailable(row)
          const validationReason = validationUnavailable(row, validationChecked)
          return (
            <div key={row.name} role="group" aria-label={`${row.name} factor`} className={`${GRID} min-w-0 py-2`}>
              <span className="flex min-w-0 items-baseline gap-1.5">
                <span
                  className="min-w-0 truncate font-mono text-[13px] font-semibold"
                  title={row.name}
                  style={{ color: "var(--text-primary)" }}
                >
                  {row.name}
                </span>
                {row.levels !== null && (
                  <span className="shrink-0 text-[11px]" style={{ color: "var(--text-muted)" }}>
                    {row.levels} levels
                  </span>
                )}
              </span>
              {row.dtype !== null ? (
                <span
                  className={`max-w-full justify-self-start truncate rounded-full px-1.5 py-0.5 font-mono text-[11px] ${getDtypeColor(row.dtype)}`}
                  title={row.dtype}
                  style={{ background: "var(--chrome-hover)" }}
                >
                  {row.dtype}
                </span>
              ) : (
                <span aria-hidden="true" />
              )}
              <span className="flex justify-center" title={ratebookReason ?? undefined}>
                <input
                  type="checkbox"
                  aria-label={`Use ${row.name} for ratebook optimisation`}
                  checked={ratebookChecked}
                  disabled={ratebookReason !== null}
                  onChange={() => onToggleRatebookFactor(row.name)}
                  className="disabled:cursor-not-allowed disabled:opacity-40"
                  style={{ accentColor }}
                />
              </span>
              <span className="flex justify-center" title={validationReason ?? undefined}>
                <input
                  type="checkbox"
                  aria-label={`Use ${row.name} for validation`}
                  checked={validationChecked}
                  disabled={validationReason !== null}
                  onChange={() => toggleValidation(row.name)}
                  className="disabled:cursor-not-allowed disabled:opacity-40"
                  style={{ accentColor }}
                />
              </span>
            </div>
          )
        })}
        {visible.length === 0 && (
          <p
            className="rounded-lg border border-dashed px-3 py-5 text-center text-[11px]"
            style={{ color: "var(--text-muted)", borderColor: "var(--border)" }}
          >
            {rows.length === 0 ? "The validation input's columns are not known yet." : "No matching factors."}
          </p>
        )}
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
          <span>“{column}” is not a column of the validation input.</span>
          <button
            type="button"
            onClick={() => toggleValidation(column)}
            className="underline"
            aria-label={`Remove validation factor ${column}`}
          >
            Remove
          </button>
        </div>
      ))}
    </section>
  )
}
