import type { InputHTMLAttributes, ReactNode } from "react"
import ConfigCheckbox from "./form/ConfigCheckbox"

// The form blocks the identity, working-branch and divergence modals share,
// rendered inside a `ModalShell`: a heading with a line of context, a text
// input, the git identity fields, and the cancel/submit row.

/** A modal's heading: its title and one line of context. */
export function ModalFormHeader({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="px-4 py-3" style={{ borderBottom: "1px solid var(--border)" }}>
      <h2 className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>
        {title}
      </h2>
      <p className="text-[12px] mt-0.5" style={{ color: "var(--text-muted)" }}>
        {children}
      </p>
    </div>
  )
}

/** A full-width modal text input. */
export function ModalTextInput(props: InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      {...props}
      className="w-full px-3 py-1.5 text-[13px] rounded-md focus:outline-none focus:ring-2"
      style={{
        background: "var(--bg-input)",
        border: "1px solid var(--border)",
        color: "var(--text-primary)",
        caretColor: "var(--accent)",
      }}
    />
  )
}

/** The name and email git records saves under, and whether to set them globally.
 *  The inputs' test ids are `${testIdPrefix}-name` and `${testIdPrefix}-email`. */
export function GitIdentityFields({
  testIdPrefix,
  name,
  email,
  setGlobal,
  onNameChange,
  onEmailChange,
  onSetGlobalChange,
  autoFocus = false,
}: {
  testIdPrefix: string
  name: string
  email: string
  setGlobal: boolean
  onNameChange: (name: string) => void
  onEmailChange: (email: string) => void
  onSetGlobalChange: (setGlobal: boolean) => void
  autoFocus?: boolean
}) {
  return (
    <>
      <ModalTextInput
        data-testid={`${testIdPrefix}-name`}
        value={name}
        onChange={(e) => onNameChange(e.target.value)}
        autoFocus={autoFocus}
        placeholder="Your name"
      />
      <ModalTextInput
        data-testid={`${testIdPrefix}-email`}
        type="email"
        value={email}
        onChange={(e) => onEmailChange(e.target.value)}
        placeholder="you@example.com"
      />
      <ConfigCheckbox
        checked={setGlobal}
        onChange={onSetGlobalChange}
        label="Use this identity for all my projects (global git config)"
      />
    </>
  )
}

/** Cancel and submit, right-aligned. The submit reads `busyLabel` while busy. */
export function ModalFormActions({
  cancelLabel = "Cancel",
  onCancel,
  submitLabel,
  busyLabel,
  busy,
  disabled,
  submitTestId,
}: {
  cancelLabel?: string
  onCancel: () => void
  submitLabel: string
  busyLabel: string
  busy: boolean
  disabled: boolean
  submitTestId: string
}) {
  return (
    <div className="flex justify-end gap-2 pt-1">
      <button
        type="button"
        onClick={onCancel}
        className="px-3 py-1.5 text-[12px] font-medium rounded-md transition-colors"
        style={{ color: "var(--text-secondary)" }}
      >
        {cancelLabel}
      </button>
      <button
        type="submit"
        data-testid={submitTestId}
        disabled={disabled}
        className="px-4 py-1.5 text-[12px] font-semibold rounded-md transition-colors disabled:opacity-50 disabled:cursor-not-allowed hover:bg-[var(--structure-action-hover)] disabled:hover:bg-[var(--structure-action)]"
        style={{ background: "var(--structure-action)", color: "var(--text-on-accent)" }}
      >
        {busy ? busyLabel : submitLabel}
      </button>
    </div>
  )
}
