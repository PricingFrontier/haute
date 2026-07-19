import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { AlertCircle, CheckCircle2, Circle, Loader2 } from "lucide-react"

import type { TranscriptEntry } from "../../stores/useAssistantStore"

interface TranscriptEntryViewProps {
  entry: TranscriptEntry
}

const MARKER_LABELS: Record<Extract<TranscriptEntry, { kind: "marker" }>["outcome"], string> = {
  completed: "Turn completed",
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

function MarkerEntry({ entry }: { entry: Extract<TranscriptEntry, { kind: "marker" }> }) {
  const isFailure = entry.outcome === "failed" || entry.outcome === "interrupted"
  const color = isFailure
    ? "var(--danger-text)"
    : entry.outcome === "completed"
      ? "var(--success)"
      : "var(--text-muted)"

  return (
    <div
      data-testid="assistant-entry-marker"
      data-outcome={entry.outcome}
      className="flex items-center gap-1.5 px-1 py-1 text-[10px]"
      style={{ color }}
    >
      <Circle size={7} fill="currentColor" aria-hidden="true" />
      <span>{MARKER_LABELS[entry.outcome]}</span>
      {entry.detail && <span className="truncate" title={entry.detail}>— {entry.detail}</span>}
    </div>
  )
}

export default function TranscriptEntryView({ entry }: TranscriptEntryViewProps) {
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
      return <MarkerEntry entry={entry} />
  }
}
