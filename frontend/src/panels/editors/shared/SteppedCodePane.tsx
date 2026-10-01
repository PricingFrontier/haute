import { AlertTriangle } from "lucide-react"
import { useMemo, type ReactNode } from "react"

import type { StepStart } from "../../../utils/polarsStepInputs"
import PolarsStepsEditor from "../polarsSteps/PolarsStepsEditor"
import { readSteps } from "../polarsSteps/types"
import type { InputSource, OnReplaceConfig, OnUpdateConfig } from "../_shared"
import PolarsCodePanel from "./PolarsCodePanel"

type ColumnInfo = { name: string; dtype: string }

export type SteppedCodePaneProps = {
  config: Record<string, unknown>
  onUpdate: OnUpdateConfig
  onReplaceConfig?: OnReplaceConfig
  /** The input chips on display. */
  inputSources: InputSource[]
  /** The input names the steps may reference: the surface's eligibility, not the chips. */
  inputNames: string[]
  onDeleteInput?: (edgeId: string) => void
  errorLine?: number | null
  /** The last run's error message for this node, if it failed. */
  runError?: string | null
  /** The node's input columns, as the previews of its inputs recorded them. */
  upstreamColumns?: ColumnInfo[]
  /** The node's own columns as its last preview recorded them, before its own selection and renames. */
  nodeColumns?: ColumnInfo[]
  /** `input` for a Transform (the first step chooses an input), `frame` when `df` is already bound. */
  start: StepStart
  /** The code box's hint (code mode). */
  codeHint: ReactNode
  /** Selectable, non-persisted initial text for an empty code box (code mode). */
  starterCode?: string
}

/**
 * One Polars surface, two authoring modes: the step builder while
 * `config.steps` is a list, otherwise the free-text code box (with the
 * discard notice when the parser dropped a stale step list on load).
 */
export default function SteppedCodePane({
  config,
  onUpdate,
  onReplaceConfig,
  inputSources,
  inputNames,
  onDeleteInput,
  errorLine,
  runError,
  upstreamColumns,
  nodeColumns,
  start,
  codeHint,
  starterCode,
}: SteppedCodePaneProps) {
  const codeColumns = useMemo(() => mergeColumns(upstreamColumns, nodeColumns), [upstreamColumns, nodeColumns])

  if (readSteps(config) !== null) {
    return (
      <PolarsStepsEditor
        config={config}
        onUpdate={onUpdate}
        onReplaceConfig={onReplaceConfig}
        inputSources={inputSources}
        inputNames={inputNames}
        onDeleteInput={onDeleteInput}
        errorLine={errorLine}
        runError={runError}
        frameColumns={codeColumns}
        start={start}
      />
    )
  }

  const discarded = typeof config._steps_discarded === "string" ? config._steps_discarded : null

  return (
    <>
      {discarded && (
        <div
          role="status"
          data-testid="polars-steps-discarded"
          className="mx-3 mt-2 flex items-start gap-2 rounded-lg px-3 py-2 text-[11px] leading-snug"
          style={{ color: "var(--warning)", background: "var(--bg-input)", border: "1px solid var(--warning)" }}
        >
          <AlertTriangle size={12} aria-hidden="true" className="mt-0.5 shrink-0" />
          <span>{discarded}</span>
        </div>
      )}
      <PolarsCodePanel
        config={config}
        onUpdate={onUpdate}
        inputSources={inputSources}
        onDeleteInput={onDeleteInput}
        errorLine={errorLine}
        codeColumns={codeColumns}
        hint={codeHint}
        starterCode={starterCode}
      />
    </>
  )
}

/**
 * The columns the pane's code can name: the node's input columns, then its own
 * (what it scores, rates, expands or reads, and what its code creates), each
 * name once with its input type.
 */
function mergeColumns(upstreamColumns: ColumnInfo[] = [], nodeColumns: ColumnInfo[] = []): ColumnInfo[] {
  const named = new Set(upstreamColumns.map((column) => column.name))
  return [...upstreamColumns, ...nodeColumns.filter((column) => !named.has(column.name))]
}
