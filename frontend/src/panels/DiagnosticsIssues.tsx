/**
 * The "Diagnostics Issues" alert both result workspaces show on Summary: each
 * diagnostic the run could not produce, named, with its error type and message.
 * Modelling and the optimiser report the same shape of failure, so a degraded
 * result reads the same on both screens.
 */

export interface DiagnosticsIssue {
  /** The diagnostic's identifier, shown beside its label. */
  diagnostic: string
  errorType: string
  message: string
}

interface DiagnosticsIssuesProps {
  issues: readonly DiagnosticsIssue[]
  /** The human label of a diagnostic identifier. */
  formatLabel: (diagnostic: string) => string
}

export default function DiagnosticsIssues({ issues, formatLabel }: DiagnosticsIssuesProps) {
  if (issues.length === 0) return null
  return (
    <div
      role="alert"
      aria-label="Diagnostic issues"
      className="w-full px-3 py-2 rounded-lg text-xs"
      style={{
        background: "var(--warning-soft-subtle)",
        border: "1px solid var(--warning-border)",
      }}
    >
      <div className="flex items-center gap-2">
        <span className="shrink-0" style={{ color: "var(--warning-strong)" }}>
          &#9888;
        </span>
        <span className="font-semibold" style={{ color: "var(--warning)" }}>
          Diagnostics Issues
        </span>
      </div>
      <div className="mt-2 space-y-2">
        {issues.map((issue, index) => (
          <div
            key={`${issue.diagnostic}-${index}`}
            className="grid gap-1"
            style={{ color: "var(--text-secondary)" }}
          >
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-semibold" style={{ color: "var(--text-primary)" }}>
                {formatLabel(issue.diagnostic)}
              </span>
              <span className="font-mono text-[10px]" style={{ color: "var(--text-muted)" }}>
                {issue.diagnostic}
              </span>
              <span
                className="font-mono text-[10px] px-1.5 py-0.5 rounded"
                style={{
                  color: "var(--warning)",
                  background: "var(--bg-input)",
                  border: "1px solid var(--warning-border)",
                }}
              >
                {issue.errorType}
              </span>
            </div>
            <div
              className="break-words whitespace-pre-wrap"
              style={{ color: "var(--warning)" }}
            >
              {issue.message}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
