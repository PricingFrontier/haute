import { PaletteColumn, PaletteHeader, PaletteItem, PaletteItems, PaletteRevealStrip } from "../haute-ui"
import useUIStore from "../stores/useUIStore"
import useWorkbenchViewStore from "../stores/useWorkbenchViewStore"
import { createWithoutPointer, startCreate } from "./sheetInteractions"
import ViewSwitcher from "./ViewSwitcher"
import { PALETTE_KINDS, paletteKind } from "./widgetKinds"

/**
 * The workbench's left column (specs/workbench), in Haute's palette shell: the components
 * a sheet can hold and a section, dragged onto it while building, or added by Enter (or
 * Space) on the item, a component then taking the focus, and disabled in Preview, and the
 * view switcher at the bottom. It shares the node palette's open state, so collapsing it in
 * either view collapses both; collapsed, it is the reveal strip with the compact switcher
 * under it.
 */
export default function WorkbenchPalette() {
  const open = useUIStore((s) => s.paletteOpen)
  const setPaletteOpen = useUIStore((s) => s.setPaletteOpen)
  const previewing = useWorkbenchViewStore((s) => s.mode === "preview")
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
          const kind = paletteKind(type)
          return (
            <PaletteItem
              key={type}
              data-testid={`palette-item-${type}`}
              icon={kind.icon}
              label={kind.label}
              color={kind.color}
              disabled={previewing}
              title={previewing ? "Build to lay out the sheets" : kind.hint}
              role="button"
              tabIndex={previewing ? -1 : 0}
              aria-keyshortcuts={previewing ? undefined : "Enter"}
              onPointerDown={previewing ? undefined : (event) => startCreate(event, type)}
              onKeyDown={
                previewing
                  ? undefined
                  : (event) => {
                      if (event.key !== "Enter" && event.key !== " ") return
                      event.preventDefault()
                      const id = createWithoutPointer(type)
                      // Once drawn, the new component takes the focus, so the arrows move it.
                      if (id !== null) requestAnimationFrame(() => document.querySelector<HTMLElement>(`[data-widget="${id}"]`)?.focus())
                    }
              }
            />
          )
        })}
      </PaletteItems>
      <ViewSwitcher compact={false} />
    </PaletteColumn>
  )
}
