import { Suspense, lazy, useEffect } from "react"
import type { GraphPayload } from "../api/types"
import { ErrorBoundary } from "../components/ErrorBoundary"
import useSettingsStore from "../stores/useSettingsStore"
import useUIStore from "../stores/useUIStore"
import useWorkbenchFormStore from "../stores/useWorkbenchFormStore"
import useWorkbenchPricingStore, { usePricingContext } from "../stores/useWorkbenchPricingStore"
import useWorkbenchStore from "../stores/useWorkbenchStore"
import useWorkbenchViewStore from "../stores/useWorkbenchViewStore"
import { pricingBasis } from "../utils/workbenchForm"
import PageTabs from "./PageTabs"
import { priceSample } from "./priceSample"
import PropertiesPanel from "./PropertiesPanel"
import SchemaPane from "./SchemaPane"
import SheetCanvas from "./SheetCanvas"
import { dropAt, sectionDropAt } from "./sheetInteractions"
import useWorkbenchShortcuts from "./useWorkbenchShortcuts"
import { paletteKind } from "./widgetKinds"
import WorkbenchPalette from "./WorkbenchPalette"

const GitPanel = lazy(() => import("../panels/GitPanel"))
const AssistantPanel = lazy(() => import("../panels/assistant/AssistantPanel"))
const FilesPanel = lazy(() => import("../panels/FilesPanel"))

interface WorkbenchViewProps {
  /** The Git panel's save, as the node properties panel gives it. */
  onSave: () => Promise<boolean>
  /** Ctrl/Cmd+S: the toolbar's Save, the project's save behind the git readiness gate. */
  onSaveShortcut: () => void
  /** The Assistant panel's props, as the node properties panel gives them. */
  isInsideSubmodel: boolean
  readOnly: boolean
  /** The pipeline as the editor holds it now, the whole document, for pricing the sample on it. */
  resolveGraph: () => GraphPayload
}

const BANNER_STYLE = {
  background: "var(--danger-soft-strong)",
  color: "var(--danger-text)",
  borderBottom: "1px solid var(--danger-border-strong)",
} as const

/**
 * The workbench's view, over the area below the toolbar while it is the active view
 * (specs/workbench), laid out alike in both modes: the component palette with the switcher
 * back to the pipeline, then the sheets, their tabs over the sheet showing, with the schema's
 * pane over the sheet's left part while the toolbar's Schema has it open, on the form the
 * store reads when the view first shows. While building the selected component's properties panel opens on
 * the right; in Preview the sheets are as an underwriter sees them, with the sample keyed
 * into them. A save refused because the file changed on disk is reported in a banner with
 * the way out, a reload. The toolbar's Git, Assistant and
 * Files panels open beside the view, in the properties panel's place. While Preview shows, the
 * sample is priced on the pipeline whenever the schema or the sample changes once typing
 * pauses, and when Preview shows, as the pipeline may have changed meanwhile.
 */
