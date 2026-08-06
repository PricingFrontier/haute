import { MessageSquare } from "lucide-react"

import useAssistantStore from "../../stores/useAssistantStore"
import { relativeTime } from "./relativeTime"

interface SessionListProps {
  currentSourceFile: string | null
}

export default function SessionList({ currentSourceFile }: SessionListProps) {
  const sessions = useAssistantStore((state) => state.sessions)
  const sessionsStatus = useAssistantStore((state) => state.sessionsStatus)
  const openSession = useAssistantStore((state) => state.openSession)
  const loadSessions = useAssistantStore((state) => state.loadSessions)

  if (sessionsStatus === "loading" || sessionsStatus === "unknown") {
    return (
      <div
        data-testid="assistant-sessions-loading"
        className="flex h-full items-center justify-center text-[11px]"
        style={{ color: "var(--text-muted)" }}
      >
        Loading chats…
      </div>
    )
  }

  if (sessionsStatus === "error") {
    return (
      <div
        data-testid="assistant-sessions-error"
        role="alert"
        className="flex h-full flex-col items-center justify-center gap-1 px-5 text-center text-[11px]"
        style={{ color: "var(--text-muted)" }}
      >
        <span>Saved chats could not be loaded.</span>
        <button
          type="button"
          data-testid="assistant-sessions-retry"
          onClick={() => { void loadSessions(currentSourceFile) }}
          className="underline underline-offset-2"
        >
          Retry
        </button>
      </div>
    )
  }

  if (sessions.length === 0) {
    return (
      <div
        data-testid="assistant-sessions-empty"
        className="flex h-full items-center justify-center px-5 text-center text-[11px]"
        style={{ color: "var(--text-muted)" }}
      >
        No chats yet. Start one with New chat.
      </div>
    )
  }

  return (
    <ul data-testid="assistant-session-list" className="flex flex-col p-1.5">
      {sessions.map((session) => (
        <li key={session.sessionId}>
          <button
            type="button"
            data-testid="assistant-session-item"
            data-session-id={session.sessionId}
            disabled={currentSourceFile === null}
            onClick={() => {
              if (currentSourceFile === null) return
              void openSession(session.sessionId, currentSourceFile)
            }}
            className="flex w-full items-start gap-2 rounded px-2 py-2 text-left transition-colors hover:bg-[var(--bg-hover)] disabled:opacity-40"
          >
            <MessageSquare
              size={12}
              aria-hidden="true"
              className="mt-0.5 shrink-0"
              style={{ color: "var(--text-muted)" }}
            />
            <span className="min-w-0 flex-1">
              <span
                className="block truncate text-[11px]"
                style={{ color: "var(--text-primary)" }}
              >
                {session.title || "Untitled chat"}
              </span>
              <span className="block text-[10px]" style={{ color: "var(--text-muted)" }}>
                {relativeTime(session.lastUsed)}
                {session.messageCount > 0 && ` · ${session.messageCount} messages`}
              </span>
            </span>
          </button>
        </li>
      ))}
    </ul>
  )
}
