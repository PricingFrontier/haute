import { Bot } from "lucide-react"

/**
 * Beside a node's run error in the data preview: hand that error to the
 * assistant. The app shell supplies what the click does, so this stays free
 * of the lazily loaded assistant store.
 */
export default function AskAssistantButton({ onClick }: { onClick: () => void }) {
  return (
    <button
      type="button"
      data-testid="ask-assistant-to-fix"
      onClick={onClick}
      className="mx-auto mt-3 inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 text-[11px] font-medium hover-bg"
      style={{ border: "1px solid var(--border)", color: "var(--text-primary)" }}
      title="Open the assistant with this node and its error"
    >
      <Bot size={12} aria-hidden="true" style={{ color: "var(--accent)" }} />
      Ask the assistant to fix
    </button>
  )
}