export default function WorkbenchView({ onSave, onSaveShortcut, isInsideSubmodel, readOnly, resolveGraph }: WorkbenchViewProps) {
  const status = useWorkbenchFormStore((s) => s.status)
  const loadError = useWorkbenchFormStore((s) => s.loadError)
  const stale = useWorkbenchFormStore((s) => s.stale)
  const reload = useWorkbenchFormStore((s) => s.reload)
  const basis = useWorkbenchFormStore((s) => (s.form === null ? null : pricingBasis(s.form)))
  // The pipeline, its data, its source or the document moved: Preview prices again.
  const context = usePricingContext()
  const previewing = useWorkbenchViewStore((s) => s.mode === "preview")
  const schemaOpen = useWorkbenchViewStore((s) => s.schemaOpen)
  const setPricer = useWorkbenchPricingStore((s) => s.setPricer)
  const schedule = useWorkbenchPricingStore((s) => s.schedule)
  const cancel = useWorkbenchPricingStore((s) => s.cancel)
  const formPath = useWorkbenchStore((s) => s.formPath) ?? "forms/form.json"
  const gitOpen = useUIStore((s) => s.gitOpen)
  const setGitOpen = useUIStore((s) => s.setGitOpen)
  const assistantOpen = useUIStore((s) => s.assistantOpen)
  const filesPlace = useUIStore((s) => s.filesPlace)
  const setFilesPlace = useUIStore((s) => s.setFilesPlace)

  useEffect(() => {
    void useWorkbenchFormStore.getState().load()
  }, [])
  useWorkbenchShortcuts(onSaveShortcut)
  // The pricer prices on the document and the source as they are when it is called.
  useEffect(() => {
    setPricer((workbench) => priceSample(resolveGraph(), workbench, useSettingsStore.getState().activeSource))
    return () => setPricer(null)
  }, [resolveGraph, setPricer])
  // Preview prices the sample; leaving it drops what waits to be priced, so nothing is
  // priced while building.
  useEffect(() => {
    if (!previewing || basis === null) return
    schedule()
    return cancel
  }, [basis, context, previewing, schedule, cancel])

  return (
    <div className="absolute inset-0 flex" data-testid="workbench-view" style={{ background: "var(--bg-base)" }}>
      <WorkbenchPalette />
      <main className="relative flex min-w-0 flex-1 flex-col" aria-label="Workbench">
        {stale && (
          <div role="alert" className="flex items-center gap-2 px-3 py-1.5 text-[12px] font-medium" style={BANNER_STYLE}>
            <span className="flex-1 truncate">
              {formPath} changed on disk since the workbench read it. Reload it to go on; unsaved edits will be lost.
            </span>
            <button
              type="button"
              onClick={() => { void reload() }}
              className="px-2 py-0.5 rounded border border-current opacity-90 hover:opacity-100"
            >
              Reload
            </button>
          </div>
        )}
        {status === "ready" ? (
          <>
            <div className="px-4 pt-2">
              <PageTabs />
            </div>
            <div className="relative flex min-h-0 flex-1 flex-col">
              <SheetCanvas />
              {schemaOpen && <SchemaPane />}
            </div>
          </>
        ) : status === "failed" ? (
          <div
            role="alert"
            className="flex flex-1 flex-col items-center justify-center gap-2 p-8 text-[13px]"
            style={{ color: "var(--danger)" }}
          >
            <span>Could not read the workbench: {loadError}</span>
            <button
              type="button"
              onClick={() => { void reload() }}
              className="toolbar-btn px-2.5 py-1 text-[12px] font-medium rounded-md"
            >
              Try again
            </button>
          </div>
        ) : (
          <div role="status" className="flex flex-1 items-center justify-center text-[13px]" style={{ color: "var(--text-muted)" }}>
            Loading the workbench…
          </div>
        )}
      </main>
      {gitOpen ? (
        <aside aria-label="Version control">
          <Suspense fallback={null}>
            <GitPanel onClose={() => setGitOpen(false)} onSave={onSave} />
          </Suspense>
        </aside>
      ) : assistantOpen ? (
        <aside aria-label="Assistant">
          <ErrorBoundary name="AssistantPanel">
            <Suspense fallback={null}>
              <AssistantPanel isInsideSubmodel={isInsideSubmodel} readOnly={readOnly} />
            </Suspense>
          </ErrorBoundary>
        </aside>
      ) : filesPlace !== null ? (
        <aside aria-label="Files">
          <Suspense fallback={null}>
            <FilesPanel key={filesPlace} place={filesPlace} onClose={() => setFilesPlace(null)} />
          </Suspense>
        </aside>
      ) : status === "ready" && !previewing ? (
        <PropertiesPanel />
      ) : null}
      <DragChip />
    </div>
  )
}

/** Follows the pointer while a component or a section is dragged out of the palette, until the sheet's ghost takes over. */
function DragChip() {
  const creating = useWorkbenchViewStore((s) => s.creating)
  if (creating === null) return null
  const { type, clientX, clientY } = creating
  if ((type === "section" ? sectionDropAt(clientX, clientY) : dropAt(type, clientX, clientY)) !== null) return null
  const { icon: Icon, label } = paletteKind(type)
  return (
    <div
      data-testid="drag-chip"
      className="pointer-events-none fixed z-50 flex items-center gap-2 rounded-lg px-3 py-1.5 text-xs shadow-2xl"
      style={{ left: creating.clientX + 12, top: creating.clientY + 8, border: "1px solid var(--accent)", background: "var(--bg-elevated)", color: "var(--text-primary)" }}
    >
      <Icon size={14} style={{ color: "var(--text-accent)" }} aria-hidden="true" />
      {label}
    </div>
  )
}
