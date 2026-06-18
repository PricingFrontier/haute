import { PanelLeftClose } from "lucide-react"
import type { DragEvent } from "react"
import type { Node } from "@xyflow/react"
import Tooltip from "../components/Tooltip"
import NodeTypeTooltip from "../components/NodeTypeTooltip"
import { NODE_TYPE_META, PALETTE_TYPES, SINGLETON_TYPES } from "../utils/nodeTypes"
import type { NodeTypeValue } from "../utils/nodeTypes"
import { withAlpha } from "../utils/color"

function onDragStart(event: DragEvent, type: NodeTypeValue) {
  const meta = NODE_TYPE_META[type]
  event.dataTransfer.setData("application/reactflow-type", type)
  event.dataTransfer.setData("application/reactflow-config", JSON.stringify(meta.defaultConfig))
  event.dataTransfer.effectAllowed = "move"
}

export default function NodePalette({ onCollapse, nodes }: { onCollapse?: () => void; nodes?: Node[] }) {
  // Build set of singleton types already present in the graph
  const existingSingletons = new Set<string>()
  if (nodes) {
    for (const n of nodes) {
      const nt = n.data.nodeType as string
      if (nt && SINGLETON_TYPES.has(nt as NodeTypeValue)) existingSingletons.add(nt)
    }
  }

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
          const disabled = SINGLETON_TYPES.has(type) && existingSingletons.has(type)
          // Rich tooltip replaces the old native `title=` — the two must
          // never both render. The disabled-singleton message lives in the
          // card's singleton note (NodeTypeTooltip singletonBlocked).
          return (
            <Tooltip
              key={type}
              content={<NodeTypeTooltip type={type} singletonBlocked={disabled} />}
              placement="right"
            >
              {(tooltipTriggerProps) => (
                <div
                  {...tooltipTriggerProps}
                  data-testid={`node-palette-item-${type}`}
                  tabIndex={0}
                  draggable={!disabled}
                  onDragStart={(e) => { if (!disabled) onDragStart(e, type) }}
                  className={`flex items-center gap-2.5 px-2.5 py-2 rounded-lg transition-colors ${disabled ? "opacity-35 cursor-not-allowed" : "cursor-grab active:cursor-grabbing hover:bg-[var(--chrome-hover)]"}`}
                >
                  <div className="w-6 h-6 rounded-md flex items-center justify-center shrink-0" style={{ background: `${withAlpha(meta.color, 0.094)}` }}>
                    <Icon size={13} style={{ color: meta.color }} />
                  </div>
                  <span className="text-[13px] font-medium" style={{ color: "var(--text-primary)" }}>{meta.name}</span>
                </div>
              )}
            </Tooltip>
          )
        })}
      </div>
    </div>
  )
}
