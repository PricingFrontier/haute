import PolarsCodePanel from "./shared/PolarsCodePanel"
import type { InputSource, OnUpdateConfig } from "./_shared"

export default function TransformEditor({
  config,
  onUpdate,
  inputSources,
  onDeleteInput,
  errorLine,
  upstreamColumns,
}: {
  config: Record<string, unknown>
  onUpdate: OnUpdateConfig
  inputSources: InputSource[]
  onDeleteInput?: (edgeId: string) => void
  errorLine?: number | null
  upstreamColumns?: { name: string; dtype: string }[]
}) {
  const hasInput = inputSources.length > 0
  const inputsCanFormStarter =
    hasInput
    && inputSources.every((input) => !input.frameUnresolved && input.name !== "df")
  const starterCode =
    inputsCanFormStarter ? `# df = ${inputSources[0].name}` : undefined

  return (
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
  )
}
