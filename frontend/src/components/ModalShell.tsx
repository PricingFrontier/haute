import { useEffect, useRef, type ReactNode } from "react"

export interface ModalShellProps {
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
  'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])'

/**
 * Shared modal shell: full-screen overlay with backdrop click,
 * Escape key handling, focus trapping, and a centred panel.
 *
 * Used by SubmodelDialog, RenameDialog, and KeyboardShortcuts.
 */
export default function ModalShell({
  ariaLabel,
  onClose,
  extraCloseKeys,
  width = "w-[360px]",
  testId,
  children,
}: ModalShellProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const previousFocusRef = useRef<Element | null>(null)

  useEffect(() => {
    // Save the previously focused element and focus the dialog container
    previousFocusRef.current = document.activeElement
    containerRef.current?.focus()

    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape" || (extraCloseKeys && extraCloseKeys.includes(e.key))) {
        e.preventDefault()
        onClose()
        return
      }

      // Focus trap: wrap Tab within the modal (Issue #41 — also redirect
      // focus back INTO the modal if it has somehow landed on an element
      // outside — e.g. a background button that retained focus before the
      // modal mounted).
      if (e.key === "Tab" && containerRef.current) {
        const focusable = containerRef.current.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)
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
        if (!containerRef.current.contains(active)) {
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
  }, [onClose, extraCloseKeys])

  return (
    <div
      ref={containerRef}
      data-testid={testId}
      className="fixed inset-0 z-50 flex items-center justify-center"
      role="dialog"
      aria-modal="true"
      aria-label={ariaLabel}
      tabIndex={-1}
      style={{ background: "rgba(0,0,0,.5)" }}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose()
      }}
    >
      <div
        className={`${width} flex flex-col rounded-xl overflow-hidden shadow-2xl`}
        style={{ background: "var(--bg-panel)", border: "1px solid var(--border)" }}
      >
        {children}
      </div>
    </div>
  )
}
