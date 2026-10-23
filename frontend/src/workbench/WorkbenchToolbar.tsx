import { Database, Eye, FlaskConical, Sheet, Wrench } from "lucide-react"
import ProjectControls from "../components/ProjectControls"
import { ToolbarBrand, ToolbarButton, ToolbarColumn, UndoRedo, ZoomInOut } from "../haute-ui"
import useWorkbenchFormStore from "../stores/useWorkbenchFormStore"
import useWorkbenchPricingStore from "../stores/useWorkbenchPricingStore"
import useWorkbenchTestQuotesStore from "../stores/useWorkbenchTestQuotesStore"
import useWorkbenchViewStore, { ZOOM_STEP } from "../stores/useWorkbenchViewStore"
import WorkbenchDisplay from "./WorkbenchDisplay"

interface WorkbenchToolbarProps {
  /** Save: the host's project save, the one the pipeline toolbar's Save runs. */
  onSave: () => void
  /** Commit: the host's milestone flow, which saves the project first. */
  onCommit: () => void
  /**
   * The pipeline document cannot be edited: Save and Commit are off, as they are in the
   * pipeline toolbar, and the Assistant unless a turn is running.
   */
  editingDisabled?: boolean
}

/**
 * The toolbar while the workbench's view shows (specs/workbench), the same in both modes,
 * so it keeps its layout as the mode changes, a control the mode or the section showing has
 * no use for disabled in its place: the brand; Build over Preview; Sheets over Schema, and
 * Display and the box of the sheets' colours it opens, disabled in Preview, which shows the
 * sheets; Test quotes, disabled while building; the form's Undo and Redo; Zoom In over Zoom
 * Out, disabled while the schema shows; in Preview, why pricing the sample last failed; and
 * the project's controls, the host's and the same as the pipeline toolbar's: Save saves the
 * project, the form and then the pipeline, and Commit records a milestone after that save.
 */
export default function WorkbenchToolbar({ onSave, onCommit, editingDisabled = false }: WorkbenchToolbarProps) {
  const ready = useWorkbenchFormStore((s) => s.status === "ready")
  const canUndo = useWorkbenchFormStore((s) => s.undoStack.length > 0)
  const canRedo = useWorkbenchFormStore((s) => s.redoStack.length > 0)
  const undo = useWorkbenchFormStore((s) => s.undo)
  const redo = useWorkbenchFormStore((s) => s.redo)
  const previewing = useWorkbenchViewStore((s) => s.mode === "preview")
  const section = useWorkbenchViewStore((s) => s.section)
  const showMode = useWorkbenchViewStore((s) => s.showMode)
  const showSection = useWorkbenchViewStore((s) => s.showSection)
  const zoomBy = useWorkbenchViewStore((s) => s.zoomBy)
  const error = useWorkbenchPricingStore((s) => s.error)
  const testQuotesOpen = useWorkbenchTestQuotesStore((s) => s.open)
  const setTestQuotesOpen = useWorkbenchTestQuotesStore((s) => s.setOpen)

  return (
    <header role="toolbar" aria-label="Workbench toolbar" className="toolbar flex-wrap gap-y-2 [&>div]:shrink-0">
      <ToolbarBrand name="haute" version={__APP_VERSION__} />
      <div className="ml-2.5 flex flex-wrap items-center gap-2.5" data-testid="workbench-toolbar-actions">
        <ToolbarColumn role="group" aria-label="Mode">
          <ToolbarButton icon={Wrench} aria-pressed={!previewing} onClick={() => showMode("build")} title="Lay out the sheets and the schema">
            Build
          </ToolbarButton>
          <ToolbarButton icon={Eye} aria-pressed={previewing} onClick={() => showMode("preview")} title="Use the sheets as an underwriter would">
            Preview
          </ToolbarButton>
        </ToolbarColumn>
        <ToolbarColumn role="group" aria-label="Section">
          <ToolbarButton icon={Sheet} aria-pressed={section === "sheets"} onClick={() => showSection("sheets")} disabled={previewing} title="Lay out the sheets">
            Sheets
          </ToolbarButton>
          <ToolbarButton icon={Database} aria-pressed={section === "schema"} onClick={() => showSection("schema")} disabled={previewing} title="The tables the sheets work with">
            Schema
          </ToolbarButton>
        </ToolbarColumn>
        <ToolbarColumn role="group" aria-label="Display">
          <WorkbenchDisplay disabled={!ready || previewing} />
        </ToolbarColumn>
        <ToolbarColumn role="group" aria-label="Test quotes">
          <ToolbarButton
            icon={FlaskConical}
            aria-pressed={previewing && testQuotesOpen}
            onClick={() => setTestQuotesOpen(!testQuotesOpen)}
            disabled={!ready || !previewing}
            title="Save the quote on the sheets as a test quote, and run the test quotes on the pipeline"
          >
            Test quotes
          </ToolbarButton>
        </ToolbarColumn>
        <UndoRedo canUndo={ready && canUndo} canRedo={ready && canRedo} onUndo={undo} onRedo={redo} />
        <ZoomInOut onZoomIn={() => zoomBy(ZOOM_STEP)} onZoomOut={() => zoomBy(-ZOOM_STEP)} disabled={section === "schema"} />
        {previewing && error !== null && (
          <span
            data-testid="workbench-pricing-error"
            className="max-w-72 truncate text-xs"
            style={{ color: "var(--warning)" }}
            title={error}
          >
            Pricing failed: {error}
          </span>
        )}
      </div>
      <ProjectControls onSave={onSave} onCommit={onCommit} editingDisabled={editingDisabled} />
    </header>
  )
}
