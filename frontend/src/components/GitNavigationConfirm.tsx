interface GitNavigationConfirmProps {
  onCancel: () => void
  onDiscard: () => void
  onSave: () => Promise<void>
}

/** Confirmation required before Git navigation can replace dirty canvas work. */
export default function GitNavigationConfirm({ onCancel, onDiscard, onSave }: GitNavigationConfirmProps) {
  return (
    <div data-testid="git-navigation-confirm" className="flex flex-col gap-2 px-2.5 py-2 rounded-md" style={{ background: "var(--warning-soft, var(--accent-soft-faint))", border: "1px solid var(--warning-border)" }}>
      <span className="text-[12px]" style={{ color: "var(--text-primary)" }}>You have unsaved changes. Save them before changing branches?</span>
      <div className="flex justify-end gap-2">
        <button type="button" data-testid="git-navigation-cancel" onClick={onCancel}>Cancel</button>
        <button type="button" data-testid="git-navigation-discard" onClick={onDiscard}>Discard</button>
        <button type="button" data-testid="git-navigation-save" onClick={() => void onSave()}>Save first</button>
      </div>
    </div>
  )
}
