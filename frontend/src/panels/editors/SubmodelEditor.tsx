import { Package } from "lucide-react"
import { EditorLabel } from "../../components/form"
import useGraphStore from "../../stores/useGraphStore"
import { isSubmodelDefinition, isSubmodelInstanceConfig } from "../../types/node"
import { withAlpha } from "../../utils/color"

interface SubmodelEditorProps {
  config: Record<string, unknown>
  accentColor: string
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

export default function SubmodelEditor({ config, accentColor }: SubmodelEditorProps) {
  const definition = useGraphStore((state) =>
    isSubmodelInstanceConfig(config) ? state.submodels[config.definitionId] : undefined,
  )
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
          <div className="mt-1 flex flex-wrap gap-1.5">
            {definition.inputPorts.map((port) => (
              <PortBadge key={port.name} {...port} accentColor={accentColor} />
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

      <div className="pt-1 text-[11px]" style={{ color: "var(--text-muted)" }}>
        Double-click to view internal nodes
      </div>
    </div>
  )
}
