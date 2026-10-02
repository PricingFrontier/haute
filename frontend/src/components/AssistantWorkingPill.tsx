import { Loader2 } from "lucide-react"

import useUIStore from "../stores/useUIStore"

/**
 * Over the canvas while an assistant turn runs: the canvas is read-only until
 * the turn ends, and this says why and stops it. Reads only the UI store's
 * mirror, so the assistant store stays in the lazy panel chunk.
 */
export default function AssistantWorkingPill() {
  const turn = useUIStore((state) => state.assistantTurn)
  if (turn === null) return null
  return (
    <div
      data-testid="assistant-working-pill"
      role="status"
      className="absolute left-1/2 top-3 z-10 flex -translate-x-1/2 items-center gap-2 rounded-full py-1 pl-3 pr-1 text-[11px] font-medium shadow-lg"
      style={{
        background: "var(--bg-elevated)",
        border: "1px solid var(--border)",
        color: "var(--text-primary)",
      }}
      title="The canvas is read-only until the assistant finishes"
    >
      <Loader2 size={12} className="animate-spin" style={{ color: "var(--accent)" }} aria-hidden="true" />
      <span>Assistant is working</span>
      <button
        type="button"
        data-testid="assistant-working-stop"
        onClick={turn.stop}
        className="rounded-full px-2 py-0.5 hover-bg"
        style={{ color: "var(--danger)" }}
        title="Stop the assistant"
      >
        Stop
      </button>
    </div>
  )
}
