import { PaletteColumn, PaletteHeader, PaletteItem, PaletteItems, PaletteRevealStrip } from "../haute-ui"
import useUIStore from "../stores/useUIStore"
import { startCreate } from "./sheetInteractions"
import ViewSwitcher from "./ViewSwitcher"
import { COMPONENT_COLOR, PALETTE_KINDS, WIDGET_KINDS } from "./widgetKinds"

/**
 * The workbench's left column (specs/workbench), in Haute's palette shell: the components
 * a sheet can hold, dragged onto it, and the view switcher at the bottom. It shares the
 * node palette's open state, so collapsing it in either view collapses both; collapsed, it
 * is the reveal strip with the compact switcher under it.
 */
export default function WorkbenchPalette() {
  const open = useUIStore((s) => s.paletteOpen)
  const setPaletteOpen = useUIStore((s) => s.setPaletteOpen)
  if (!open) {
    return (
      <div className="flex shrink-0 flex-col" style={{ background: "var(--chrome)", borderRight: "1px solid var(--chrome-border)" }}>
        <div className="min-h-0 flex-1">
          <PaletteRevealStrip label="Show component palette" onReveal={() => setPaletteOpen(true)} />
        </div>
        <ViewSwitcher compact />
      </div>
    )
  }
  return (
    <PaletteColumn>
      <PaletteHeader title="Components" onCollapse={() => setPaletteOpen(false)} />
      <PaletteItems>
        {PALETTE_KINDS.map((type) => {
          const kind = WIDGET_KINDS[type]
          return (
            <PaletteItem
              key={type}
              data-testid={`palette-item-${type}`}
              icon={kind.icon}
              label={kind.label}
              color={COMPONENT_COLOR}
              title={kind.hint}
              onPointerDown={(event) => startCreate(event, type)}
            />
          )
        })}
      </PaletteItems>
      <ViewSwitcher compact={false} />
    </PaletteColumn>
  )
}
