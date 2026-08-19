import { useState } from "react"
import { Send, Square } from "lucide-react"

import useGraphStore from "../../stores/useGraphStore"
import useAssistantStore, { assistantSendDisabledReason } from "../../stores/useAssistantStore"

interface ComposerProps {
  isInsideSubmodel: boolean
  currentSourceFile: string | null
  readOnly: boolean
}

export default function Composer({ isInsideSubmodel, currentSourceFile, readOnly }: ComposerProps) {
  const [text, setText] = useState("")
  const turnStatus = useAssistantStore((state) => state.turnStatus)
  const status = useAssistantStore((state) => state.status)
  const dirty = useGraphStore((state) => state.dirty)
  const reason = assistantSendDisabledReason(status, isInsideSubmodel, dirty, readOnly)
  const streaming = turnStatus === "streaming"
  const disabled = streaming || reason !== null

  const send = () => {
    if (!text.trim() || disabled) return
    setText("")
    void useAssistantStore.getState().sendMessage(text, {
      isInsideSubmodel,
      currentSourceFile: currentSourceFile ?? "",
      readOnly,
    })
  }

  return (
    <div
      className="shrink-0 p-3 space-y-2"
      style={{ borderTop: "1px solid var(--border)" }}
    >
      {reason && !streaming && (
        <p
          data-testid="assistant-composer-notice"
          role="status"
          className="text-[11px] leading-relaxed"
          style={{ color: "var(--text-muted)" }}
        >
          {reason}
        </p>
      )}
      {streaming && (
        <p
          data-testid="assistant-composer-streaming"
          className="text-[11px]"
          style={{ color: "var(--text-muted)" }}
        >
          Assistant is responding…
        </p>
      )}
      <form
        className="flex items-end gap-2"
        onSubmit={(event) => { event.preventDefault(); send() }}
      >
        <textarea
          data-testid="assistant-composer"
          value={text}
          onChange={(event) => setText(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault()
              send()
            }
          }}
          disabled={streaming}
          rows={2}
          placeholder="Ask Assistant to update the pipeline…"
          aria-label="Assistant message"
          className="min-w-0 flex-1 resize-none rounded-md px-2.5 py-2 text-[12px] focus:outline-none disabled:opacity-60"
          style={{
            background: "var(--bg-input)",
            border: "1px solid var(--border)",
            color: "var(--text-primary)",
          }}
        />
        {streaming ? (
          <button
            type="button"
            data-testid="assistant-stop"
            onClick={() => useAssistantStore.getState().stopTurn()}
            className="inline-flex shrink-0 items-center gap-1.5 rounded-md px-2.5 py-2 text-[11px] font-medium hover-bg"
            style={{ color: "var(--danger)" }}
            title="Stop Assistant"
          >
            <Square size={13} fill="currentColor" aria-hidden="true" />
            Stop
          </button>
        ) : (
          <button
            type="submit"
            data-testid="assistant-send"
            disabled={disabled || !text.trim()}
            className="inline-flex shrink-0 items-center gap-1.5 rounded-md px-2.5 py-2 text-[11px] font-medium disabled:opacity-30 hover:bg-[var(--accent-hover)]"
            style={{ background: "var(--accent)", color: "white" }}
            title="Send message"
          >
            <Send size={13} aria-hidden="true" />
            Send
          </button>
        )}
      </form>
      <div className="text-[10px]" style={{ color: "var(--text-muted)" }}>
        Shift+Enter for a new line
      </div>
    </div>
  )
}
