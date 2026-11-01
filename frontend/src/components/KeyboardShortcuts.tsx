import ModalShell from "./ModalShell"
import { KEYBOARD_SHORTCUT_BINDINGS } from "../hooks/useKeyboardShortcuts"

const isMac = typeof navigator !== "undefined" && /Mac/.test(navigator.userAgent)
const mod = isMac ? "\u2318" : "Ctrl"

export default function KeyboardShortcuts({ onClose }: { onClose: () => void }) {
  return (
    <ModalShell ariaLabel="Keyboard shortcuts" onClose={onClose} extraCloseKeys={["?"]} testId="keyboard-shortcuts-modal">
      <div className="px-4 py-3 flex items-center justify-between" style={{ borderBottom: "1px solid var(--border)" }}>
        <h2 className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>Keyboard Shortcuts</h2>
        <button
          onClick={onClose}
          aria-label="Close keyboard shortcuts"
          className="p-1 rounded transition-colors hover-bg"
          style={{ color: "var(--text-muted)" }}
        >
          ✕
        </button>
      </div>
      <div className="px-4 py-3 space-y-1.5">
        {KEYBOARD_SHORTCUT_BINDINGS.map((s) => {
          const keys = s.keys(mod)
          return (
            <div key={s.id} className="flex items-center justify-between py-1">
              <span className="text-[12px]" style={{ color: "var(--text-secondary)" }}>{s.label}</span>
              <kbd
                className="text-[11px] font-mono px-2 py-0.5 rounded"
                style={{
                  background: "var(--bg-input)",
                  border: "1px solid var(--border)",
                  color: "var(--text-muted)",
                }}
              >
                {keys}
              </kbd>
            </div>
          )
        })}
      </div>
    </ModalShell>
  )
}
