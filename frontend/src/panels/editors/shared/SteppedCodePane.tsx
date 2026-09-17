import { AlertTriangle } from "lucide-react"
import type { ReactNode } from "react"

import type { StepStart } from "../../../utils/polarsStepInputs"
import PolarsStepsEditor from "../polarsSteps/PolarsStepsEditor"
import { readSteps } from "../polarsSteps/types"
import type { InputSource, OnReplaceConfig, OnUpdateConfig } from "../_shared"
import PolarsCodePanel from "./PolarsCodePanel"

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
  upstreamColumns?: { name: string; dtype: string }[]
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
  start,
  codeHint,
  starterCode,
}: SteppedCodePaneProps) {
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
        upstreamColumns={upstreamColumns}
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
        upstreamColumns={upstreamColumns}
        hint={codeHint}
        starterCode={starterCode}
      />
    </>
  )
}
