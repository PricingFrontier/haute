import { Database, Sheet } from "lucide-react"
import ProjectControls from "../components/ProjectControls"
import { ToolbarBrand, ToolbarButton, ToolbarColumn, UndoRedo, ZoomInOut } from "../haute-ui"
import useWorkbenchFormStore from "../stores/useWorkbenchFormStore"
import useWorkbenchPricingStore from "../stores/useWorkbenchPricingStore"
import useWorkbenchViewStore, { ZOOM_STEP } from "../stores/useWorkbenchViewStore"

declare const __APP_VERSION__: string

interface WorkbenchToolbarProps {
  /** Commit: the host's milestone flow, which saves the form's unsaved edits first. */
  onCommit: () => void
}

/**
 * The toolbar while the workbench's view shows (specs/workbench): the brand, the view's
 * sections (Sheets over Schema), the form's Undo and Redo, Zoom In over Zoom Out while the
 * sheets show, why pricing the sample last failed, and the project's controls, whose Save
 * saves the form and whose Commit records a milestone through the host, the form and the
 * pipeline saved first.
 */
export default function WorkbenchToolbar({ onCommit }: WorkbenchToolbarProps) {
  const ready = useWorkbenchFormStore((s) => s.status === "ready")
  const canUndo = useWorkbenchFormStore((s) => s.undoStack.length > 0)
  const canRedo = useWorkbenchFormStore((s) => s.redoStack.length > 0)
  const saving = useWorkbenchFormStore((s) => s.saving)
  const undo = useWorkbenchFormStore((s) => s.undo)
  const redo = useWorkbenchFormStore((s) => s.redo)
  const save = useWorkbenchFormStore((s) => s.save)
  const section = useWorkbenchViewStore((s) => s.section)
  const showSection = useWorkbenchViewStore((s) => s.showSection)
  const zoomBy = useWorkbenchViewStore((s) => s.zoomBy)
  const pricingError = useWorkbenchPricingStore((s) => s.error)

  return (
    <header role="toolbar" aria-label="Workbench toolbar" className="toolbar flex-wrap gap-y-2 [&>div]:shrink-0">
      <ToolbarBrand name="haute" version={__APP_VERSION__} />
      <div className="ml-2.5 flex flex-wrap items-center gap-2.5" data-testid="workbench-toolbar-actions">
        <ToolbarColumn role="group" aria-label="Section">
          <ToolbarButton icon={Sheet} aria-pressed={section === "sheets"} onClick={() => showSection("sheets")} title="Lay out the sheets">
            Sheets
          </ToolbarButton>
          <ToolbarButton icon={Database} aria-pressed={section === "schema"} onClick={() => showSection("schema")} title="The tables the sheets work with">
            Schema
          </ToolbarButton>
        </ToolbarColumn>
        <UndoRedo canUndo={ready && canUndo} canRedo={ready && canRedo} onUndo={undo} onRedo={redo} />
        {section === "sheets" && <ZoomInOut onZoomIn={() => zoomBy(ZOOM_STEP)} onZoomOut={() => zoomBy(-ZOOM_STEP)} />}
        {pricingError !== null && (
          <span
            data-testid="workbench-pricing-error"
            className="max-w-72 truncate text-xs"
            style={{ color: "var(--warning)" }}
            title={pricingError}
          >
            Pricing failed: {pricingError}
          </span>
        )}
      </div>
      <ProjectControls onSave={() => { void save() }} onCommit={onCommit} saveDisabled={!ready || saving} />
    </header>
  )
}
