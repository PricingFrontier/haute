/**
 * The underwriter app's small pieces (specs/workbench), at Haute's density: a quote's stage
 * as a chip, a one-line notice with its actions, the notice of a page drawn for an earlier
 * release, and a menu behind a button.
 */
import { AlertTriangle, ChevronDown, Info, X, type LucideIcon } from "lucide-react"
import { useEffect, useRef, useState, type ReactNode } from "react"
import type { WorkbenchAppQuote } from "../api/types"
import { STAGE_LABEL } from "./quoteValues"

export const BUTTON = "toolbar-btn inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium disabled:opacity-50"
export const ICON_BUTTON = "toolbar-btn inline-flex items-center rounded-md p-1 disabled:opacity-50"
export const FILL_BUTTON = "toolbar-fill toolbar-fill-save inline-flex items-center gap-1 px-2.5"
/** The toolbar's fill shares its row equally; a fill button outside the toolbar is as wide as its label. */
export const FILL_STYLE = { flex: "none" } as const

const STAGE_STYLE: Record<WorkbenchAppQuote["stage"] | "archived", { color: string; background: string; border: string }> = {
  draft: { color: "var(--text-secondary)", background: "transparent", border: "var(--border-bright)" },
  priced: { color: "var(--text-accent)", background: "var(--accent-soft)", border: "var(--accent-ring)" },
  final: { color: "var(--quote-final-text)", background: "var(--quote-final-bg)", border: "var(--quote-final-border)" },
  archived: { color: "var(--warning)", background: "var(--warning-soft)", border: "var(--warning-border-strong)" },
}

/** A quote's stage, or Archived, as a small chip. */
export function StageChip({ stage }: { stage: WorkbenchAppQuote["stage"] | "archived" }) {
  const style = STAGE_STYLE[stage]
  return (
    <span
      data-testid={`stage-${stage}`}
      className="inline-flex h-[18px] shrink-0 items-center rounded px-1.5 text-[10px] font-semibold tracking-wide uppercase"
      style={{ color: style.color, background: style.background, border: `1px solid ${style.border}` }}
    >
      {stage === "archived" ? "Archived" : STAGE_LABEL[stage]}
    </span>
  )
}

/** A notice across the sheets: what happened, and what can be done about it. */
export function Notice({
  tone,
  children,
  actions,
  onDismiss,
  testId,
}: {
  tone: "info" | "warning" | "danger"
  children: ReactNode
  actions?: ReactNode
  onDismiss?: () => void
  testId?: string
}) {
  const Icon: LucideIcon = tone === "info" ? Info : AlertTriangle
  const color = tone === "info" ? "var(--text-accent)" : tone === "warning" ? "var(--warning)" : "var(--danger-text)"
  const border = tone === "info" ? "var(--accent-ring)" : tone === "warning" ? "var(--warning-border-strong)" : "var(--danger-border-strong)"
  return (
    <div
      role={tone === "info" ? "status" : "alert"}
      data-testid={testId}
      className="mx-4 mt-2 flex items-center gap-2 rounded-md px-2.5 py-1.5 text-xs"
      style={{ border: `1px solid ${border}`, background: "var(--bg-panel)", color: "var(--text-primary)" }}
    >
      <Icon size={13} className="shrink-0" style={{ color }} aria-hidden="true" />
      <span className="min-w-0 flex-1">{children}</span>
      {actions}
      {onDismiss !== undefined && (
        <button type="button" className={ICON_BUTTON} onClick={onDismiss} aria-label="Dismiss" title="Dismiss">
          <X size={12} />
        </button>
      )}
    </div>
  )
}

/** The page was drawn for an earlier release: Reload, saying whether what is typed stays in the browser. */
export function ReleaseNotice({ kept }: { kept: boolean }) {
  return (
    <Notice
      tone="danger"
      testId="notice-release"
      actions={
        <button type="button" className={BUTTON} onClick={() => window.location.reload()}>
          Reload
        </button>
      }
    >
      This page was drawn for an earlier release of the app.{" "}
      {kept ? "Reload it to carry on: your changes stay in this browser." : "Reload it to carry on. What is typed here is not kept."}
    </Notice>
  )
}

export interface MenuItem {
  label: string
  icon: LucideIcon
  run: () => void
  disabled?: boolean
  title?: string
}

/** A button opening a menu of `items` below it; Escape or a press elsewhere closes it. */
export function Menu({ label, icon: Icon, items, testId }: { label: string; icon: LucideIcon; items: MenuItem[]; testId?: string }) {
  const [open, setOpen] = useState(false)
  const holder = useRef<HTMLDivElement>(null)
  const entries = useRef<(HTMLButtonElement | null)[]>([])

  useEffect(() => {
    if (!open) return
    const away = (event: PointerEvent) => {
      if (!holder.current?.contains(event.target as Node)) setOpen(false)
    }
    window.addEventListener("pointerdown", away)
    entries.current.find((entry) => entry !== null && !entry.disabled)?.focus()
    return () => window.removeEventListener("pointerdown", away)
  }, [open])

  const onKey = (event: React.KeyboardEvent) => {
    if (event.key === "Escape") {
      event.stopPropagation()
      setOpen(false)
      return
    }
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return
    event.preventDefault()
    const live = entries.current.filter((entry): entry is HTMLButtonElement => entry !== null && !entry.disabled)
    const at = live.indexOf(document.activeElement as HTMLButtonElement)
    live[(at + (event.key === "ArrowDown" ? 1 : live.length - 1)) % live.length]?.focus()
  }

  return (
    <div ref={holder} className="relative" onKeyDown={onKey}>
      <button
        type="button"
        className="toolbar-btn toolbar-action"
        aria-haspopup="menu"
        aria-expanded={open}
        data-testid={testId}
        onClick={() => setOpen((was) => !was)}
      >
        <Icon size={13} aria-hidden="true" />
        {label}
        <ChevronDown size={11} aria-hidden="true" />
      </button>
      {open && (
        <div
          role="menu"
          aria-label={label}
          className="absolute right-0 z-40 mt-1 flex min-w-[190px] flex-col rounded-md py-1 shadow-xl"
          style={{ background: "var(--bg-elevated)", border: "1px solid var(--border-bright)" }}
        >
          {items.map((item, index) => (
            <button
              key={item.label}
              ref={(element) => {
                entries.current[index] = element
              }}
              type="button"
              role="menuitem"
              disabled={item.disabled}
              title={item.title}
              onClick={() => {
                setOpen(false)
                item.run()
              }}
              className="flex h-7 items-center gap-2 px-2.5 text-left text-xs hover:bg-[var(--bg-hover)] focus-visible:bg-[var(--bg-hover)] focus-visible:outline-none disabled:opacity-40"
              style={{ color: "var(--text-primary)" }}
            >
              <item.icon size={13} aria-hidden="true" style={{ color: "var(--text-secondary)" }} />
              {item.label}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

