/**
 * A quote's history in the underwriter app (specs/workbench), on Haute's side panel: the
 * handover note, edited here; the quote it came from and those made from it; and what was
 * done to it, by whom and when, newest first.
 */
import { History } from "lucide-react"
import { useState } from "react"
import type { WorkbenchAppQuote } from "../api/types"
import { halfWindowWidth, SidePanel } from "../haute-ui"
import useWorkbenchViewStore from "../stores/useWorkbenchViewStore"
import { relativeTime } from "../utils/relativeTime"
import { RELATION_TEXT as RELATION, activityText, fullTime } from "./quoteValues"
import { followLink, quotePath } from "./route"
import { BUTTON, Collapsible } from "./ui"
import useOpenQuoteStore, { busy, canEdit } from "./useOpenQuoteStore"

const MIN_WIDTH = 300
const maxWidth = () => halfWindowWidth(MIN_WIDTH)

function NoteEditor({ quote }: { quote: WorkbenchAppQuote }) {
  const change = useOpenQuoteStore((s) => s.change)
  const working = useOpenQuoteStore(busy)
  const [note, setNote] = useState(quote.note)
  const [editedFrom, setEditedFrom] = useState(quote.note)
  // The quote's note changed under the editor (saved here or elsewhere): show it.
  if (quote.note !== editedFrom) {
    setEditedFrom(quote.note)
    setNote(quote.note)
  }
  const locked = quote.archived || !canEdit()
  return (
    <div className="flex flex-col gap-1">
      <textarea
        aria-label="Handover note"
        value={note}
        onChange={(event) => setNote(event.target.value)}
        disabled={locked || working}
        maxLength={4000}
        rows={3}
        placeholder="What a colleague picking this quote up should know"
        className="focus-ring w-full resize-y rounded-md px-1.5 py-1 text-xs"
        style={{ background: "var(--bg-input)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
      />
      <div className="flex items-center gap-2 text-[11px]" style={{ color: "var(--text-muted)" }}>
        {quote.note_by !== null && quote.note_at !== null && <span title={fullTime(quote.note_at)}>By {quote.note_by}, {relativeTime(new Date(quote.note_at))}</span>}
        <button type="button" className={`${BUTTON} ml-auto`} disabled={locked || working || note.trim() === quote.note} onClick={() => void change("note", { note: note.trim() }, quote)}>
          Save note
        </button>
      </div>
    </div>
  )
}

export default function HistoryPanel() {
  const quote = useOpenQuoteStore((s) => s.quote)
  const activity = useOpenQuoteStore((s) => s.activity)
  const setHistoryOpen = useOpenQuoteStore((s) => s.setHistoryOpen)
  const panelWidth = useWorkbenchViewStore((s) => s.panelWidth)
  const setPanelWidth = useWorkbenchViewStore((s) => s.setPanelWidth)
  if (quote === null) return null
  const linkStyle = { color: "var(--text-accent)" }
  const source = activity?.source ?? null
  const sourcePath = source === null ? null : quotePath(source.reference, source.option)
  return (
    <SidePanel
      width={panelWidth}
      onWidthChange={setPanelWidth}
      minWidth={MIN_WIDTH}
      maxWidth={maxWidth}
      testId="quote-history"
      title="History"
      subtitle={<span className="text-[11px]" style={{ color: "var(--text-muted)" }}>{quote.label}</span>}
      icon={<History size={14} style={{ color: "var(--accent)" }} />}
      onClose={() => setHistoryOpen(false)}
    >
      <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto p-2.5 text-xs">
        <Collapsible title="Note">
          <NoteEditor quote={quote} />
        </Collapsible>
        {activity === null ? (
          <p role="status" style={{ color: "var(--text-muted)" }}>
            Reading the history…
          </p>
        ) : (
          <>
            {(source !== null || activity.derived.length > 0) && (
              <Collapsible title="Related quotes" count={(source === null ? 0 : 1) + activity.derived.length}>
                <ul className="flex flex-col gap-0.5">
                  {source !== null && sourcePath !== null && (
                    <li>
                      Made as {RELATION[source.relation]} of{" "}
                      <a href={sourcePath} onClick={(event) => followLink(event, sourcePath)} style={linkStyle}>
                        {source.label}
                      </a>
                    </li>
                  )}
                  {activity.derived.map((made) => {
                    const path = quotePath(made.reference, made.option)
                    return (
                      <li key={made.label}>
                        <a href={path} onClick={(event) => followLink(event, path)} style={linkStyle}>
                          {made.label}
                        </a>{" "}
                        <span style={{ color: "var(--text-secondary)" }}>{RELATION[made.relation]}</span>
                      </li>
                    )
                  })}
                </ul>
              </Collapsible>
            )}
            <Collapsible title="Activity" count={activity.entries.length}>
              <ol className="flex flex-col gap-1">
                {activity.entries
                  .map((entry, at) => ({ entry, at }))
                  .reverse()
                  .map(({ entry, at }) => (
                    <li key={at} className="flex flex-col" data-testid={`activity-${entry.action}`}>
                      <span style={{ color: "var(--text-primary)" }}>
                        {entry.actor} {activityText(entry, source)}
                      </span>
                      <span className="text-[11px]" style={{ color: "var(--text-muted)" }} title={fullTime(entry.at)}>
                        {relativeTime(new Date(entry.at))}
                      </span>
                    </li>
                  ))}
              </ol>
            </Collapsible>
          </>
        )}
      </div>
    </SidePanel>
  )
}
