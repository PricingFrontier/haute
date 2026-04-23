export interface WaterfallErrorAlertProps {
  error: string
}

export default function WaterfallErrorAlert({ error }: WaterfallErrorAlertProps) {
  const hasMessage = error.trim().length > 0
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
      <div style={{ fontWeight: 600, marginBottom: hasMessage ? 2 : 0 }}>
        Waterfall error
      </div>
      {hasMessage ? (
        <div style={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
          {error}
        </div>
      ) : (
        <div style={{ fontStyle: "italic" }}>
          No details were provided by the backend.
        </div>
      )}
    </div>
  )
}
