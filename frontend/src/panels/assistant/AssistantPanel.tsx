import { useEffect, useRef } from "react"
import { Bot, Plus } from "lucide-react"

import PanelShell from "../PanelShell"
import TranscriptEntryView from "./TranscriptEntryView"
import Composer from "./Composer"
import useAssistantStore from "../../stores/useAssistantStore"
import useUIStore from "../../stores/useUIStore"

interface AssistantPanelProps {
  isInsideSubmodel: boolean
  currentSourceFile: string | null
}

export default function AssistantPanel({ isInsideSubmodel, currentSourceFile }: AssistantPanelProps) {
  const setAssistantOpen = useUIStore((state) => state.setAssistantOpen)
  const entries = useAssistantStore((state) => state.entries)
  const turnStatus = useAssistantStore((state) => state.turnStatus)
  const status = useAssistantStore((state) => state.status)
  const notice = useAssistantStore((state) => state.notice)
  const refreshStatus = useAssistantStore((state) => state.refreshStatus)
  const newChat = useAssistantStore((state) => state.newChat)
  const transcriptRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    void refreshStatus()
  }, [refreshStatus])

  useEffect(() => {
    const transcript = transcriptRef.current
    if (transcript && turnStatus === "streaming") {
      transcript.scrollTop = transcript.scrollHeight
    }
  }, [entries, turnStatus])

  const statusError = status === "error"

  return (
    <PanelShell
      testId="assistant-panel"
      title="Pricing Assistant"
      onClose={() => setAssistantOpen(false)}
      icon={<Bot size={14} style={{ color: "var(--accent)" }} />}
      actions={
        <button
          type="button"
          data-testid="assistant-new-chat"
          onClick={newChat}
          disabled={turnStatus === "streaming"}
          className="inline-flex items-center gap-1 rounded px-1.5 py-1 text-[10px] transition-colors hover:bg-[var(--bg-hover)] disabled:opacity-30"
          style={{ color: "var(--text-muted)" }}
          title={turnStatus === "streaming" ? "Stop the current turn before starting a new chat" : "New chat"}
        >
          <Plus size={12} aria-hidden="true" />
          New chat
        </button>
      }
    >
      {statusError && (
        <div
          data-testid="assistant-status-error"
          role="alert"
          className="mx-3 mt-3 rounded-md px-2.5 py-2 text-[11px]"
          style={{
            background: "var(--danger-soft)",
            border: "1px solid var(--danger-border)",
            color: "var(--danger-text)",
          }}
        >
          <div className="font-medium">Assistant status could not be loaded.</div>
          <button
            type="button"
            data-testid="assistant-status-retry"
            onClick={() => { void refreshStatus() }}
            className="mt-1 underline underline-offset-2"
          >
            Retry
          </button>
        </div>
      )}
      {status !== "unknown" && status !== "error" && status.configured && (
        <div
          data-testid="assistant-egress-status"
          className="shrink-0 px-3 py-1.5 text-[10px]"
          style={{ color: "var(--text-muted)", borderBottom: "1px solid var(--border)" }}
        >
          Provider: {status.endpoint_host} · {status.trust} · up to {status.max_sensitivity}
        </div>
      )}

      <div
        ref={transcriptRef}
        data-testid="assistant-transcript"
        className="flex-1 min-h-0 overflow-y-auto space-y-2 p-3"
        style={{ background: "var(--bg-panel)" }}
      >
        {entries.length === 0 && !statusError && (
          <div
            data-testid="assistant-empty"
            className="flex h-full items-center justify-center px-5 text-center text-[11px]"
            style={{ color: "var(--text-muted)" }}
          >
            Describe a pipeline change and Assistant will help author it here.
          </div>
        )}
        {entries.map((entry, index) => (
          <TranscriptEntryView key={`${entry.kind}-${index}`} entry={entry} />
        ))}
      </div>

      {notice && (
        <div
          data-testid="assistant-notice"
          role="alert"
          className="shrink-0 px-3 py-2 text-[11px]"
          style={{
            background: "var(--warning-soft)",
            borderTop: "1px solid var(--border)",
            color: "var(--warning-strong)",
          }}
        >
          {notice}
        </div>
      )}

      <Composer
        isInsideSubmodel={isInsideSubmodel}
        currentSourceFile={currentSourceFile}
      />
    </PanelShell>
  )
}
