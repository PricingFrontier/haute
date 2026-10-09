import { Suspense, lazy, useEffect } from "react"
import type { GraphPayload } from "../api/types"
import { ErrorBoundary } from "../components/ErrorBoundary"
import useSettingsStore from "../stores/useSettingsStore"
import useUIStore from "../stores/useUIStore"
import useWorkbenchFormStore from "../stores/useWorkbenchFormStore"
import useWorkbenchPricingStore from "../stores/useWorkbenchPricingStore"
import useWorkbenchStore from "../stores/useWorkbenchStore"
import useWorkbenchViewStore from "../stores/useWorkbenchViewStore"
import { pricingBasis } from "../utils/workbenchForm"
import PageTabs from "./PageTabs"
import { priceSample } from "./priceSample"
import PropertiesPanel from "./PropertiesPanel"
import SchemaEditor from "./SchemaEditor"
import SheetCanvas from "./SheetCanvas"
import { dropAt } from "./sheetInteractions"
import useWorkbenchShortcuts from "./useWorkbenchShortcuts"
import { WIDGET_KINDS } from "./widgetKinds"
import WorkbenchPalette from "./WorkbenchPalette"

const GitPanel = lazy(() => import("../panels/GitPanel"))
const AssistantPanel = lazy(() => import("../panels/assistant/AssistantPanel"))

interface WorkbenchViewProps {
  /** The Git panel's save, as the node properties panel gives it. */
  onSave: () => Promise<boolean>
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
 * (specs/workbench): the component palette with the switcher back to the pipeline, then
 * the section the toolbar chose, the sheets (their tabs over the sheet showing, with the
 * selected component's properties panel on the right), the schema editor, or Preview,
 * the sheets as an underwriter sees them with the quote keyed into them, on the form the
 * store reads when the view first shows. A save refused because the file changed on disk
 * is reported in a banner with the way out, a reload. The toolbar's Git and Assistant
 * panels open beside the view, in the properties panel's place. While the view shows, the
 * sample is priced on the pipeline whenever the schema or the sample changes once typing
 * pauses, and when the sheets show while building, as the pipeline may have changed
 * meanwhile.
 */
export default function WorkbenchView({ onSave, isInsideSubmodel, readOnly, resolveGraph }: WorkbenchViewProps) {
  const status = useWorkbenchFormStore((s) => s.status)
  const loadError = useWorkbenchFormStore((s) => s.loadError)
  const stale = useWorkbenchFormStore((s) => s.stale)
  const reload = useWorkbenchFormStore((s) => s.reload)
  const basis = useWorkbenchFormStore((s) => (s.form === null ? null : pricingBasis(s.form)))
  const section = useWorkbenchViewStore((s) => s.section)
  const setPricer = useWorkbenchPricingStore((s) => s.setPricer)
  const schedule = useWorkbenchPricingStore((s) => s.schedule)
  const formPath = useWorkbenchStore((s) => s.formPath) ?? "forms/form.json"
  const gitOpen = useUIStore((s) => s.gitOpen)
  const setGitOpen = useUIStore((s) => s.setGitOpen)
  const assistantOpen = useUIStore((s) => s.assistantOpen)

  useEffect(() => {
    void useWorkbenchFormStore.getState().load()
  }, [])
  useWorkbenchShortcuts()
  // The pricer prices on the document and the source as they are when it is called.
  useEffect(() => {
    setPricer((workbench) => priceSample(resolveGraph(), workbench, useSettingsStore.getState().activeSource))
    return () => setPricer(null)
  }, [resolveGraph, setPricer])
  useEffect(() => {
    if (section === "sheets" && basis !== null) schedule()
  }, [basis, section, schedule])

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
          section === "schema" ? (
            <SchemaEditor />
          ) : (
            <>
              <div className="px-4 pt-2">
                <PageTabs readOnly={section === "preview"} />
              </div>
              <SheetCanvas />
            </>
          )
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
      ) : status === "ready" && section === "sheets" ? (
        <PropertiesPanel />
      ) : null}
      <DragChip />
    </div>
  )
}

/** Follows the pointer while a component is dragged out of the palette, until the sheet's ghost takes over. */
function DragChip() {
  const creating = useWorkbenchViewStore((s) => s.creating)
  if (creating === null || dropAt(creating.type, creating.clientX, creating.clientY) !== null) return null
  const { icon: Icon, label } = WIDGET_KINDS[creating.type]
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
