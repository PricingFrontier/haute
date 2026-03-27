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

      // Focus trap: wrap Tab within the modal
      if (e.key === "Tab" && containerRef.current) {
        const focusable = containerRef.current.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)
        if (focusable.length === 0) return
        const first = focusable[0]
        const last = focusable[focusable.length - 1]
        if (e.shiftKey) {
          if (document.activeElement === first) {
            e.preventDefault()
            last.focus()
          }
        } else {
          if (document.activeElement === last) {
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
