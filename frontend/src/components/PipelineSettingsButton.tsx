import { Suspense, lazy, useCallback, useState } from "react"
import type { WsStatus } from "../hooks/useWebSocketSync"

const PipelineSettingsModal = lazy(() => import("./PipelineSettingsModal"))

// Only these statuses mean the server is known to be unreachable; idle and
// connecting are not evidence, so a page load never flashes "Offline".
const OFFLINE_TITLES: Partial<Record<WsStatus, string>> = {
  reconnecting: "Server offline - reconnecting…",
  disconnected: "Server offline - reload the page once haute serve is running",
}

const PIPELINE_SETTINGS_TITLE = "Pipeline settings - preview rows, chunk rows and cached data"

/**
 * The Pipeline button and the pipeline settings it opens, as wide as its column: under
 * the Source selector in the pipeline and workbench toolbars, and under Menu in the
 * Modelling view's. It reads "Pipeline" whatever the calculation mode, and "Offline"
 * while the server is away, as nothing calculates then; "Offline" is no wider than
 * "Pipeline", so going offline never widens the column.
 */
export default function PipelineSettingsButton({ wsStatus }: { wsStatus: WsStatus }) {
  // Local, not in the UI store: this button is the only thing that opens the pipeline
  // settings, so no other surface needs to read or set this.
  const [open, setOpen] = useState(false)
  const close = useCallback(() => setOpen(false), [])
  const offlineTitle = OFFLINE_TITLES[wsStatus]
  return (
    <>
      {/* Spelled as the Source selector over it is, whose chevron the kit's centred
          action would not take, so the two texts line up. */}
      <button
        data-testid="toolbar-pipeline-settings"
        onClick={() => setOpen(true)}
        aria-haspopup="dialog"
        aria-expanded={open}
        className="toolbar-btn w-full px-2.5 py-1 text-[12px] font-medium rounded-md flex items-center justify-center"
        style={offlineTitle ? { color: 'var(--danger)' } : undefined}
        title={offlineTitle ?? PIPELINE_SETTINGS_TITLE}
      >
        {offlineTitle ? "Offline" : "Pipeline"}
      </button>
      {open && (
        <Suspense fallback={null}>
          <PipelineSettingsModal onClose={close} />
        </Suspense>
      )}
    </>
  )
}
