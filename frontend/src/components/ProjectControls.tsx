import { useCallback, useEffect, useRef, useState } from "react"
import { BookOpen, Bot, Bug, CircleHelp, Keyboard, Loader2 } from "lucide-react"
import { SaveCommit } from "../haute-ui"
import useClickOutside from "../hooks/useClickOutside"
import useUIStore from "../stores/useUIStore"
import { DOCUMENTATION_URL } from "../utils/documentation"
import BranchIndicator from "./BranchIndicator"

const REPORT_BUG_URL = "https://github.com/PricingFrontier/haute/issues/new"

const HELP_ITEM_CLASS =
  "w-full flex items-center gap-2 px-3 py-1.5 text-[12px] text-left transition-colors hover:bg-[var(--chrome-hover)] focus-visible:bg-[var(--chrome-hover)] focus:outline-none"

interface ProjectControlsProps {
  onSave: () => void
  /** Without it there is no Commit button. */
  onCommit?: () => void
  /** Save is disabled, and the Assistant button unless a turn is running. */
  editingDisabled?: boolean
  /** Save alone is disabled; `editingDisabled` when not given. */
  saveDisabled?: boolean
}

/**
 * The project's controls at the toolbar's right, the same in the pipeline editor's
 * toolbar and the workbench's (specs/frontend-shared): the Assistant and Help column,
 * and the branch indicator with Save and Commit. 10px is the toolbar's one spacing value:
 * between adjacent buttons and between sections alike.
 */
export default function ProjectControls({
  onSave,
  onCommit,
  editingDisabled = false,
  saveDisabled = editingDisabled,
}: ProjectControlsProps) {
  const assistantOpen = useUIStore((s) => s.assistantOpen)
  const setAssistantOpen = useUIStore((s) => s.setAssistantOpen)
  const assistantTurn = useUIStore((s) => s.assistantTurn)
  const assistantUnseenOutcome = useUIStore((s) => s.assistantUnseenOutcome)
  const assistantState = assistantTurn !== null
    ? "The assistant is working"
    : assistantUnseenOutcome
      ? "The assistant finished while the panel was closed"
      : null
  const setShortcutsOpen = useUIStore((s) => s.setShortcutsOpen)
  const [helpOpen, setHelpOpen] = useState(false)
  const helpRef = useRef<HTMLDivElement>(null)
  const closeHelp = useCallback(() => setHelpOpen(false), [])
  useClickOutside(helpRef, closeHelp, helpOpen)
  const helpItems = () =>
    Array.from(helpRef.current?.querySelectorAll<HTMLElement>('[role="menuitem"]') ?? [])
  // Menu keyboard contract: focus lands on the first item when it opens, and
  // the arrow keys move through the items.
  useEffect(() => {
    if (helpOpen) helpItems()[0]?.focus()
  }, [helpOpen])

  return (
    <div className="ml-auto flex max-w-full flex-wrap items-center justify-end gap-2.5">
      {/* Assistant and Help column — equal width, paired with branch name & save/commit */}
      <div className="flex flex-col gap-1 w-fit">
        {/* A running turn fences the canvas, but the panel that shows and
            stops it stays reachable. */}
        <button
          data-testid="toolbar-assistant"
          onClick={() => setAssistantOpen(!assistantOpen)}
          disabled={editingDisabled && assistantTurn === null}
          aria-label={assistantState === null ? "Assistant" : `Assistant: ${assistantState}`}
          aria-pressed={assistantOpen}
          className="toolbar-btn relative px-2.5 py-1 text-[12px] font-medium rounded-md flex items-center justify-center gap-1 w-full"
          title={assistantState ?? "Pricing assistant"}
        >
          {assistantTurn !== null ? (
            <Loader2 size={13} className="animate-spin" data-testid="toolbar-assistant-working" aria-hidden="true" />
          ) : (
            <Bot size={13} />
          )}
          Assistant
          {assistantUnseenOutcome && assistantTurn === null && (
            <span
              data-testid="toolbar-assistant-unseen"
              aria-hidden="true"
              className="absolute right-1 top-1 h-1.5 w-1.5 rounded-full"
              style={{ background: "var(--accent)" }}
            />
          )}
        </button>
        <div
          ref={helpRef}
          className="relative w-full"
          onKeyDown={(e) => {
            if (!helpOpen) return
            if (e.key === "Escape") {
              e.stopPropagation()
              setHelpOpen(false)
              helpRef.current?.querySelector<HTMLElement>('[data-testid="toolbar-help"]')?.focus()
              return
            }
            if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return
            e.preventDefault()
            const items = helpItems()
            const index = items.indexOf(document.activeElement as HTMLElement)
            const step = e.key === "ArrowDown" ? 1 : -1
            items[(index + step + items.length) % items.length]?.focus()
          }}
        >
          <button
            data-testid="toolbar-help"
            onClick={() => setHelpOpen((v) => !v)}
            aria-haspopup="menu"
            aria-expanded={helpOpen}
            className="toolbar-btn px-2.5 py-1 text-[12px] font-medium rounded-md flex items-center justify-center gap-1 w-full"
            title="Help"
          >
            <CircleHelp size={13} />
            Help
          </button>
          {helpOpen && (
            <div
              role="menu"
              aria-label="Help"
              data-testid="toolbar-help-menu"
              className="absolute top-full right-0 mt-1 rounded-lg shadow-2xl z-50 min-w-[160px] overflow-hidden py-1"
              style={{ background: 'var(--bg-panel)', border: '1px solid var(--border)', color: 'var(--text-primary)' }}
            >
              <a
                role="menuitem"
                data-testid="toolbar-documentation"
                href={DOCUMENTATION_URL}
                target="_blank"
                rel="noopener noreferrer"
                onClick={closeHelp}
                className={HELP_ITEM_CLASS}
                title="Documentation - opens in a new tab"
              >
                <BookOpen size={12} />
                Documentation
              </a>
              <button
                role="menuitem"
                data-testid="toolbar-hotkeys"
                onClick={() => { setShortcutsOpen(true); setHelpOpen(false) }}
                className={HELP_ITEM_CLASS}
                title="Keyboard shortcuts (?)"
              >
                <Keyboard size={12} />
                Hotkeys
              </button>
              <a
                role="menuitem"
                data-testid="toolbar-report-bug"
                href={REPORT_BUG_URL}
                target="_blank"
                rel="noopener noreferrer"
                onClick={closeHelp}
                className={HELP_ITEM_CLASS}
                title="Report a bug on GitHub - opens in a new tab"
              >
                <Bug size={12} />
                Report a bug
              </a>
            </div>
          )}
        </div>
      </div>
      <BranchIndicator>
        {/* Save then Commit, in the order the work happens: a save is itself a
            commit to the save branch, and Commit rolls those saves into a
            milestone.  Two filled buttons, distinguished by hue rather than by
            one being demoted, sharing the branch name button's width. */}
        <SaveCommit onSave={onSave} onCommit={onCommit} disabled={saveDisabled} />
      </BranchIndicator>
    </div>
  )
}
