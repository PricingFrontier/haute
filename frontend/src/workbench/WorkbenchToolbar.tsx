import { ClipboardCheck, Database, Eye, RotateCcw, Wrench } from "lucide-react"
import ProjectControls from "../components/ProjectControls"
import SourcePipelineControls from "../components/SourcePipelineControls"
import type { WsStatus } from "../hooks/useWebSocketSync"
import { ToolbarBrand, ToolbarButton, ToolbarColumn, UndoRedo, ZoomInOut } from "../haute-ui"
import useWorkbenchFormStore from "../stores/useWorkbenchFormStore"
import useWorkbenchPricingStore from "../stores/useWorkbenchPricingStore"
import useWorkbenchTestQuotesStore from "../stores/useWorkbenchTestQuotesStore"
import useWorkbenchViewStore, { ZOOM_STEP } from "../stores/useWorkbenchViewStore"
import { isNewQuoteSample, newQuoteSample } from "../utils/workbenchForm"
import WorkbenchDisplay from "./WorkbenchDisplay"

interface WorkbenchToolbarProps {
  /** Save: the host's project save, the one the pipeline toolbar's Save runs. */
  onSave: () => void
  /** Commit: the host's milestone flow, which saves the project first. */
  onCommit: () => void
  /** Live sync's state, which the Pipeline button reads "Offline" from, as in the pipeline toolbar. */
  wsStatus: WsStatus
  /**
   * The pipeline document cannot be edited: Save and Commit are off, as they are in the
   * pipeline toolbar, and the Assistant unless a turn is running.
   */
  editingDisabled?: boolean
  /** The document's source selection can be trusted, as the pipeline toolbar is told. */
  sourceSelectionTrusted?: boolean
}

/**
 * The toolbar while the workbench's view shows (specs/workbench), the same in both modes,
 * so it keeps its layout as the mode changes, a control the mode has no use for disabled in
 * its place: the brand; the pipeline toolbar's Source over Pipeline; the form's Undo and Redo;
 * Zoom In over Zoom Out, these three where the pipeline toolbar has them; Build over Preview;
 * Schema, opening and closing the schema's pane in either mode, over Display and the box of
 * the sheets' colours it opens, disabled in Preview; Reset, in Preview, starting the sample
 * again as a new quote, one undo step, over Test quotes, in Preview, opening and closing the
 * test quotes' panel; in Preview, why pricing the sample last failed; and
 * the project's controls, the host's and the same as the pipeline toolbar's: Save saves the
 * project, the form and then the pipeline, and Commit records a milestone after that save.
 */
export default function WorkbenchToolbar({
  onSave,
  onCommit,
  wsStatus,
  editingDisabled = false,
  sourceSelectionTrusted = true,
}: WorkbenchToolbarProps) {
  const ready = useWorkbenchFormStore((s) => s.status === "ready")
  const canUndo = useWorkbenchFormStore((s) => s.undoStack.length > 0)
  const canRedo = useWorkbenchFormStore((s) => s.redoStack.length > 0)
  const undo = useWorkbenchFormStore((s) => s.undo)
  const redo = useWorkbenchFormStore((s) => s.redo)
  const change = useWorkbenchFormStore((s) => s.change)
  const newQuote = useWorkbenchFormStore((s) => s.form === null || isNewQuoteSample(s.form))
  const previewing = useWorkbenchViewStore((s) => s.mode === "preview")
  const schemaOpen = useWorkbenchViewStore((s) => s.schemaOpen)
  const showMode = useWorkbenchViewStore((s) => s.showMode)
  const setSchemaOpen = useWorkbenchViewStore((s) => s.setSchemaOpen)
  const zoomBy = useWorkbenchViewStore((s) => s.zoomBy)
  const error = useWorkbenchPricingStore((s) => s.error)
  const testQuotesOpen = useWorkbenchTestQuotesStore((s) => s.open)
  const setTestQuotesOpen = useWorkbenchTestQuotesStore((s) => s.setOpen)

  return (
    <header role="toolbar" aria-label="Workbench toolbar" className="toolbar flex-wrap gap-y-2 [&>div]:shrink-0">
      <ToolbarBrand name="haute" version={__APP_VERSION__} />
      <SourcePipelineControls wsStatus={wsStatus} editingDisabled={editingDisabled} sourceSelectionTrusted={sourceSelectionTrusted} />
      <div className="ml-2.5 flex flex-wrap items-center gap-2.5" data-testid="workbench-toolbar-actions">
        <UndoRedo canUndo={ready && canUndo} canRedo={ready && canRedo} onUndo={undo} onRedo={redo} />
        <ZoomInOut onZoomIn={() => zoomBy(ZOOM_STEP)} onZoomOut={() => zoomBy(-ZOOM_STEP)} />
        <ToolbarColumn role="group" aria-label="Mode">
          <ToolbarButton icon={Wrench} aria-pressed={!previewing} onClick={() => showMode("build")} title="Lay out the sheets and the schema">
            Build
          </ToolbarButton>
          <ToolbarButton icon={Eye} aria-pressed={previewing} onClick={() => showMode("preview")} title="Use the sheets as an underwriter would">
            Preview
          </ToolbarButton>
        </ToolbarColumn>
        <ToolbarColumn>
          <ToolbarButton icon={Database} aria-pressed={schemaOpen} onClick={() => setSchemaOpen(!schemaOpen)} disabled={!ready} title="The tables the sheets work with">
            Schema
          </ToolbarButton>
          <WorkbenchDisplay disabled={!ready || previewing} />
        </ToolbarColumn>
        <ToolbarColumn>
          <ToolbarButton icon={RotateCcw} onClick={() => change(newQuoteSample)} disabled={!ready || !previewing || newQuote} title="Back to a new quote: the defaults alone">
            Reset
          </ToolbarButton>
          <ToolbarButton
            icon={ClipboardCheck}
            aria-pressed={previewing && testQuotesOpen}
            onClick={() => setTestQuotesOpen(!testQuotesOpen)}
            disabled={!ready || !previewing}
            title="Quotes kept and priced again as the pipeline changes"
          >
            Test quotes
          </ToolbarButton>
        </ToolbarColumn>
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
