import { memo, type ReactNode } from "react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { AlertCircle, AlertTriangle, CheckCircle2, Circle, HelpCircle, Loader2, OctagonX } from "lucide-react"

import { CHOOSE_FOR_ME_REPLY, type TranscriptEntry } from "../../stores/useAssistantStore"

/** The question card's one-click reply, supplied by the panel for the latest entry only. */
export interface OutcomeReply {
  onSend: () => void
  /** Why a reply cannot be sent now (the composer's send gate), or `null`. */
  disabledReason: string | null
}

interface TranscriptEntryViewProps {
  entry: TranscriptEntry
  reply?: OutcomeReply
}

type MarkerOutcome = Extract<TranscriptEntry, { kind: "marker" }>["outcome"] | "applied" | "answered"

const MARKER_LABELS: Record<MarkerOutcome, string> = {
  applied: "Changes applied",
  answered: "Turn completed",
  failed: "Turn failed",
  stopped: "Turn stopped",
  interrupted: "Turn interrupted",
}

function AssistantMarkdown({ text, streaming }: { text: string; streaming: boolean }) {
  return (
    <div
      className="text-[12px] leading-relaxed assistant-markdown"
      style={{ color: "var(--text-primary)" }}
    >
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        skipHtml
        components={{
          pre: ({ children, ...props }) => (
            <pre
              {...props}
              className="my-2 overflow-x-auto rounded-md px-3 py-2 text-[11px] leading-relaxed"
              style={{
                background: "var(--bg-input)",
                border: "1px solid var(--border)",
                color: "var(--text-secondary)",
              }}
            >
              {children}
            </pre>
          ),
          code: ({ children, className, ...props }) => (
            <code
              {...props}
              className={`${className ?? ""} font-mono text-[11px]`}
              style={{ color: "var(--text-accent-muted)" }}
            >
              {children}
            </code>
          ),
          a: ({ children, ...props }) => (
            <a {...props} style={{ color: "var(--accent)" }}>
              {children}
            </a>
          ),
        }}
      >
        {text}
      </ReactMarkdown>
      {streaming && (
        <span
          aria-label="Assistant is typing"
          className="inline-block w-1.5 h-3 ml-1 align-[-2px] animate-pulse"
          style={{ background: "var(--accent)" }}
        />
      )}
    </div>
  )
}

function ActivityEntry({ entry }: { entry: Extract<TranscriptEntry, { kind: "activity" }> }) {
  const Icon = entry.state === "running"
    ? Loader2
    : entry.state === "ok"
      ? CheckCircle2
      : AlertCircle
  const color = entry.state === "running"
    ? "var(--accent)"
    : entry.state === "ok"
      ? "var(--success)"
      : "var(--danger)"

  return (
    <div
      data-testid="assistant-entry-activity"
      className="flex items-start gap-2 rounded-md px-2.5 py-1.5 text-[11px]"
      style={{ background: "var(--bg-elevated)", border: "1px solid var(--border)" }}
    >
      <Icon
        size={13}
        aria-hidden="true"
        className={entry.state === "running" ? "animate-spin" : undefined}
        style={{ color }}
      />
      <div className="min-w-0 flex-1">
        <div className="font-medium truncate" style={{ color: "var(--text-secondary)" }}>
          {entry.name}
        </div>
        {entry.summary && (
          <div className="break-words" style={{ color: "var(--text-muted)" }}>
            {entry.summary}
          </div>
        )}
      </div>
    </div>
  )
}

function MarkerEntry({ outcome, detail }: { outcome: MarkerOutcome; detail?: string }) {
  const isFailure = outcome === "failed" || outcome === "interrupted"
  const color = isFailure
    ? "var(--danger-text)"
    : outcome === "applied" || outcome === "answered"
      ? "var(--success)"
      : "var(--text-muted)"

  return (
    <div
      data-testid="assistant-entry-marker"
      data-outcome={outcome}
      className="flex items-center gap-1.5 px-1 py-1 text-[10px]"
      style={{ color }}
    >
      <Circle size={7} fill="currentColor" aria-hidden="true" />
      <span>{MARKER_LABELS[outcome]}</span>
      {detail && <span className="truncate" title={detail}>- {detail}</span>}
    </div>
  )
}

