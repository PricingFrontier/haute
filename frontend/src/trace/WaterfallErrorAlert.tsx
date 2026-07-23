export interface WaterfallErrorAlertProps {
  error: string
  errorType?: string
}

const WATERFALL_ERROR_COPY: Record<string, string> = {
  WaterfallReconciliationError: "The calculation breakdown does not match the traced result.",
  WaterfallUnavailableError: "A waterfall cannot be shown for this trace.",
}

export default function WaterfallErrorAlert({ error, errorType }: WaterfallErrorAlertProps) {
  const hasMessage = error.trim().length > 0
  const summary = (errorType && WATERFALL_ERROR_COPY[errorType])
    || "The calculation breakdown could not be built."
  return (
    <div
      role="alert"
      className="waterfall-error-alert"
      style={{
        padding: "8px 12px",
        border: "1px solid var(--warning-border-emphasis)",
        borderRadius: 4,
        background: "var(--warning-soft)",
        color: "var(--warning-strong)",
        fontSize: 12,
        marginTop: 4,
      }}
    >
      <div style={{ fontWeight: 600 }}>
        {summary}
      </div>
      {hasMessage ? (
        <details style={{ marginTop: 4 }}>
          <summary>Technical details</summary>
          <div style={{ whiteSpace: "pre-wrap", wordBreak: "break-word", marginTop: 2 }}>
            {error}
          </div>
        </details>
      ) : (
        <div style={{ fontStyle: "italic" }}>
          No details were provided by the backend.
        </div>
      )}
    </div>
  )
}
