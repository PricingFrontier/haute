import { useEffect, useRef, useState } from "react"
import { ArrowLeft, Bot, Loader2, Plus } from "lucide-react"

import PanelShell from "../PanelShell"
import TranscriptEntryView, { type OutcomeReply } from "./TranscriptEntryView"
import AssistantIntro from "./AssistantIntro"
import BuildChecklist from "./BuildChecklist"
import Composer from "./Composer"
import ReadinessCard from "./ReadinessCard"
import SessionList from "./SessionList"
import useAssistantStore, {
  CHOOSE_FOR_ME_REPLY,
  FIX_ERROR_PROMPT,
  assistantSendDisabledReason,
} from "../../stores/useAssistantStore"
import useDocumentStatusStore from "../../stores/useDocumentStatusStore"
import useGraphStore from "../../stores/useGraphStore"
import useUIStore from "../../stores/useUIStore"

interface AssistantPanelProps {
  isInsideSubmodel: boolean
  readOnly: boolean
}

export default function AssistantPanel({
  isInsideSubmodel,
  readOnly,
}: AssistantPanelProps) {
  // Every chat is bound to the loaded pipeline document, never to the drilled
  // submodel file; an unsaved canvas has no source file to bind to.
  const documentSourceFile = useDocumentStatusStore((state) => state.sourceFile)
  const currentSourceFile = documentSourceFile === "" ? null : documentSourceFile
  const setAssistantOpen = useUIStore((state) => state.setAssistantOpen)
  const entries = useAssistantStore((state) => state.entries)
  const buildPlan = useAssistantStore((state) => state.buildPlan)
  const turnStatus = useAssistantStore((state) => state.turnStatus)
  const thinking = useAssistantStore((state) => state.thinking)
  const status = useAssistantStore((state) => state.status)
  const notice = useAssistantStore((state) => state.notice)
  const refreshStatus = useAssistantStore((state) => state.refreshStatus)
  const newChat = useAssistantStore((state) => state.newChat)
  const view = useAssistantStore((state) => state.view)
  const loadSessions = useAssistantStore((state) => state.loadSessions)
  const showSessionList = useAssistantStore((state) => state.showSessionList)
  const chatSource = useAssistantStore((state) => state.pipelineSource)
  const dirty = useGraphStore((state) => state.dirty)
  const previewErrorNodeId = useUIStore((state) => state.assistantPreviewErrorNodeId)
  const transcriptRef = useRef<HTMLDivElement>(null)
  const [draft, setDraft] = useState("")

  // "Ask the assistant to fix": an empty draft says what to do, once per
  // request (adjusted while rendering, the way React derives state from a
  // changed input)...
  const [draftedFixRequest, setDraftedFixRequest] = useState<string | null>(null)
  if (previewErrorNodeId !== draftedFixRequest) {
    setDraftedFixRequest(previewErrorNodeId)
    if (previewErrorNodeId !== null && !draft.trim()) setDraft(FIX_ERROR_PROMPT)
  }
  // ...and the request needs a composer, so the list screen gives way to a new chat.
  useEffect(() => {
    if (previewErrorNodeId !== null && useAssistantStore.getState().view === "list") {
      useAssistantStore.getState().newChat()
    }
  }, [previewErrorNodeId])

  useEffect(() => {
    void refreshStatus()
  }, [refreshStatus])

  // The panel opens on the chat list, so its contents are fetched on open
  // rather than waiting for a message to be sent.
  useEffect(() => {
    void loadSessions(currentSourceFile)
  }, [loadSessions, currentSourceFile])

  useEffect(() => {
    const transcript = transcriptRef.current
    if (transcript && turnStatus === "streaming") {
      transcript.scrollTop = transcript.scrollHeight
    }
  }, [entries, turnStatus, thinking])

  const statusError = status === "error"

  // Only the latest question can be answered in one click, and only when the
  // composer could send: the reply is an ordinary message under the same gate.
  const lastEntry = entries[entries.length - 1]
  const reply: OutcomeReply | undefined =
    turnStatus === "idle" &&
    lastEntry?.kind === "outcome" &&
    lastEntry.outcome.kind === "needs_input"
      ? {
          disabledReason: assistantSendDisabledReason({
            status,
            isInsideSubmodel,
            dirty,
            readOnly,
            sourceFile: currentSourceFile,
            chatSource,
          }),
          onSend: () => {
            void useAssistantStore.getState().sendMessage(CHOOSE_FOR_ME_REPLY, {
              isInsideSubmodel,
              currentSourceFile,
              readOnly,
            })
          },
        }
      : undefined

  return (
    <PanelShell
      testId="assistant-panel"
      title="Pricing Assistant"
      subtitle={
        status !== "unknown" && status !== "error" && status.configured && status.model !== null ? (
          <span data-testid="assistant-model">
            {status.model}
            {status.provider !== null && ` · ${status.provider}`}
          </span>
        ) : undefined
      }
      onClose={() => setAssistantOpen(false)}
      icon={
        view === "chat" ? (
          <button
            type="button"
            data-testid="assistant-back-to-list"
            onClick={() => showSessionList(currentSourceFile)}
            disabled={turnStatus === "streaming"}
            aria-label="Back to chats"
            title={
              turnStatus === "streaming"
                ? "Stop the current turn before leaving this chat"
                : "Back to chats"
            }
            className="rounded p-0.5 transition-colors hover:bg-[var(--bg-hover)] disabled:opacity-30"
            style={{ color: "var(--text-muted)" }}
          >
            <ArrowLeft size={14} aria-hidden="true" />
          </button>
        ) : (
          <Bot size={14} style={{ color: "var(--accent)" }} />
        )
      }
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
      <ReadinessCard showReadinessReasons={view === "list"} />
      {status !== "unknown" && status !== "error" && status.configured && (
        <div
          data-testid="assistant-egress-status"
          className="shrink-0 px-3 py-1.5 text-[10px]"
          style={{ color: "var(--text-muted)", borderBottom: "1px solid var(--border)" }}
        >
          Provider: {status.endpoint_host} · {status.trust} · up to {status.max_sensitivity}
        </div>
      )}

      {view === "list" ? (
        <div
          data-testid="assistant-sessions"
          className="flex-1 min-h-0 overflow-y-auto"
          style={{ background: "var(--bg-panel)" }}
        >
          <SessionList currentSourceFile={currentSourceFile} />
        </div>
      ) : (
        <div
          ref={transcriptRef}
          data-testid="assistant-transcript"
          className="flex-1 min-h-0 overflow-y-auto space-y-2 p-3"
          style={{ background: "var(--bg-panel)" }}
        >
          {entries.length === 0 && !statusError && (
            <AssistantIntro sourceFile={currentSourceFile} onChoosePrompt={setDraft} />
          )}
          {entries.map((entry, index) => (
            <TranscriptEntryView
              key={`${entry.kind}-${index}`}
              entry={entry}
              reply={index === entries.length - 1 ? reply : undefined}
            />
          ))}
          {thinking && (
            <div
              data-testid="assistant-thinking"
              role="status"
              className="flex items-center gap-1.5 text-[11px]"
              style={{ color: "var(--text-muted)" }}
            >
              <Loader2 size={12} className="animate-spin" aria-hidden="true" />
              Thinking…
            </div>
          )}
        </div>
      )}

      {view === "chat" && buildPlan !== null && (
        <BuildChecklist plan={buildPlan} entries={entries} transcriptRef={transcriptRef} />
      )}

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

      {view === "chat" && (
        <Composer
          isInsideSubmodel={isInsideSubmodel}
          currentSourceFile={currentSourceFile}
          readOnly={readOnly}
          text={draft}
          setText={setDraft}
        />
      )}
    </PanelShell>
  )
}
