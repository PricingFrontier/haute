import { AppWindow, ChartSpline, Workflow, type LucideIcon } from "lucide-react"
import useUIStore from "../stores/useUIStore"
import useWorkbenchStore, { type EditorView } from "../stores/useWorkbenchStore"

const VIEWS: ReadonlyArray<{ view: EditorView; label: string; Icon: LucideIcon }> = [
  { view: "pipeline", label: "Pricing", Icon: Workflow },
  { view: "workbench", label: "Workbench", Icon: AppWindow },
  { view: "modelling", label: "Modelling", Icon: ChartSpline },
]

/**
 * Switches between the editor's views: the pipeline editor, the workbench's view
 * (specs/workbench) and the Modelling view (specs/frontend-modelling-optimiser-ui). It
 * sits at the bottom of the left column of whichever view shows. There it stacks one
 * labelled button per view, each as wide as the column, so a long label still fits.
 * `compact` is the column of icon buttons beside the collapsed node palette. Pricing and
 * Modelling always show; Workbench only while the workbench is enabled.
 */
export default function ViewSwitcher({ compact }: { compact: boolean }) {
  const enabled = useWorkbenchStore((s) => s.enabled)
  const activeView = useWorkbenchStore((s) => s.activeView)
  const showView = useWorkbenchStore((s) => s.showView)

  return (
    <div
      role="group"
      aria-label="Views"
      data-testid="view-switcher"
      className={compact ? "flex flex-col items-center gap-1 py-2" : "flex flex-col gap-1 px-2 py-2"}
      style={{ borderTop: "1px solid var(--chrome-border)" }}
    >
      {VIEWS.filter(({ view }) => view !== "workbench" || enabled).map(({ view, label, Icon }) => (
        <button
          key={view}
          type="button"
          data-testid={`view-switcher-${view}`}
          aria-pressed={activeView === view}
          aria-label={compact ? label : undefined}
          title={`Show ${label}`}
          // Another view hides an open utility panel, so it waits for the panel's unsaved edit.
          onClick={() => useUIStore.getState().afterUtilitySaved(() => showView(view))}
          className={
            compact
              ? "toolbar-btn w-7 h-7 rounded-md flex items-center justify-center"
              : "toolbar-btn w-full min-w-0 px-2 py-1 rounded-md flex items-center gap-1.5 text-[12px] font-medium"
          }
        >
          <Icon size={13} aria-hidden="true" />
          {!compact && <span className="truncate">{label}</span>}
        </button>
      ))}
    </div>
  )
}
