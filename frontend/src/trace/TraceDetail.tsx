import type { CSSProperties, ReactNode } from "react"

type Tone = "default" | "muted" | "accent" | "success" | "warning" | "danger"

function toneStyle(tone: Tone): CSSProperties {
  if (tone === "accent") {
    return { color: "var(--accent)", background: "var(--accent-soft)" }
  }
  if (tone === "success") {
    return { color: "var(--color-added, var(--success-hover))", background: "var(--success-soft-mid)" }
  }
  if (tone === "warning") {
    return { color: "var(--warning)", background: "var(--warning-bright-soft-strong)" }
  }
  if (tone === "danger") {
    return { color: "var(--danger-text)", background: "var(--danger-soft)" }
  }
  if (tone === "muted") {
    return { color: "var(--text-muted)", background: "rgba(255,255,255,.035)" }
  }
  return { color: "var(--text-secondary)", background: "rgba(255,255,255,.055)" }
}

const traceDetailLabelStyle: CSSProperties = {
  color: "var(--text-muted)",
  fontSize: 10,
}

export function TraceCalculationFrame({
  nodeName,
  column,
  result,
  resultTitle,
  resultMuted = false,
  accentColor = "var(--accent)",
  children,
}: {
  nodeName?: string
  column: string
  result?: ReactNode
  resultTitle?: string
  resultMuted?: boolean
  accentColor?: string
  children: ReactNode
}) {
  return (
    <section
      className="calculation-hero mx-3 my-3 space-y-3 rounded-lg px-4 py-3"
      data-testid="trace-calculation-frame"
      aria-label={`Trace calculation: ${column}`}
      style={{
        background: "var(--bg-elevated, rgba(255,255,255,0.03))",
        border: `1px solid ${accentColor}`,
        boxShadow: "none",
        color: "var(--text-secondary)",
        overflow: "hidden",
      }}
    >
      {nodeName && (
        <div className="truncate text-[13px] font-mono" style={{ color: "var(--text-secondary)" }}>
          {nodeName}
        </div>
      )}

      <div className="flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-1">
        <h3
          className="min-w-0 truncate text-[18px] font-bold"
          style={{ color: "var(--text-primary)", letterSpacing: 0 }}
          title={column.length > 60 ? column : undefined}
        >
          {column}
        </h3>
        {result !== undefined && (
          <span
            className={resultMuted ? "result-value muted null-value" : "result-value accent"}
            data-accent={!resultMuted || undefined}
            data-muted={resultMuted || undefined}
            data-testid="trace-calculation-result"
            title={resultTitle}
            style={{
              minWidth: 0,
              fontFamily: "var(--font-mono, monospace)",
              fontSize: 13,
              fontWeight: 600,
              fontVariantNumeric: "tabular-nums",
              color: resultMuted ? "var(--text-muted)" : "var(--accent)",
              fontStyle: resultMuted ? "italic" : undefined,
            }}
          >
            = {result}
          </span>
        )}
      </div>

      <div>{children}</div>
    </section>
  )
}

export function TraceDetailPanel({
  title,
  summary,
  children,
}: {
  title: string
  summary?: ReactNode
  children?: ReactNode
}) {
  return (
    <section
      aria-label={`Trace detail: ${title}`}
      data-testid="trace-detail-panel"
      className="my-2 space-y-2 text-[11px]"
      style={{ color: "var(--text-secondary)" }}
    >
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="text-[10px] font-semibold uppercase tracking-wide" style={{ color: "var(--text-muted)" }}>
          {title}
        </span>
        {summary}
      </div>
      {children}
    </section>
  )
}

export function TraceDetailChip({
  children,
  tone = "default",
  mono = true,
}: {
  children: ReactNode
  tone?: Tone
  mono?: boolean
}) {
  return (
    <span
      className={`rounded px-1.5 py-0.5 text-[10px] ${mono ? "font-mono" : "font-sans font-semibold"}`}
      style={toneStyle(tone)}
    >
      {children}
    </span>
  )
}

export function TraceDetailAlert({
  children,
}: {
  children: ReactNode
}) {
  return (
    <div role="alert" className="rounded px-2 py-1" style={{ background: "var(--danger-soft)", color: "var(--danger-text)" }}>
      {children}
    </div>
  )
}

export function TraceDetailCallout({
  title,
  summary,
  children,
}: {
  title: string
  summary?: ReactNode
  children?: ReactNode
}) {
  return (
    <section
      aria-label={title}
      className="rounded px-2 py-1.5"
      style={{ background: "var(--accent-soft)" }}
    >
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="font-semibold" style={{ color: "var(--accent)" }}>{title}</span>
        {summary}
      </div>
      {children}
    </section>
  )
}

export function TraceDetailSection({
  title,
  children,
}: {
  title: string
  children: ReactNode
}) {
  return (
    <section className="space-y-1.5" aria-label={title}>
      <div className="text-[10px] font-semibold uppercase tracking-wide" style={{ color: "var(--text-muted)" }}>
        {title}
      </div>
      {children}
    </section>
  )
}

export function TraceDetailTable({
  ariaLabel,
  gridClass,
  headers,
  children,
}: {
  ariaLabel: string
  gridClass: string
  headers: ReactNode[]
  children: ReactNode
}) {
  return (
    <div className="space-y-1 overflow-x-auto" aria-label={ariaLabel}>
      <div className={`${gridClass} tabular-nums text-[10px] font-semibold uppercase`} style={traceDetailLabelStyle}>
        {headers.map((header, index) => (
          <span key={index} className={index > 0 ? "text-center" : undefined} style={{ overflowWrap: "anywhere" }}>
            {header}
          </span>
        ))}
      </div>
      {children}
    </div>
  )
}

export function TraceDetailTableRow({
  gridClass,
  selected = false,
  children,
}: {
  gridClass: string
  selected?: boolean
  children: ReactNode
}) {
  return (
    <div
      className={`${gridClass} rounded border-l-2 px-1 py-0.5 font-mono text-[10px] tabular-nums`}
      style={{
        background: selected ? "var(--accent-soft)" : "transparent",
        borderColor: selected ? "var(--accent)" : "transparent",
        color: selected ? "var(--text-primary)" : "var(--text-secondary)",
      }}
    >
      {children}
    </div>
  )
}
