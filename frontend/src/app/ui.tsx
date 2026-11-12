/**
 * The underwriter app's small pieces (specs/workbench), at Haute's density: a quote's stage
 * as a chip, a one-line notice with its actions, the notice of a page drawn for an earlier
 * release, a titled part that folds, and a menu behind an icon button.
 */
import { AlertTriangle, ChevronDown, ChevronRight, Info, X, type LucideIcon } from "lucide-react"
import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react"
import { createPortal } from "react-dom"
import type { WorkbenchAppQuote } from "../api/types"
import { useMenuKeyboard } from "../hooks/useMenuKeyboard"
import { STAGE_LABEL } from "./quoteValues"

export const BUTTON = "toolbar-btn inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium disabled:opacity-50"
export const ICON_BUTTON = "toolbar-btn inline-flex items-center rounded-md p-1 disabled:opacity-50"
/** The toolbar's fill shares its row equally; a fill button outside the toolbar is as wide as its label. */
export const FILL_BUTTON = "toolbar-fill toolbar-fill-save inline-flex flex-none items-center gap-1"

const STAGE_STYLE: Record<WorkbenchAppQuote["stage"] | "archived", { color: string; background: string; border: string }> = {
  draft: { color: "var(--text-secondary)", background: "transparent", border: "var(--border-bright)" },
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

/** A titled part, open until its title is pressed, its count beside the title when given. */
export function Collapsible({ title, count, children }: { title: string; count?: number; children: ReactNode }) {
  const [open, setOpen] = useState(true)
  return (
    <section className="flex flex-col gap-1" aria-label={title}>
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        className="flex h-6 items-center gap-1 px-1 text-[10px] font-semibold tracking-wider uppercase"
        style={{ color: "var(--text-muted)" }}
      >
        {open ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
        {title}
        {count !== undefined && <span className="ml-1 tabular-nums">{count}</span>}
      </button>
      {open && children}
    </section>
  )
}

export interface MenuItem {
  label: string
  icon: LucideIcon
  run: () => void
  disabled?: boolean
  title?: string
}

/** How far a menu keeps from its button and from the window's edges. */
const MENU_GAP = 4

/**
 * A button showing `icon` alone, named `label`, opening a menu of `items`: drawn on the
 * document's body below the button, flush with its right edge, or above it where the window
 * has no room below, so a list's scrolling never clips it. A press or the focus elsewhere, a
 * scroll or the window's resize closes it, so one menu is open at a time, and Escape, which
 * gives the focus back to the button.
 */
export function Menu({ label, icon: Icon, items, testId }: { label: string; icon: LucideIcon; items: MenuItem[]; testId?: string }) {
  const [open, setOpen] = useState(false)
  const button = useRef<HTMLButtonElement>(null)
  const panel = useRef<HTMLDivElement>(null)
  const close = useCallback(() => setOpen(false), [])
  const escape = useCallback(() => {
    setOpen(false)
    button.current?.focus()
  }, [])

  // Placed before it is painted, and before the keyboard's effect gives its first item the focus.
  useLayoutEffect(() => {
    if (!open || button.current === null || panel.current === null) return
    const at = button.current.getBoundingClientRect()
    const box = panel.current.getBoundingClientRect()
    const below = at.bottom + MENU_GAP
    const top = below + box.height <= window.innerHeight - MENU_GAP ? below : Math.max(MENU_GAP, at.top - MENU_GAP - box.height)
    const left = Math.max(MENU_GAP, Math.min(at.right - box.width, window.innerWidth - MENU_GAP - box.width))
    panel.current.style.top = `${top}px`
    panel.current.style.left = `${left}px`
  }, [open])

  useMenuKeyboard(panel, escape, open)

  useEffect(() => {
    if (!open) return
    const inside = (target: EventTarget | null) => target instanceof Node && (button.current?.contains(target) === true || panel.current?.contains(target) === true)
    const away = (event: Event) => {
      if (!inside(event.target)) close()
    }
    window.addEventListener("pointerdown", away)
    window.addEventListener("focusin", away)
    window.addEventListener("scroll", away, true)
    window.addEventListener("resize", close)
    return () => {
      window.removeEventListener("pointerdown", away)
      window.removeEventListener("focusin", away)
      window.removeEventListener("scroll", away, true)
      window.removeEventListener("resize", close)
    }
  }, [open, close])

  return (
    <>
      <button
        ref={button}
        type="button"
        className={ICON_BUTTON}
        aria-label={label}
        title={label}
        aria-haspopup="menu"
        aria-expanded={open}
        data-testid={testId}
        onClick={() => setOpen((was) => !was)}
      >
        <Icon size={12} aria-hidden="true" />
      </button>
      {open &&
        createPortal(
          <div
            ref={panel}
            role="menu"
            aria-label={label}
            className="fixed z-50 flex min-w-[190px] flex-col rounded-md py-1 shadow-xl"
            style={{ top: 0, left: 0, background: "var(--bg-elevated)", border: "1px solid var(--border-bright)" }}
          >
            {items.map((item) => (
              <button
                key={item.label}
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
          </div>,
          document.body,
        )}
    </>
  )
}

