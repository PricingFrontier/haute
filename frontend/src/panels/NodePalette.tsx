import { PanelLeftClose } from "lucide-react"
import type { DragEvent } from "react"
import { NODE_TYPE_META, PALETTE_TYPES, SINGLETON_TYPES } from "../utils/nodeTypes"
import type { NodeTypeValue } from "../utils/nodeTypes"

function onDragStart(event: DragEvent, type: NodeTypeValue) {
  const meta = NODE_TYPE_META[type]
  event.dataTransfer.setData("application/reactflow-type", type)
  event.dataTransfer.setData("application/reactflow-config", JSON.stringify(meta.defaultConfig))
  event.dataTransfer.effectAllowed = "move"
}

export default function NodePalette({
  onCollapse,
  existingSingletonTypes = new Set<NodeTypeValue>(),
}: {
  onCollapse?: () => void
  existingSingletonTypes?: ReadonlySet<NodeTypeValue>
}) {
  return (
    <div className="w-[180px] h-full overflow-y-auto shrink-0 flex flex-col" style={{ background: "var(--chrome)", borderRight: "1px solid var(--chrome-border)" }}>
      <div className="px-4 py-3 flex items-center justify-between">
        <h2 className="text-[11px] font-bold uppercase tracking-[0.1em]" style={{ color: "var(--text-muted)" }}>Nodes</h2>
        {onCollapse && (
          <button
            onClick={onCollapse}
            className="p-0.5 rounded transition-colors hover:bg-[var(--chrome-hover)] hover:text-[var(--text-secondary)]"
            style={{ color: 'var(--text-muted)' }}
            title="Collapse palette"
          >
            <PanelLeftClose size={14} />
          </button>
        )}
      </div>

      <div className="px-2 space-y-0.5 flex-1">
        {PALETTE_TYPES.map((type) => {
          const meta = NODE_TYPE_META[type]
          const Icon = meta.icon
          const disabled = SINGLETON_TYPES.has(type) && existingSingletonTypes.has(type)
          return (
            <div
              key={type}
              data-testid={`node-palette-item-${type}`}
              draggable={!disabled}
              onDragStart={(e) => { if (!disabled) onDragStart(e, type) }}
              className={`flex items-center gap-2.5 px-2.5 py-2 rounded-lg transition-colors ${disabled ? "opacity-35 cursor-not-allowed" : "cursor-grab active:cursor-grabbing hover:bg-[var(--chrome-hover)]"}`}
              title={disabled ? `Only one ${meta.name} allowed per pipeline` : meta.description}
            >
              <div className="w-6 h-6 rounded-md flex items-center justify-center shrink-0" style={{ background: `${meta.color}18` }}>
                <Icon size={13} style={{ color: meta.color }} />
              </div>
              <span className="text-[13px] font-medium" style={{ color: "var(--text-primary)" }}>{meta.name}</span>
            </div>
          )
        })}
      </div>
    </div>
  )
}
