import SteppedCodePane from "./shared/SteppedCodePane"
import type { InputSource, OnReplaceConfig, OnUpdateConfig } from "./_shared"

/**
 * Transform node editor: the shared stepped-code pane in `input` mode (the
 * first step chooses an input), with the transform's code hint and starter
 * code for the code box.
 */
export default function TransformEditor({
  config,
  onUpdate,
  onReplaceConfig,
  inputSources,
  onDeleteInput,
  errorLine,
  runError,
  upstreamColumns,
}: {
  config: Record<string, unknown>
  onUpdate: OnUpdateConfig
  onReplaceConfig?: OnReplaceConfig
  inputSources: InputSource[]
  onDeleteInput?: (edgeId: string) => void
  errorLine?: number | null
  /** The last run's error message for this node, if it failed. */
  runError?: string | null
  upstreamColumns?: { name: string; dtype: string }[]
}) {
  const hasInput = inputSources.length > 0
  const inputsCanFormStarter =
    hasInput
    && inputSources.every((input) => !input.frameUnresolved && input.name !== "df")
  const starterCode =
    inputsCanFormStarter ? `# df = ${inputSources[0].name}` : undefined

  return (
    <SteppedCodePane
      config={config}
      onUpdate={onUpdate}
      onReplaceConfig={onReplaceConfig}
      inputSources={inputSources}
      inputNames={inputSources.map((source) => source.name)}
      onDeleteInput={onDeleteInput}
      errorLine={errorLine}
      runError={runError}
      upstreamColumns={upstreamColumns}
      start="input"
      codeHint={hasInput ? "use input names, assign to df" : "assign to df"}
      starterCode={starterCode}
    />
  )
}
