import { useEffect, useRef, useState } from "react"
import ModalShell from "./ModalShell"

interface RenameDialogProps {
  defaultValue: string
  onConfirm: (newName: string) => Promise<
    { ok: true } | { ok: false; error: string }
  >
  onCancel: () => void
}

/** Maximum allowed length for a rename. Longer names break the breadcrumb
 *  bar, context menu, and code generation downstream. */
const MAX_NAME_LENGTH = 200

/** Unsafe characters. These would corrupt the generated Python code, break
 *  markdown rendering, or produce invisible (control) glyphs:
 *    - `\u0000-\u001f` — all C0 control characters (includes \n, \t, \r, \0)
 *    - `\u007f`        — DEL control char
 *    - `` ` ``         — breaks markdown code spans and our template strings
 *
 *  Unicode letters, digits, punctuation, spaces, dashes, etc. are allowed
 *  freely — sanitisation for code-gen happens in a separate backend identity
 *  step (not here). */
// eslint-disable-next-line no-control-regex -- deliberately matching control chars
const UNSAFE_CHAR_REGEX = /[\u0000-\u001f\u007f`]/

/**
 * Validate a human-visible node label.
 *
 * @returns The trimmed value if valid; otherwise null.
 */
function validateName(raw: string): string | null {
  const trimmed = raw.trim()
  if (trimmed.length === 0) return null
  if (trimmed.length > MAX_NAME_LENGTH) return null
  if (UNSAFE_CHAR_REGEX.test(trimmed)) return null
  return trimmed
}

export default function RenameDialog({ defaultValue, onConfirm, onCancel }: RenameDialogProps) {
  // We render a single-line <textarea> rather than <input type="text"> so
  // that newline characters are visible to validation. HTMLInputElement
  // silently strips newlines during its value-sanitization algorithm
  // (https://html.spec.whatwg.org/multipage/input.html#text-(type=text)-state-and-search-state-(type=search))
  // which would let a pasted or programmatically-injected "\n" slip past
  // us even though those characters corrupt downstream code generation.
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const [value, setValue] = useState<string>(defaultValue)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Auto-focus and select all text on mount
  useEffect(() => {
    const el = inputRef.current
    if (el) {
      el.focus()
      el.select()
    }
  }, [])

  const validated = validateName(value)
  const canSubmit = validated !== null

  const submit = async () => {
    if (pending) return
    const result = validateName(value)
    if (result === null) return
    setPending(true)
    setError(null)
    try {
      const outcome = await onConfirm(result)
      if (!outcome.ok) setError(outcome.error)
    } catch (reason: unknown) {
      setError(`Rename failed: ${reason instanceof Error ? reason.message : String(reason)}`)
    } finally {
      setPending(false)
    }
  }

  return (
    <ModalShell
      ariaLabel="Rename node"
      onClose={pending ? () => {} : onCancel}
      testId="rename-dialog"
    >
      <div className="px-4 py-3" style={{ borderBottom: "1px solid var(--border)" }}>
        <h2 className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>
          Rename Node
        </h2>
      </div>
      <form
        className="p-4 flex flex-col gap-3"
        onSubmit={(e) => {
          e.preventDefault()
          void submit()
        }}
      >
        <div>
          <label
            htmlFor="rename-input"
            className="text-[11px] font-medium block mb-1"
            style={{ color: "var(--text-muted)" }}
          >
            Node name
          </label>
          <textarea
            ref={inputRef}
            id="rename-input"
            name="name"
            rows={1}
            value={value}
            disabled={pending}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={(e) => {
              // Enter submits (as an <input type="text"> would). Shift+Enter
              // inserts a literal newline — but the subsequent validation
              // will reject it, so this is only useful as an escape hatch
              // during development. We intentionally don't intercept it
              // silently because a silent drop of user keystrokes would
              // violate the "fail loudly" principle.
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault()
                void submit()
              }
            }}
            className="w-full px-3 py-1.5 text-[13px] rounded-md focus:outline-none focus:ring-2 resize-none overflow-hidden"
            style={{
              background: "var(--bg-input)",
              border: "1px solid var(--border)",
              color: "var(--text-primary)",
              caretColor: "var(--accent)",
              whiteSpace: "nowrap",
            }}
            aria-invalid={!canSubmit}
            aria-describedby={error ? "rename-error" : undefined}
          />
          {error && (
            <p id="rename-error" role="alert" className="mt-1 text-[11px]" style={{ color: "var(--danger)" }}>
              {error}
            </p>
          )}
        </div>
        <div className="flex justify-end gap-2">
          <button
            type="button"
            onClick={onCancel}
            disabled={pending}
            className="px-3 py-1.5 text-[12px] font-medium rounded-md transition-colors"
            style={{ color: "var(--text-secondary)" }}
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={!canSubmit || pending}
            className="px-4 py-1.5 text-[12px] font-semibold rounded-md transition-colors disabled:opacity-50 disabled:cursor-not-allowed hover:bg-[var(--structure-action-hover)] disabled:hover:bg-[var(--structure-action)]"
            style={{ background: "var(--structure-action)", color: "var(--text-on-accent)" }}
          >
            {pending ? "Resolving…" : "Rename"}
          </button>
        </div>
      </form>
    </ModalShell>
  )
}
