import type { DragEvent } from "react"
import { PaletteColumn, PaletteHeader, PaletteItem, PaletteItems } from "../haute-ui"
import useWorkbenchStore from "../stores/useWorkbenchStore"
import { paletteWorkbenchInputConfig, paletteWorkbenchOutputConfig } from "../utils/workbenchTables"
import {
  NODE_TYPE_META,
  NODE_TYPES,
  PALETTE_TYPES,
  SINGLETON_TYPES,
  singletonSlotName,
} from "../utils/nodeTypes"
import type { NodeTypeValue } from "../utils/nodeTypes"

/** The config a node dragged from the palette starts with. */
function paletteConfig(type: NodeTypeValue): Record<string, unknown> {
  const { defaultConfig } = NODE_TYPE_META[type]
  // A Workbench Input or Output starts with the newest tables the workbench supplied.
  const { tables } = useWorkbenchStore.getState()
  if (type === NODE_TYPES.WORKBENCH_INPUT) return paletteWorkbenchInputConfig(defaultConfig, tables)
  if (type === NODE_TYPES.WORKBENCH_OUTPUT) return paletteWorkbenchOutputConfig(defaultConfig, tables)
  return defaultConfig
}

function onDragStart(event: DragEvent, type: NodeTypeValue) {
  const config = paletteConfig(type)
  event.dataTransfer.setData("application/reactflow-type", type)
  event.dataTransfer.setData("application/reactflow-config", JSON.stringify(config))
  event.dataTransfer.effectAllowed = "move"
}

export default function NodePalette({
  onCollapse,
  existingSingletonTypes = new Set<NodeTypeValue>(),
}: {
  onCollapse?: () => void
  existingSingletonTypes?: ReadonlySet<NodeTypeValue>
}) {
  // While the project's workbench is enabled, the Workbench Input takes the Quote
  // Input's place and the Workbench Output the Quote Response's (specs/workbench).
  const workbench = useWorkbenchStore((state) => state.enabled)
  const types = PALETTE_TYPES.map((type) => {
    if (workbench && type === NODE_TYPES.API_INPUT) return NODE_TYPES.WORKBENCH_INPUT
    if (workbench && type === NODE_TYPES.OUTPUT) return NODE_TYPES.WORKBENCH_OUTPUT
    return type
  })
  return (
    <PaletteColumn>
      <PaletteHeader title="Nodes" onCollapse={onCollapse} />

      <PaletteItems>
        {types.map((type) => {
          const meta = NODE_TYPE_META[type]
          const disabled = SINGLETON_TYPES.has(type) && existingSingletonTypes.has(type)
          return (
            <PaletteItem
              key={type}
              data-testid={`node-palette-item-${type}`}
              icon={meta.icon}
              label={meta.name}
              color={meta.color}
              disabled={disabled}
              draggable={!disabled}
              onDragStart={(e) => { if (!disabled) onDragStart(e, type) }}
              title={disabled ? `Only one ${singletonSlotName(type)} allowed per pipeline` : meta.description}
            />
          )
        })}
      </PaletteItems>
    </PaletteColumn>
  )
}
