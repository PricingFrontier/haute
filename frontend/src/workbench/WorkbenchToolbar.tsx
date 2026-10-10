import { Calculator, Database, Eraser, Eye, Sheet, Wrench } from "lucide-react"
import ProjectControls from "../components/ProjectControls"
import { ToolbarBrand, ToolbarButton, ToolbarColumn, UndoRedo, ZoomInOut } from "../haute-ui"
import useWorkbenchFormStore from "../stores/useWorkbenchFormStore"
import useWorkbenchPreviewStore from "../stores/useWorkbenchPreviewStore"
import useWorkbenchPricingStore from "../stores/useWorkbenchPricingStore"
import useWorkbenchViewStore, { ZOOM_STEP } from "../stores/useWorkbenchViewStore"
import { quoteFilled } from "../utils/sheetValues"

interface WorkbenchToolbarProps {
  /** Commit: the host's milestone flow, which saves the form's unsaved edits first. */
  onCommit: () => void
}

/**
 * The toolbar while the workbench's view shows (specs/workbench): the brand; Build over
 * Preview; while building, Sheets over Schema, the form's Undo and Redo and, while the
 * sheets show, Zoom In over Zoom Out and why pricing the sample last failed; in Preview,
 * Price over Clear for the quote, the zoom, and why the quote has no price; and the
 * project's controls, whose Save saves the form and whose Commit records a milestone
 * through the host, the form and the pipeline saved first.
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
  const sampleError = useWorkbenchPricingStore((s) => s.error)
  const previewError = useWorkbenchPreviewStore((s) => s.error)
  const pricing = useWorkbenchPreviewStore((s) => s.pricing)
  const schema = useWorkbenchFormStore((s) => s.form?.schema ?? null)
  const quote = useWorkbenchPreviewStore((s) => s.quote)
  // Judged by the tables the schema has now: a value typed in a column since removed is
  // nothing to price or to clear, as the server leaves it out of the quote.
  const blank = schema === null || !quoteFilled(schema, quote)
  const priceQuote = useWorkbenchPreviewStore((s) => s.priceQuote)
  const clear = useWorkbenchPreviewStore((s) => s.clear)
  const previewing = section === "preview"
  const note = previewing
    ? previewError === null ? null : { text: previewError, title: previewError }
    : sampleError === null ? null : { text: `Pricing failed: ${sampleError}`, title: sampleError }

  return (
    <header role="toolbar" aria-label="Workbench toolbar" className="toolbar flex-wrap gap-y-2 [&>div]:shrink-0">
      <ToolbarBrand name="haute" version={__APP_VERSION__} />
      <div className="ml-2.5 flex flex-wrap items-center gap-2.5" data-testid="workbench-toolbar-actions">
        <ToolbarColumn role="group" aria-label="Mode">
          <ToolbarButton icon={Wrench} aria-pressed={!previewing} onClick={() => showSection("sheets")} title="Lay out the sheets and the schema">
            Build
          </ToolbarButton>
          <ToolbarButton icon={Eye} aria-pressed={previewing} onClick={() => showSection("preview")} title="Use the sheets as an underwriter would">
            Preview
          </ToolbarButton>
        </ToolbarColumn>
        {previewing ? (
          <ToolbarColumn role="group" aria-label="Quote">
            <ToolbarButton
              icon={Calculator}
              onClick={() => { void priceQuote() }}
              disabled={!ready || pricing || blank}
              title={blank ? "Type the quote first" : "Check the quote and price it on the pipeline"}
            >
              Price
            </ToolbarButton>
            <ToolbarButton
              icon={Eraser}
              onClick={() => { if (window.confirm("Clear the quote? Every cell will be emptied.")) clear() }}
              disabled={blank}
              title="Start a new quote"
            >
              Clear
            </ToolbarButton>
          </ToolbarColumn>
        ) : (
          <>
            <ToolbarColumn role="group" aria-label="Section">
              <ToolbarButton icon={Sheet} aria-pressed={section === "sheets"} onClick={() => showSection("sheets")} title="Lay out the sheets">
                Sheets
              </ToolbarButton>
              <ToolbarButton icon={Database} aria-pressed={section === "schema"} onClick={() => showSection("schema")} title="The tables the sheets work with">
                Schema
              </ToolbarButton>
            </ToolbarColumn>
            <UndoRedo canUndo={ready && canUndo} canRedo={ready && canRedo} onUndo={undo} onRedo={redo} />
          </>
        )}
        {section !== "schema" && <ZoomInOut onZoomIn={() => zoomBy(ZOOM_STEP)} onZoomOut={() => zoomBy(-ZOOM_STEP)} />}
        {note !== null && (
          <span
            data-testid="workbench-pricing-error"
            className="max-w-72 truncate text-xs"
            style={{ color: "var(--warning)" }}
            title={note.title}
          >
            {note.text}
          </span>
        )}
      </div>
      <ProjectControls onSave={() => { void save() }} onCommit={onCommit} saveDisabled={!ready || saving} />
    </header>
  )
}
