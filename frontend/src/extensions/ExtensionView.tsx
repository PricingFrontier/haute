import { useEffect, useRef, useState } from "react"
import { apiErrorMessage } from "../api/errors"
import type { ExtensionInfo } from "../api/types"
import useExtensionsStore from "../stores/useExtensionsStore"
import { SWITCHER_SLOT, loadExtensionModule, mountExtension, type ExtensionHandle } from "./loadExtensionModule"
import ViewSwitcher from "./ViewSwitcher"

type ViewState = { status: "loading" } | { status: "mounted" } | { status: "failed"; message: string }

/**
 * One extension's view, over the area below the toolbar while it is the
 * active view (specs/extensions). It imports the extension's module and mounts
 * it into its host element and the toolbar's slot. Once mounted, the switcher
 * reaches the extension's palette through the `haute-view-switcher` slot;
 * until then (or if loading fails) it stays in a column of its own, so there
 * is always a way back to the pipeline.
 */
export default function ExtensionView({ extension }: { extension: ExtensionInfo }) {
  const toolbarSlot = useExtensionsStore((s) => s.toolbarSlot)
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
        handle = mountExtension(module, {
          main,
          toolbar: toolbarSlot,
          apiBase: extension.api_base,
          switcherSlot: SWITCHER_SLOT,
        })
        setState({ status: "mounted" })
      })
      .catch((error: unknown) => {
        if (!left) setState({ status: "failed", message: `${extension.label} could not be loaded: ${apiErrorMessage(error)}` })
      })
    return () => {
      left = true
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
              <ViewSwitcher compact={false} />
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
    </div>
  )
}
