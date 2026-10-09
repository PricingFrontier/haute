import type { DragEvent } from "react"
import { PaletteColumn, PaletteHeader, PaletteItem, PaletteItems } from "../haute-ui"
import useExtensionsStore from "../stores/useExtensionsStore"
import { paletteWorkbenchInputConfig, quoteTablesSupplier } from "../utils/extensionQuoteTables"
import {
  NODE_TYPE_META,
  NODE_TYPES,
  PALETTE_TYPES,
  SINGLETON_TYPES,
  isRequestInputType,
} from "../utils/nodeTypes"
import type { NodeTypeValue } from "../utils/nodeTypes"

function onDragStart(event: DragEvent, type: NodeTypeValue) {
  const meta = NODE_TYPE_META[type]
  // A Workbench Input starts with the newest tables the extension supplied.
  const config = type === NODE_TYPES.WORKBENCH_INPUT
    ? paletteWorkbenchInputConfig(meta.defaultConfig, useExtensionsStore.getState().quoteTables)
    : meta.defaultConfig
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
  // While an installed extension supplies the quote's tables, the Workbench Input
  // takes the Quote Input's place (specs/extensions).
  const workbench = useExtensionsStore((state) => quoteTablesSupplier(state.extensions) !== null)
  const types = PALETTE_TYPES.map((type) =>
    workbench && type === NODE_TYPES.API_INPUT ? NODE_TYPES.WORKBENCH_INPUT : type)
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
              title={disabled
                ? `Only one ${isRequestInputType(type) ? "Quote Input or Workbench Input" : meta.name} allowed per pipeline`
                : meta.description}
            />
          )
        })}
      </PaletteItems>
    </PaletteColumn>
  )
}
