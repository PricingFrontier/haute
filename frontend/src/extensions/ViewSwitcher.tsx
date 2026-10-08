import { AppWindow, Workflow } from "lucide-react"
import useExtensionsStore, { PIPELINE_VIEW } from "../stores/useExtensionsStore"

/**
 * Switches between the pipeline editor and installed extensions' views
 * (specs/extensions). It sits at the bottom of the left palette: the node
 * palette's, or an extension's through its switcher slot. There it stacks one
 * labelled button per view, each as wide as the palette, so a long label still
 * fits. `compact` is the column of icon buttons beside the collapsed node
 * palette. Nothing renders until an extension is installed.
 */
export default function ViewSwitcher({ compact }: { compact: boolean }) {
  const extensions = useExtensionsStore((s) => s.extensions)
  const activeView = useExtensionsStore((s) => s.activeView)
  const showView = useExtensionsStore((s) => s.showView)
  if (extensions.length === 0) return null

  const views = [
    { name: PIPELINE_VIEW, label: "Pricing", Icon: Workflow },
    ...extensions.map((extension) => ({ name: extension.name, label: extension.label, Icon: AppWindow })),
  ]
  return (
    <div
      role="group"
      aria-label="Views"
      data-testid="view-switcher"
      className={compact ? "flex flex-col items-center gap-1 py-2" : "flex flex-col gap-1 px-2 py-2"}
      style={{ borderTop: "1px solid var(--chrome-border)" }}
    >
      {views.map(({ name, label, Icon }) => (
        <button
          key={name}
          type="button"
          data-testid={`view-switcher-${name}`}
          aria-pressed={activeView === name}
          aria-label={compact ? label : undefined}
          title={`Show ${label}`}
          onClick={() => showView(name)}
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
