import ModalShell from "./ModalShell"

const isMac = typeof navigator !== "undefined" && /Mac/.test(navigator.userAgent)
const mod = isMac ? "\u2318" : "Ctrl"

const shortcuts = [
  { keys: `${mod}+Z`, label: "Undo" },
  { keys: `${mod}+Shift+Z`, label: "Redo" },
  { keys: `${mod}+C`, label: "Copy selected nodes" },
  { keys: `${mod}+V`, label: "Paste nodes" },
  { keys: `${mod}+A`, label: "Select all nodes" },
  { keys: `${mod}+S`, label: "Save pipeline" },
  { keys: `${mod}+K`, label: "Search nodes" },
  { keys: `${mod}+1`, label: "Fit view" },
  { keys: "Delete / Backspace", label: "Delete selected" },
  { keys: "?", label: "Show this help" },
]

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
        {shortcuts.map((s) => (
          <div key={s.keys} className="flex items-center justify-between py-1">
            <span className="text-[12px]" style={{ color: "var(--text-secondary)" }}>{s.label}</span>
            <kbd
              className="text-[11px] font-mono px-2 py-0.5 rounded"
              style={{
                background: "var(--bg-input)",
                border: "1px solid var(--border)",
                color: "var(--text-muted)",
              }}
            >
              {s.keys}
            </kbd>
          </div>
        ))}
      </div>
    </ModalShell>
  )
}
