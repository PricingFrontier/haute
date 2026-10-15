/**
 * A quote's history in the underwriter app (specs/workbench), on Haute's side panel: the
 * handover note, edited here; the quote it came from and those made from it; its revisions,
 * each opened as it was; and what was done to it, by whom and when, newest first.
 */
import { ChevronDown, ChevronRight, History } from "lucide-react"
import { useState, type ReactNode } from "react"
import type { WorkbenchAppQuote } from "../api/types"
import { SidePanel } from "../haute-ui"
import useWorkbenchViewStore from "../stores/useWorkbenchViewStore"
import { RELATION_TEXT as RELATION, eventText, fullTime, relativeTime } from "./quoteValues"
import { followLink, quotePath, revisionPath } from "./route"
import { BUTTON } from "./ui"
import useOpenQuoteStore, { canEdit } from "./useOpenQuoteStore"

const MIN_WIDTH = 300
const maxWidth = () => Math.max(MIN_WIDTH, Math.floor(window.innerWidth / 2))

function Section({ title, count, children }: { title: string; count?: number; children: ReactNode }) {
  const [open, setOpen] = useState(true)
  return (
    <section className="flex flex-col gap-1" aria-label={title}>
      <button type="button" onClick={() => setOpen(!open)} aria-expanded={open} className="flex h-6 items-center gap-1 text-[10px] font-semibold tracking-wider uppercase" style={{ color: "var(--text-muted)" }}>
        {open ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
        {title}
        {count !== undefined && <span className="ml-1 tabular-nums">{count}</span>}
      </button>
      {open && children}
    </section>
  )
}

function NoteEditor({ quote }: { quote: WorkbenchAppQuote }) {
  const change = useOpenQuoteStore((s) => s.change)
  const changing = useOpenQuoteStore((s) => s.changing)
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
        disabled={locked || changing}
        maxLength={4000}
        rows={3}
        placeholder="What a colleague picking this quote up should know"
        className="focus-ring w-full resize-y rounded-md px-1.5 py-1 text-xs"
        style={{ background: "var(--bg-input)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
      />
      <div className="flex items-center gap-2 text-[11px]" style={{ color: "var(--text-muted)" }}>
        {quote.note_by !== null && quote.note_at !== null && <span title={fullTime(quote.note_at)}>By {quote.note_by}, {relativeTime(quote.note_at)}</span>}
        <button type="button" className={`${BUTTON} ml-auto`} disabled={locked || changing || note.trim() === quote.note} onClick={() => void change("note", { note: note.trim() }, quote)}>
          Save note
        </button>
      </div>
    </div>
  )
}

export default function HistoryPanel() {
  const quote = useOpenQuoteStore((s) => s.quote)
  const revision = useOpenQuoteStore((s) => s.revision)
  const history = useOpenQuoteStore((s) => s.history)
  const setHistoryOpen = useOpenQuoteStore((s) => s.setHistoryOpen)
  const panelWidth = useWorkbenchViewStore((s) => s.panelWidth)
  const setPanelWidth = useWorkbenchViewStore((s) => s.setPanelWidth)
  if (quote === null) return null
  const linkStyle = { color: "var(--text-accent)" }
  return (
    <SidePanel
      width={panelWidth}
      onWidthChange={setPanelWidth}
      minWidth={MIN_WIDTH}
      maxWidth={maxWidth}
      testId="quote-history"
      title="History"
      subtitle={<span className="text-[11px]" style={{ color: "var(--text-muted)" }}>{quote.reference}</span>}
      icon={<History size={14} style={{ color: "var(--accent)" }} />}
      onClose={() => setHistoryOpen(false)}
    >
      <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto p-2.5 text-xs">
        <Section title="Note">
          <NoteEditor quote={quote} />
        </Section>
        {history === null ? (
          <p role="status" style={{ color: "var(--text-muted)" }}>
            Reading the history…
          </p>
        ) : (
          <>
            {(history.source || history.derived.length > 0) && (
              <Section title="Related quotes" count={(history.source ? 1 : 0) + history.derived.length}>
                <ul className="flex flex-col gap-0.5">
                  {history.source && (
                    <li>
                      Made as {RELATION[history.source.relation]} of{" "}
                      <a href={revisionPath(history.source.id, history.source.revision)} onClick={(event) => followLink(event, revisionPath(history.source?.id ?? "", history.source?.revision ?? 1))} style={linkStyle}>
                        {history.source.reference}
                      </a>{" "}
                      revision {history.source.revision}
                    </li>
                  )}
                  {history.derived.map((made) => (
                    <li key={made.id}>
                      <a href={quotePath(made.id)} onClick={(event) => followLink(event, quotePath(made.id))} style={linkStyle}>
                        {made.reference}
                      </a>{" "}
                      <span style={{ color: "var(--text-secondary)" }}>
                        {RELATION[made.relation]} from revision {made.revision}
                      </span>
                    </li>
                  ))}
                </ul>
              </Section>
            )}
            <Section title="Revisions" count={history.revisions.length}>
              <ul className="flex flex-col">
                {[...history.revisions].reverse().map((each) => {
                  const showing = revision?.number === each.number
                  const path = each.number === quote.latest ? quotePath(quote.id) : revisionPath(quote.id, each.number)
                  return (
                    <li key={each.number} className="flex h-6 items-center gap-1.5" style={{ color: showing ? "var(--text-primary)" : "var(--text-secondary)" }}>
                      <a href={path} onClick={(event) => followLink(event, path)} style={linkStyle} aria-current={showing ? "true" : undefined}>
                        Revision {each.number}
                      </a>
                      {quote.final_revision === each.number && <span style={{ color: "var(--quote-final-text)" }}>final</span>}
                      <span>{each.priced ? "priced" : "unpriced"}</span>
                      <span className="ml-auto truncate" title={fullTime(each.saved_at)}>
                        {each.saved_by}, {relativeTime(each.saved_at)}
                      </span>
                    </li>
                  )
                })}
              </ul>
            </Section>
            <Section title="Activity" count={history.events.length}>
              <ol className="flex flex-col gap-1">
                {[...history.events].reverse().map((event) => (
                  <li key={event.id} className="flex flex-col" data-testid={`event-${event.kind}`}>
                    <span style={{ color: "var(--text-primary)" }}>
                      {event.actor} {eventText(event)}
                    </span>
                    <span className="text-[11px]" style={{ color: "var(--text-muted)" }} title={fullTime(event.at)}>
                      {relativeTime(event.at)}
                    </span>
                  </li>
                ))}
              </ol>
            </Section>
          </>
        )}
      </div>
    </SidePanel>
  )
}
