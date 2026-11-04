import type { NodeEditorState } from "../../types/node"
import SteppedCodePane from "./shared/SteppedCodePane"
import type { InputSource, OnUpdateConfig } from "./_shared"

/**
 * Transform node editor: the shared stepped-code pane in `input` mode (the
 * first step chooses an input), with the transform's code hint and starter
 * code for the code box.
 */
export default function TransformEditor({
  config,
  onUpdate,
  inputSources,
  onDeleteInput,
  errorLine,
  errorRevision,
  runError,
  upstreamColumns,
  nodeId,
  nodeColumns,
  editor,
}: {
  config: Record<string, unknown>
  onUpdate: OnUpdateConfig
  inputSources: InputSource[]
  onDeleteInput?: (edgeId: string) => void
  errorLine?: number | null
  /** Changes with each run's result, so a repeat error line is marked again. */
  errorRevision?: unknown
  /** The last run's error message for this node, if it failed. */
  runError?: string | null
  upstreamColumns?: { name: string; dtype: string }[]
  nodeId: string
  /** The node's own columns as its last preview recorded them. */
  nodeColumns?: { name: string; dtype: string }[]
  /** The server's editor state for the node, beside its config. */
  editor?: NodeEditorState
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
      inputSources={inputSources}
      inputNames={inputSources.map((source) => source.name)}
      onDeleteInput={onDeleteInput}
      errorLine={errorLine}
      errorRevision={errorRevision}
      runError={runError}
      upstreamColumns={upstreamColumns}
      nodeColumns={nodeColumns}
      start="input"
      nodeId={nodeId}
      codeHint={hasInput ? "use input names, assign to df" : "assign to df"}
      starterCode={starterCode}
      editor={editor}
    />
  )
}
