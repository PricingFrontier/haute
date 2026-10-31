import { Database } from "lucide-react"
import { useState } from "react"
import { halfWindowWidth, SidePanel } from "../haute-ui"
import useWorkbenchViewStore, { SCHEMA_FULL_WIDTH } from "../stores/useWorkbenchViewStore"
import SchemaEditor from "./SchemaEditor"
import StoreSqlWindow from "./StoreSqlWindow"
import { COMPONENT_COLOR } from "./widgetKinds"

const MIN_WIDTH = 320

/**
 * At most half the window, so the sheet stays in view beside it, but never short of the
 * width its fields take at full stretch.
 */
const maxWidth = () => halfWindowWidth(SCHEMA_FULL_WIDTH)

/**
 * The schema's pane (specs/workbench): over the sheet's left part, under the sheet tabs,
 * while the toolbar's Schema has it open, on Haute's side panel opened on the left. The
 * sheet stays where it is under it. It edits the schema while building and shows it read
 * only in Preview. Its SQL, while building, opens the tables' SQL (`StoreSqlWindow`). It
 * registers itself with the view store, so nothing dragged out of the palette lands on the
 * sheet it covers.
 */
export default function SchemaPane() {
  const previewing = useWorkbenchViewStore((s) => s.mode === "preview")
  const width = useWorkbenchViewStore((s) => s.schemaWidth)
  const setWidth = useWorkbenchViewStore((s) => s.setSchemaWidth)
  const setOpen = useWorkbenchViewStore((s) => s.setSchemaOpen)
  const setPane = useWorkbenchViewStore((s) => s.setSchemaPane)
  const [storeSqlOpen, setStoreSqlOpen] = useState(false)
  return (
    <div ref={setPane} className="absolute inset-y-0 left-0 z-20 flex shadow-2xl" data-testid="schema-pane">
      <SidePanel
        side="left"
        title="Schema"
        icon={<Database size={14} style={{ color: COMPONENT_COLOR }} />}
        onClose={() => setOpen(false)}
        actions={
          <button
            type="button"
            className="btn-ghost rounded px-1.5 py-0.5 text-[11px]"
            disabled={previewing}
            onClick={() => setStoreSqlOpen(true)}
          >
            SQL
          </button>
        }
        width={width}
        onWidthChange={setWidth}
        minWidth={MIN_WIDTH}
        maxWidth={maxWidth}
      >
        <SchemaEditor readOnly={previewing} />
      </SidePanel>
      {storeSqlOpen && <StoreSqlWindow onClose={() => setStoreSqlOpen(false)} />}
    </div>
  )
}
