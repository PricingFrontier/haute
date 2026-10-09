import { Suspense, lazy, useEffect, useRef, useState } from "react"
import { apiErrorMessage } from "../api/errors"
import type { ExtensionInfo } from "../api/types"
import { ErrorBoundary } from "../components/ErrorBoundary"
import useExtensionsStore from "../stores/useExtensionsStore"
import useUIStore from "../stores/useUIStore"
import { SWITCHER_SLOT, loadExtensionModule, mountExtension, type ExtensionHandle } from "./loadExtensionModule"
import ViewSwitcher from "./ViewSwitcher"

const GitPanel = lazy(() => import("../panels/GitPanel"))
const AssistantPanel = lazy(() => import("../panels/assistant/AssistantPanel"))

type ViewState = { status: "loading" } | { status: "mounted" } | { status: "failed"; message: string }

interface ExtensionViewProps {
  extension: ExtensionInfo
  /** The Git panel's save, as the node properties panel gives it. */
  onSave: () => Promise<boolean>
  /** The Assistant panel's props, as the node properties panel gives them. */
  isInsideSubmodel: boolean
  readOnly: boolean
}

/**
 * One extension's view, over the area below the toolbar while it is the
 * active view (specs/extensions). It imports the extension's module and mounts
 * it into its host element and the toolbar's slot. Once mounted, the switcher
 * reaches the extension's palette through the `haute-view-switcher` slot;
 * until then (or if loading fails) it stays in a column of its own, so there
 * is always a way back to the pipeline. The toolbar's Git and Assistant panels
 * open beside the view, since the pipeline region they normally open in is hidden.
 */
export default function ExtensionView({ extension, onSave, isInsideSubmodel, readOnly }: ExtensionViewProps) {
  const toolbarSlot = useExtensionsStore((s) => s.toolbarSlot)
  const paletteOpen = useUIStore((s) => s.paletteOpen)
  const gitOpen = useUIStore((s) => s.gitOpen)
  const setGitOpen = useUIStore((s) => s.setGitOpen)
  const assistantOpen = useUIStore((s) => s.assistantOpen)
  const hostRef = useRef<HTMLDivElement>(null)
  const [state, setState] = useState<ViewState>({ status: "loading" })

  useEffect(() => {
    const main = hostRef.current
    if (!main || !toolbarSlot || !extension.ready) return
    let handle: ExtensionHandle | null = null
    let left = false
    loadExtensionModule(extension.entry_url)
      .then((module) => {
        if (left) return
        // Read when it mounts, not a dependency: opening or collapsing the palette
        // must not remount the view. The extension tracks it from here.
        const { paletteOpen: open, setPaletteOpen: setOpen } = useUIStore.getState()
        handle = mountExtension(module, {
          main,
          toolbar: toolbarSlot,
          apiBase: extension.api_base,
          switcherSlot: SWITCHER_SLOT,
          palette: { open, setOpen },
        })
        // The toolbar's Save now saves this view's work, if the module can.
        useExtensionsStore.getState().setViewSave(module.save ?? null)
        setState({ status: "mounted" })
      })
      .catch((error: unknown) => {
        if (!left) setState({ status: "failed", message: `${extension.label} could not be loaded: ${apiErrorMessage(error)}` })
      })
    return () => {
      left = true
      useExtensionsStore.getState().setViewSave(null)
      handle?.unmount()
    }
  }, [extension, toolbarSlot])

  // An unbuilt extension is never loaded: the server says what to do instead.
  const view: ViewState = extension.ready
    ? state
    : { status: "failed", message: extension.detail ?? `${extension.label} is not built.` }
  const mounted = view.status === "mounted"
  return (
    <div className="absolute inset-0 flex" data-testid={`extension-view-${extension.name}`} style={{ background: "var(--bg-base)" }}>
      {!mounted && (
        <div
          className="w-[180px] shrink-0 flex flex-col justify-end"
          style={{ background: "var(--chrome)", borderRight: "1px solid var(--chrome-border)" }}
        >
          <ViewSwitcher compact={false} />
        </div>
      )}
      <div className="relative flex-1 min-w-0">
        {/* The extension may give its host a shadow root, which shows only slotted
            children, so the status messages are the host's siblings. */}
        <div ref={hostRef} className="absolute inset-0" data-testid="extension-view-host">
          {mounted && (
            <div slot={SWITCHER_SLOT}>
              <ViewSwitcher compact={!paletteOpen} />
            </div>
          )}
        </div>
        {view.status === "loading" && (
          <div role="status" className="absolute inset-0 flex items-center justify-center text-[13px]" style={{ color: "var(--text-muted)" }}>
            Loading {extension.label}…
          </div>
        )}
        {view.status === "failed" && (
          <div role="alert" className="absolute inset-0 flex items-center justify-center p-8 text-[13px]" style={{ color: "var(--danger)" }}>
            {view.message}
          </div>
        )}
      </div>
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
