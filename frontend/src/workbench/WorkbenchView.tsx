import { Suspense, lazy, useEffect } from "react"
import { ErrorBoundary } from "../components/ErrorBoundary"
import { PaletteColumn, PaletteHeader, PaletteItems } from "../haute-ui"
import useUIStore from "../stores/useUIStore"
import useWorkbenchFormStore from "../stores/useWorkbenchFormStore"
import SchemaEditor from "./SchemaEditor"
import ViewSwitcher from "./ViewSwitcher"
import useWorkbenchShortcuts from "./useWorkbenchShortcuts"

const GitPanel = lazy(() => import("../panels/GitPanel"))
const AssistantPanel = lazy(() => import("../panels/assistant/AssistantPanel"))

interface WorkbenchViewProps {
  /** The Git panel's save, as the node properties panel gives it. */
  onSave: () => Promise<boolean>
  /** The Assistant panel's props, as the node properties panel gives them. */
  isInsideSubmodel: boolean
  readOnly: boolean
}

const BANNER_STYLE = {
  background: "var(--danger-soft-strong)",
  color: "var(--danger-text)",
  borderBottom: "1px solid var(--danger-border-strong)",
} as const

/**
 * The workbench's view, over the area below the toolbar while it is the active view
 * (specs/workbench): its own left column, with the view's sections and the switcher back
 * to the pipeline, and the schema editor on the form the store reads when the view first
 * shows. A save refused because the form changed on disk is reported in a banner with
 * the way out, a reload. The toolbar's Git and Assistant panels open beside the view,
 * since the pipeline region they normally open in is hidden.
 */
export default function WorkbenchView({ onSave, isInsideSubmodel, readOnly }: WorkbenchViewProps) {
  const status = useWorkbenchFormStore((s) => s.status)
  const loadError = useWorkbenchFormStore((s) => s.loadError)
  const stale = useWorkbenchFormStore((s) => s.stale)
  const reload = useWorkbenchFormStore((s) => s.reload)
  const gitOpen = useUIStore((s) => s.gitOpen)
  const setGitOpen = useUIStore((s) => s.setGitOpen)
  const assistantOpen = useUIStore((s) => s.assistantOpen)

  useEffect(() => {
    void useWorkbenchFormStore.getState().load()
  }, [])
  useWorkbenchShortcuts()

  return (
    <div className="absolute inset-0 flex" data-testid="workbench-view" style={{ background: "var(--bg-base)" }}>
      <PaletteColumn>
        <PaletteHeader title="Workbench" />
        <PaletteItems>
          <nav aria-label="Workbench sections" className="flex flex-col gap-1">
            <button
              type="button"
              aria-current="page"
              className="toolbar-btn w-full min-w-0 px-2 py-1 rounded-md flex items-center gap-1.5 text-[12px] font-medium"
            >
              Schema
            </button>
          </nav>
        </PaletteItems>
        <ViewSwitcher compact={false} />
      </PaletteColumn>
      <main className="relative flex min-w-0 flex-1 flex-col" aria-label="Workbench">
        {stale && (
          <div role="alert" className="flex items-center gap-2 px-3 py-1.5 text-[12px] font-medium" style={BANNER_STYLE}>
            <span className="flex-1 truncate">
              The form changed on disk since the workbench read it. Reload it to go on; unsaved edits will be lost.
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
          <SchemaEditor />
        ) : status === "failed" ? (
          <div
            role="alert"
            className="flex flex-1 flex-col items-center justify-center gap-2 p-8 text-[13px]"
            style={{ color: "var(--danger)" }}
          >
            <span>The form could not be read: {loadError}</span>
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
            Loading the form…
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
      ) : null}
    </div>
  )
}
