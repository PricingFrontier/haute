import type { OnUpdateConfig } from "./_shared"
import { configField } from "../../utils/configField"
import ColumnTable from "../../components/ColumnTable"
import { EditorLabel } from "../../components/form"
import { useGraph } from "../useGraph"

export default function OutputEditor({
  config,
  onUpdate,
  nodeId,
}: {
  config: Record<string, unknown>
  onUpdate: OnUpdateConfig
  nodeId: string
}) {
  const { allNodes, edges } = useGraph()
  const fields = configField<string[]>(config, "fields", [])

  // Read cached columns from the upstream node (populated by preview/run)
  const incomingEdge = edges.find((e) => e.target === nodeId)
  const upstreamNode = incomingEdge ? allNodes.find((n) => n.id === incomingEdge.source) : null
  const upstreamColumns = ((upstreamNode?.data as Record<string, unknown>)?._columns as { name: string; dtype: string }[]) || []

  const toggleField = (col: string) => {
    const next = fields.includes(col) ? fields.filter((f) => f !== col) : [...fields, col]
    onUpdate("fields", next)
  }

  return (
    <div className="px-4 py-3 space-y-3">
      <div>
        <EditorLabel className="block mb-2">Response Fields</EditorLabel>

        {upstreamColumns.length === 0 ? (
          <div className="text-xs py-3" style={{ color: 'var(--text-muted)' }}>Preview or run the upstream node to see columns</div>
        ) : (
          <ColumnTable
            columns={upstreamColumns}
            checkbox={{
              isChecked: (name) => fields.includes(name),
              onToggle: toggleField,
              accentClass: "accent-rose-500",
            }}
            nameColor={(name) => fields.includes(name) ? "var(--text-primary)" : "var(--text-muted)"}
          />
        )}
      </div>

      {fields.length > 0 && (
        <div className="rounded-lg px-3 py-2" style={{ background: 'var(--bg-elevated)', border: '1px solid var(--border)' }}>
          <EditorLabel className="mb-1.5" as="div">JSON Preview</EditorLabel>
          <pre className="text-[11px] font-mono leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
{`{\n${fields.map((f) => `  "${f}": ...`).join(",\n")}\n}`}
          </pre>
        </div>
      )}
    </div>
  )
}
