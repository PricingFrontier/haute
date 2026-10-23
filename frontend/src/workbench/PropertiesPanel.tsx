/**
 * The properties panel (specs/workbench): what opens on the right, on Haute's side panel,
 * when a component on the sheet is selected. Its title, when it shows, its layout (the
 * rows a Table's grid starts with, whether they are fixed and whether the grid is
 * transposed, a Collection's columns), its
 * look (a Table's density, gridlines, header, row numbers, groups, stripes and frame, a
 * Collection's density, labels, boxes and frame, and either's text and columns' own looks), a Chart's chart
 * (`ChartSettings`), the order of the fields it shows, and the fields themselves, ticked
 * from the schema tables of its kind: many-row tables for a Table, one-row tables for a
 * Collection, any table for a Chart, which plots number columns alone, with a Collection's
 * spacers among them in its order. A grid holds one
 * kind of row, so once a Table or a Chart shows a field, tables whose rows do not line up
 * with it are greyed out. A Table's and a Collection's are in two panes, as Haute's node
 * panel puts an Explore node's in panes: Config, the fields and their order, and Display,
 * the rest; a Chart's are one list. A Heading's panel is its text and description, when it
 * shows, and its size and alignment. Opened from the showing sheet's tab instead, it is the
 * sheet's panel: its title, when it shows and its width, and deleting it, asked first in a
 * dialog of the editor's own; opened from a section's header, the
 * section's: its title, when it shows, its height, and adding a section under it or
 * deleting it.
 */
import { Rows3, Sheet } from "lucide-react"
import { SidePanel } from "../haute-ui"
import useWorkbenchFormStore from "../stores/useWorkbenchFormStore"
import useWorkbenchViewStore, { activePage } from "../stores/useWorkbenchViewStore"
import { findSection, findWidget } from "../utils/workbenchForm"
import { COMPONENT_COLOR, WIDGET_KINDS } from "./widgetKinds"
import { FieldsSettings } from "./panel/FieldsSettings"
import { HeadingSettings } from "./panel/HeadingSettings"
import { SectionSettings } from "./panel/SectionSettings"
import { SheetSettings } from "./panel/SheetSettings"

const MIN_WIDTH = 320

/** At most half the window, so the sheet stays in view. */
const maxWidth = () => Math.max(MIN_WIDTH, Math.floor(window.innerWidth / 2))

export default function PropertiesPanel() {
  const form = useWorkbenchFormStore((s) => s.form)
  const selectedId = useWorkbenchViewStore((s) => s.selectedId)
  const sheetSelected = useWorkbenchViewStore((s) => s.sheetSelected)
  const sectionId = useWorkbenchViewStore((s) => s.sectionId)
  const pageId = useWorkbenchViewStore((s) => s.pageId)
  const select = useWorkbenchViewStore((s) => s.select)
  const panelWidth = useWorkbenchViewStore((s) => s.panelWidth)
  const setPanelWidth = useWorkbenchViewStore((s) => s.setPanelWidth)
  if (form === null) return null
  const found = selectedId !== null ? findWidget(form, selectedId) : null
  const panel = { width: panelWidth, onWidthChange: setPanelWidth, minWidth: MIN_WIDTH, maxWidth, onClose: () => select(null), testId: "workbench-properties" }
  if (sheetSelected) {
    return (
      <SidePanel {...panel} title="Sheet" icon={<Sheet size={14} style={{ color: COMPONENT_COLOR }} />}>
        {/* Keyed by the sheet, so a width refused for one is never shown, or saved, for the next. */}
        <SheetSettings key={activePage(form, pageId).id} form={form} page={activePage(form, pageId)} />
      </SidePanel>
    )
  }
  const section = sectionId !== null ? findSection(form, sectionId) : null
  if (section !== null) {
    return (
      <SidePanel {...panel} title="Section" icon={<Rows3 size={14} style={{ color: COMPONENT_COLOR }} />}>
        {/* Keyed by the section, so a height refused for one is never shown, or saved, for the next. */}
        <SectionSettings key={section.section.id} form={form} page={section.page} section={section.section} index={section.index} />
      </SidePanel>
    )
  }
  if (found === null) return null
  const { label, icon: Icon, color } = WIDGET_KINDS[found.widget.type]

  return (
    <SidePanel {...panel} title={label} icon={<Icon size={14} style={{ color }} />}>
      {/* Keyed by the component, as a section's settings are by the section, so a value refused for one is never shown, or saved, for the next. */}
      {found.widget.type === "heading" ? (
        <HeadingSettings key={found.widget.id} form={form} widget={found.widget} />
      ) : (
        <FieldsSettings key={found.widget.id} form={form} widget={found.widget} />
      )}
    </SidePanel>
  )
}
