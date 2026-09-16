import { AlertTriangle } from "lucide-react"

import PolarsStepsEditor from "./polarsSteps/PolarsStepsEditor"
import { readSteps } from "./polarsSteps/types"
import PolarsCodePanel from "./shared/PolarsCodePanel"
import type { InputSource, OnReplaceConfig, OnUpdateConfig } from "./_shared"

/**
 * Transform node editor: the low-code step builder while `config.steps` is a
 * list, otherwise the free-text Polars code box (with the discard notice when
 * the parser dropped a stale step list).
 */
export default function TransformEditor({
  config,
  onUpdate,
  onReplaceConfig,
  inputSources,
  onDeleteInput,
  errorLine,
  upstreamColumns,
}: {
  config: Record<string, unknown>
  onUpdate: OnUpdateConfig
  onReplaceConfig?: OnReplaceConfig
  inputSources: InputSource[]
  onDeleteInput?: (edgeId: string) => void
  errorLine?: number | null
  upstreamColumns?: { name: string; dtype: string }[]
}) {
  if (readSteps(config) !== null) {
    return (
      <PolarsStepsEditor
        config={config}
        onUpdate={onUpdate}
        onReplaceConfig={onReplaceConfig}
        inputSources={inputSources}
        onDeleteInput={onDeleteInput}
        errorLine={errorLine}
        upstreamColumns={upstreamColumns}
      />
    )
  }

  const hasInput = inputSources.length > 0
  const inputsCanFormStarter =
    hasInput
    && inputSources.every((input) => !input.frameUnresolved && input.name !== "df")
  const starterCode =
    inputsCanFormStarter ? `# df = ${inputSources[0].name}` : undefined
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
        hint={hasInput ? "use input names, assign to df" : "assign to df"}
        starterCode={starterCode}
      />
    </>
  )
}