interface OutcomeCardProps {
  testId: string
  icon: typeof AlertCircle
  tone: "accent" | "warning" | "danger"
  title: string
  children: ReactNode
}

const CARD_TONES: Record<OutcomeCardProps["tone"], { background: string; border: string; color: string }> = {
  accent: { background: "var(--accent-soft)", border: "var(--border)", color: "var(--accent)" },
  warning: { background: "var(--warning-soft)", border: "var(--border)", color: "var(--warning-strong)" },
  danger: { background: "var(--danger-soft)", border: "var(--danger-border)", color: "var(--danger-text)" },
}

function OutcomeCard({ testId, icon: Icon, tone, title, children }: OutcomeCardProps) {
  const colors = CARD_TONES[tone]
  return (
    <div
      data-testid={testId}
      role="status"
      className="rounded-md px-2.5 py-2 text-[11px] space-y-1.5"
      style={{ background: colors.background, border: `1px solid ${colors.border}` }}
    >
      <div className="flex items-center gap-1.5 font-medium" style={{ color: colors.color }}>
        <Icon size={13} aria-hidden="true" />
        <span>{title}</span>
      </div>
      {children}
    </div>
  )
}

function OutcomeEntry({
  entry,
  reply,
}: {
  entry: Extract<TranscriptEntry, { kind: "outcome" }>
  reply?: OutcomeReply
}) {
  const { outcome } = entry
  switch (outcome.kind) {
    case "applied":
    case "answered":
      return <MarkerEntry outcome={outcome.kind} />
    case "needs_input":
      return (
        <OutcomeCard
          testId="assistant-outcome-needs-input"
          icon={HelpCircle}
          tone="accent"
          title="Assistant needs your input"
        >
          <AssistantMarkdown text={outcome.detail} streaming={false} />
          {reply && (
            <button
              type="button"
              data-testid="assistant-choose-for-me"
              onClick={reply.onSend}
              disabled={reply.disabledReason !== null}
              title={reply.disabledReason ?? "Let the assistant choose and say what it picked"}
              className="rounded-md px-2 py-1 text-[11px] font-medium disabled:opacity-40 hover:bg-[var(--bg-hover)]"
              style={{ border: "1px solid var(--border)", color: "var(--text-primary)" }}
            >
              {CHOOSE_FOR_ME_REPLY}
            </button>
          )}
        </OutcomeCard>
      )
    case "blocked":
      return (
        <OutcomeCard
          testId="assistant-outcome-blocked"
          icon={OctagonX}
          tone="danger"
          title="Assistant is blocked"
        >
          <AssistantMarkdown text={outcome.detail} streaming={false} />
          <p style={{ color: "var(--text-secondary)" }}>Nothing was saved.</p>
        </OutcomeCard>
      )
    case "committed_unverified":
      return (
        <OutcomeCard
          testId="assistant-outcome-committed-unverified"
          icon={AlertTriangle}
          tone="warning"
          title="Saved, but not verified"
        >
          <p style={{ color: "var(--text-primary)" }}>
            Your changes were saved, but the check after saving failed. Review the pipeline, or
            return to the previous save in the Git panel, before continuing.
          </p>
          <p className="break-words" style={{ color: "var(--text-muted)" }}>{outcome.detail}</p>
        </OutcomeCard>
      )
  }
}

function TranscriptEntryView({ entry, reply }: TranscriptEntryViewProps) {
  switch (entry.kind) {
    case "user":
      return (
        <div
          data-testid="assistant-entry-user"
          className="self-end max-w-[88%] rounded-lg rounded-br-sm px-3 py-2 text-[12px] whitespace-pre-wrap"
          style={{ background: "var(--accent-soft)", color: "var(--text-primary)" }}
        >
          {entry.text}
        </div>
      )
    case "assistant":
      return (
        <div data-testid="assistant-entry-assistant" className="max-w-[94%]">
          <AssistantMarkdown text={entry.text} streaming={entry.streaming} />
        </div>
      )
    case "activity":
      return <ActivityEntry entry={entry} />
    case "marker":
      return <MarkerEntry outcome={entry.outcome} detail={entry.detail} />
    case "outcome":
      return <OutcomeEntry entry={entry} reply={reply} />
  }
}

export default memo(TranscriptEntryView)
