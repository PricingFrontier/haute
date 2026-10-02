import { AlertTriangle } from "lucide-react"

import useAssistantStore from "../../stores/useAssistantStore"

interface ReadinessCardProps {
  /**
   * The list screen also states why the assistant cannot be used yet; inside a
   * chat the composer states it, so only a status that could not be read shows.
   */
  showReadinessReasons: boolean
}

function Card({
  testId,
  title,
  reason,
  fix,
  actionLabel,
}: {
  testId: string
  title: string
  reason?: string
  fix?: string
  actionLabel: string
}) {
  const refreshStatus = useAssistantStore((state) => state.refreshStatus)
  return (
    <div
      data-testid={testId}
      role="alert"
      className="mx-3 mt-3 shrink-0 space-y-1 rounded-md px-2.5 py-2 text-[11px]"
      style={{
        background: "var(--warning-soft)",
        border: "1px solid var(--border)",
        color: "var(--text-primary)",
      }}
    >
      <div className="flex items-center gap-1.5 font-medium" style={{ color: "var(--warning-strong)" }}>
        <AlertTriangle size={13} aria-hidden="true" />
        <span>{title}</span>
      </div>
      {reason && <p data-testid="assistant-readiness-reason" className="break-words">{reason}</p>}
      {fix && <p style={{ color: "var(--text-secondary)" }}>{fix}</p>}
      <button
        type="button"
        data-testid="assistant-status-retry"
        onClick={() => { void refreshStatus() }}
        className="underline underline-offset-2"
      >
        {actionLabel}
      </button>
    </div>
  )
}

/** Why the assistant cannot be used, with the reason the backend gave and a re-check. */
export default function ReadinessCard({ showReadinessReasons }: ReadinessCardProps) {
  const status = useAssistantStore((state) => state.status)
  const statusErrorDetail = useAssistantStore((state) => state.statusErrorDetail)

  if (status === "unknown") return null
  if (status === "error") {
    return statusErrorDetail === null ? (
      <Card
        testId="assistant-status-error"
        title="Assistant status could not be loaded."
        actionLabel="Retry"
      />
    ) : (
      <Card
        testId="assistant-status-error"
        title="Assistant settings could not be read."
        reason={statusErrorDetail}
        fix="Fix haute.toml, then check again."
        actionLabel="Check again"
      />
    )
  }
  if (!showReadinessReasons) return null
  if (!status.configured) {
    return (
      <Card
        testId="assistant-readiness"
        title="Assistant is not set up"
        reason={status.reason ?? "Assistant is not configured."}
        actionLabel="Check again"
      />
    )
  }
  if (!status.mutations_enabled) {
    return (
      <Card
        testId="assistant-readiness"
        title="Assistant cannot edit this project"
        reason={status.mutations_reason ?? "Assistant mutations are disabled."}
        actionLabel="Check again"
      />
    )
  }
  return null
}
