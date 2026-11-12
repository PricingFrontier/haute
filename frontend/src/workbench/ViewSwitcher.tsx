import { AppWindow, Workflow, type LucideIcon } from "lucide-react"
import { Button, ToolbarButton } from "../haute-ui"
import useUIStore from "../stores/useUIStore"
import useWorkbenchStore, { type EditorView } from "../stores/useWorkbenchStore"

const VIEWS: ReadonlyArray<{ view: EditorView; label: string; Icon: LucideIcon }> = [
  { view: "pipeline", label: "Pricing", Icon: Workflow },
  { view: "workbench", label: "Workbench", Icon: AppWindow },
]

/**
 * Switches between the pipeline editor and the workbench's view (specs/workbench). It
 * sits at the bottom of the left palette: the node palette's, or the workbench's. There
 * it stacks one labelled button per view, each as wide as the palette, so a long label
 * still fits. `compact` is the column of icon buttons beside the collapsed node palette.
 * Nothing renders while the workbench is not enabled.
 */
export default function ViewSwitcher({ compact }: { compact: boolean }) {
  const enabled = useWorkbenchStore((s) => s.enabled)
  const activeView = useWorkbenchStore((s) => s.activeView)
  const showView = useWorkbenchStore((s) => s.showView)
  if (!enabled) return null

  return (
    <div
      role="group"
      aria-label="Views"
      data-testid="view-switcher"
      className={compact ? "flex flex-col items-center gap-1 py-2" : "flex flex-col gap-1 px-2 py-2"}
      style={{ borderTop: "1px solid var(--chrome-border)" }}
    >
      {VIEWS.map(({ view, label, Icon }) => {
        const shared = {
          "data-testid": `view-switcher-${view}`,
          "aria-pressed": activeView === view,
          title: `Show ${label}`,
          // Another view hides an open utility panel, so it waits for the panel's unsaved edit.
          onClick: () => useUIStore.getState().afterUtilitySaved(() => showView(view)),
        }
        return compact ? (
          <Button key={view} {...shared} iconOnly aria-label={label} className="h-7 w-7 justify-center">
            <Icon size={13} aria-hidden="true" />
          </Button>
        ) : (
          <ToolbarButton key={view} {...shared} icon={Icon} compact className="min-w-0 justify-start gap-1.5">
            <span className="truncate">{label}</span>
          </ToolbarButton>
        )
      })}
    </div>
  )
}
