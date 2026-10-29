import { Package } from "lucide-react"
import { EditorLabel } from "../../components/form"
import useGraphStore from "../../stores/useGraphStore"
import { isSubmodelDefinition, isSubmodelInstanceConfig } from "../../types/node"
import { withAlpha } from "../../utils/color"
import { retargetSubmodelInput } from "../../utils/canonicalSubmodelBoundaryEditing"
import { edgeInputName } from "../../utils/apiInputPorts"
import type { SimpleNode } from "./_shared"
import useToastStore from "../../stores/useToastStore"

interface SubmodelEditorProps {
  nodeId: string
  config: Record<string, unknown>
  accentColor: string
  readOnly?: boolean
}

interface PortBadgeProps {
  name: string
  accentColor: string
}

function PortBadge({ name, accentColor }: PortBadgeProps) {
  return (
    <span
      className="inline-flex items-center rounded px-2 py-0.5 font-mono text-[11px]"
      style={{ background: withAlpha(accentColor, 0.1), color: accentColor }}
    >
      {name}
    </span>
  )
}

export default function SubmodelEditor({ nodeId, config, accentColor, readOnly = false }: SubmodelEditorProps) {
  const nodes = useGraphStore((state) => state.nodes)
  const edges = useGraphStore((state) => state.edges)
  const definition = useGraphStore((state) =>
    isSubmodelInstanceConfig(config) ? state.submodels[config.definitionId] : undefined,
  )
  const incoming = edges.filter((edge) => edge.target === nodeId)
  const retarget = (port: string, edgeId: string) => {
    if (readOnly) return
    try {
      const state = useGraphStore.getState()
      const next = retargetSubmodelInput(state, nodeId, port, edgeId || null)
      state.setNodesAndEdgesAndSubmodels(next.nodes, next.edges, next.submodels)
    } catch (error: unknown) {
      useToastStore.getState().addToast("error", error instanceof Error ? error.message : String(error))
    }
  }
  if (!isSubmodelInstanceConfig(config) || !isSubmodelDefinition(definition, config.definitionId)) {
    return (
      <div role="alert" className="px-4 py-3 text-xs" style={{ color: "var(--danger)" }}>
        Submodel occurrence or definition is invalid.
      </div>
    )
  }

  return (
    <div className="space-y-3 px-4 py-3">
      <div
        className="flex items-center gap-2 rounded-lg px-2.5 py-2"
        style={{
          background: withAlpha(accentColor, 0.08),
          border: `1px solid ${withAlpha(accentColor, 0.2)}`,
        }}
      >
        <Package size={14} style={{ color: accentColor }} />
        <span className="text-xs font-medium" style={{ color: accentColor }}>Submodel</span>
        <span className="ml-auto font-mono text-[11px]" style={{ color: "var(--text-muted)" }}>
          {definition.graph.nodes.length} nodes
        </span>
      </div>

      <div>
        <EditorLabel>File</EditorLabel>
        <div
          className="mt-1 rounded-lg px-2.5 py-1.5 font-mono text-xs"
          style={{
            background: "var(--bg-input)",
            border: "1px solid var(--border)",
            color: "var(--text-secondary)",
          }}
        >
          {definition.file}
        </div>
      </div>

      {definition.inputPorts.length > 0 && (
        <div>
          <EditorLabel>Inputs</EditorLabel>
          <div className="mt-1 space-y-2">
            {definition.inputPorts.map((port) => (
              <label key={port.name} className="flex items-center justify-between gap-2 text-xs">
                <span>{port.name}</span>
                <select
                  aria-label={port.name}
                  disabled={readOnly}
                  value={incoming.find((edge) => edge.targetHandle === `in__${port.name}`)?.id ?? ""}
                  onChange={(event) => retarget(port.name, event.target.value)}
                  className="min-w-0 rounded px-2 py-1"
                  style={{ background: "var(--bg-input)", color: "var(--text-primary)", border: "1px solid var(--border)" }}
                >
                  <option value="">not connected</option>
                  {incoming.map((edge) => {
                    const source = nodes.find((node) => node.id === edge.source)
                    if (!source) throw new Error(`Connection ${edge.id} has no source ${edge.source}`)
                    return <option key={edge.id} value={edge.id}>{edgeInputName(edge, source as unknown as SimpleNode)}</option>
                  })}
                </select>
              </label>
            ))}
          </div>
        </div>
      )}

      {definition.outputPorts.length > 0 && (
        <div>
          <EditorLabel>Outputs</EditorLabel>
          <div className="mt-1 flex flex-wrap gap-1.5">
            {definition.outputPorts.map((port) => (
              <PortBadge key={port.name} {...port} accentColor={accentColor} />
            ))}
          </div>
        </div>
      )}

    </div>
  )
}
