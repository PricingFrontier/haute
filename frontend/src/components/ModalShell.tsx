import { useEffect, useRef, type ReactNode } from "react"

export interface ModalShellProps {
  /** Keep the same subtree mounted inline while the modal is inactive. */
  active?: boolean
  /** Accessible label for the dialog */
  ariaLabel: string
  /** Called when the user clicks the backdrop or presses Escape */
  onClose: () => void
  /** Optional extra keys (besides Escape) that close the modal */
  extraCloseKeys?: string[]
  /** Width class for the inner panel (default: "w-[360px]") */
  width?: string
  /** data-testid applied to the outer backdrop (for E2E tests) */
  testId?: string
  children: ReactNode
}

const FOCUSABLE_SELECTOR =
  'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), summary, [tabindex]:not([tabindex="-1"])'

function isAvailableForFocus(element: HTMLElement): boolean {
  if (element.closest("[hidden], [inert]")) return false
  // Closed disclosures leave their children in the DOM, but only their summary
  // participates in keyboard navigation. Check every ancestor for nested details.
  for (let parent = element.parentElement; parent; parent = parent.parentElement) {
    if (parent instanceof HTMLDetailsElement && !parent.open) {
      const summary = parent.querySelector(":scope > summary")
      if (!summary?.contains(element)) return false
    }
  }
  return true
}

/**
 * Shared modal shell: full-screen overlay with backdrop click,
 * Escape key handling, focus trapping, and a centred panel.
 *
 * Used by SubmodelDialog, RenameDialog, and KeyboardShortcuts.
 */
export default function ModalShell({
  active = true,
  ariaLabel,
  onClose,
  extraCloseKeys,
  width = "w-[360px]",
  testId,
  children,
}: ModalShellProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const previousFocusRef = useRef<Element | null>(null)
  const onCloseRef = useRef(onClose)
  const extraCloseKeysRef = useRef(extraCloseKeys)

  useEffect(() => {
    onCloseRef.current = onClose
    extraCloseKeysRef.current = extraCloseKeys
  }, [onClose, extraCloseKeys])

  useEffect(() => {
    if (!active) return
    // Save the previously focused element and focus the dialog container
    previousFocusRef.current = document.activeElement
    containerRef.current?.focus()

    const handler = (e: KeyboardEvent) => {
      const currentExtraCloseKeys = extraCloseKeysRef.current
      if (e.key === "Escape" || (currentExtraCloseKeys && currentExtraCloseKeys.includes(e.key))) {
        e.preventDefault()
        onCloseRef.current()
        return
      }

      // Focus trap: wrap Tab within the modal (Issue #41 — also redirect
      // focus back INTO the modal if it has somehow landed on an element
      // outside — e.g. a background button that retained focus before the
      // modal mounted).
      if (e.key === "Tab" && containerRef.current) {
        const focusable = Array.from(
          containerRef.current.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR),
        ).filter(isAvailableForFocus)
        if (focusable.length === 0) {
          // Nothing focusable inside — keep focus on the modal container
          // so Tab cannot escape.  This branch also guards against the
          // querySelectorAll-returned-empty edge case below.
          e.preventDefault()
          containerRef.current.focus()
          return
        }
        const first = focusable[0]
        const last = focusable[focusable.length - 1]
        const active = document.activeElement

        // If focus is currently OUTSIDE the modal, redirect it back in.
        // Without this, a background element that held focus before the
        // modal opened can Tab out freely, violating the trap.
        if (active === containerRef.current || !containerRef.current.contains(active)) {
          e.preventDefault()
          if (e.shiftKey) {
            last.focus()
          } else {
            first.focus()
          }
          return
        }

        if (e.shiftKey) {
          if (active === first) {
            e.preventDefault()
            last.focus()
          }
        } else {
          if (active === last) {
            e.preventDefault()
            first.focus()
          }
        }
      }
    }
    document.addEventListener("keydown", handler)
    return () => {
      document.removeEventListener("keydown", handler)
      // Restore focus to the previously focused element
      if (previousFocusRef.current instanceof HTMLElement) {
        previousFocusRef.current.focus()
      }
    }
  }, [active])

  return (
    <div
      ref={containerRef}
      data-testid={testId}
      className={active ? "fixed inset-0 z-50 flex items-center justify-center" : "contents"}
      role={active ? "dialog" : undefined}
      aria-modal={active ? true : undefined}
      aria-label={active ? ariaLabel : undefined}
      tabIndex={active ? -1 : undefined}
      style={active ? { background: "rgba(0,0,0,.5)" } : undefined}
      onClick={(e) => {
        if (active && e.target === e.currentTarget) onClose()
      }}
    >
      <div
        className={
          active ? `${width} flex flex-col rounded-xl overflow-hidden shadow-2xl` : "contents"
        }
        style={
          active ? { background: "var(--bg-panel)", border: "1px solid var(--border)" } : undefined
        }
      >
        {children}
      </div>
    </div>
  )
}
