/**
 * The Quotes page (specs/workbench): where the underwriter app opens. On the left, the
 * lists (My work, Team, Archived) and the stages of the one showing, each with its count;
 * above the list its name, the search, in Team an assignee, the order and New quote; the
 * list itself a table at Haute's density, a page at a time, each row opening its quote and
 * offering Open, Duplicate, and Archive or Restore on hover or focus. The list showing is
 * in the page's address.
 */
import {
  Archive,
  ArchiveRestore,
  BadgeCheck,
  Calculator,
  ChevronDown,
  ChevronRight,
  CircleDashed,
  Copy,
  FileText,
  FolderOpen,
  Plus,
  RotateCw,
  Search,
  UserRound,
  Users,
  type LucideIcon,
} from "lucide-react"
import { useEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from "react"
import { changeAppQuote, type QuotesQuery } from "../api/app"
import { apiErrorMessage } from "../api/errors"
import type { WorkbenchAppQuote } from "../api/types"
import { isTypingTarget } from "../hooks/keyboardTargets"
import useToastStore from "../stores/useToastStore"
import { fullTime, listColumns, listValue, relativeTime } from "./quoteValues"
import { followLink, navigate, newQuotePath, quotePath, quotesPath, quotesQueryOf } from "./route"
import { BUTTON, FILL_BUTTON, FILL_STYLE, ICON_BUTTON, StageChip } from "./ui"
import useAppStore from "./useAppStore"
import useQuoteListStore from "./useQuoteListStore"

const VIEWS: ReadonlyArray<{ view: QuotesQuery["view"]; label: string; icon: LucideIcon; empty: string }> = [
  { view: "mine", label: "My work", icon: UserRound, empty: "No quotes are with you yet." },
  { view: "team", label: "Team", icon: Users, empty: "No quotes yet." },
  { view: "archived", label: "Archived", icon: Archive, empty: "Nothing is archived." },
]
const STAGES: ReadonlyArray<{ stage: NonNullable<QuotesQuery["stage"]>; label: string; icon: LucideIcon }> = [
  { stage: "draft", label: "Draft", icon: CircleDashed },
  { stage: "priced", label: "Priced", icon: Calculator },
  { stage: "final", label: "Final", icon: BadgeCheck },
]
const SORTS: ReadonlyArray<{ sort: QuotesQuery["sort"]; label: string }> = [
  { sort: "saved", label: "Newest saved" },
  { sort: "reference", label: "Newest reference" },
  { sort: "title", label: "Title" },
]
const SELECT = "focus-ring rounded-md px-1.5 py-1 text-xs"
const SELECT_STYLE = { border: "1px solid var(--border)", color: "var(--text-primary)" }

/** A list in the rail: its icon, name and count, picked or not. */
function RailItem({ icon: Icon, label, count, picked, onPick, testId }: { icon: LucideIcon; label: string; count: number | undefined; picked: boolean; onPick: () => void; testId: string }) {
  return (
    <button
      type="button"
      data-testid={testId}
      aria-current={picked ? "page" : undefined}
      onClick={onPick}
      className="focus-ring flex h-7 w-full items-center gap-2 rounded-md px-2 text-left text-xs transition-colors hover:bg-[var(--bg-hover)]"
      style={{ color: picked ? "var(--text-accent)" : "var(--text-primary)", background: picked ? "var(--accent-soft)" : undefined }}
    >
      <Icon size={13} aria-hidden="true" style={{ color: picked ? "var(--text-accent)" : "var(--text-secondary)" }} />
      <span className="flex-1 truncate">{label}</span>
      <span className="tabular-nums text-[11px]" style={{ color: "var(--text-muted)" }}>
        {count ?? ""}
      </span>
    </button>
  )
}

/** A collapsible group of the rail. */
function RailGroup({ title, children }: { title: string; children: React.ReactNode }) {
  const [open, setOpen] = useState(true)
  return (
    <div className="flex flex-col gap-0.5">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        className="flex h-6 items-center gap-1 px-1 text-[10px] font-semibold tracking-wider uppercase"
        style={{ color: "var(--text-muted)" }}
      >
        {open ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
        {title}
      </button>
      {open && children}
    </div>
  )
}

export default function QuotesPage({ query }: { query: QuotesQuery }) {
  const form = useAppStore((s) => s.form)
  const release = useAppStore((s) => s.release)
  const people = useAppStore((s) => s.people)
  const loadPeople = useAppStore((s) => s.loadPeople)
  const quotes = useQuoteListStore((s) => s.quotes)
  const counts = useQuoteListStore((s) => s.counts)
  const status = useQuoteListStore((s) => s.status)
  const error = useQuoteListStore((s) => s.error)
  const next = useQuoteListStore((s) => s.next)
  const loadingMore = useQuoteListStore((s) => s.loadingMore)
  const load = useQuoteListStore((s) => s.load)
  const more = useQuoteListStore((s) => s.more)
  const addToast = useToastStore((s) => s.addToast)
  const [typed, setTyped] = useState(query.search)
  const searchBox = useRef<HTMLInputElement>(null)
  const columns = listColumns(form)
  const principal = release?.principal ?? ""
  // One who may only read quotes is offered nothing that changes them.
  const editor = release?.can_edit ?? true

  const go = (change: Partial<QuotesQuery>, options: { replace?: boolean } = {}) => {
    void navigate(quotesPath({ ...query, ...change }), options)
  }

  // The list follows the address; the query is read anew each render, so its address is the key.
  const address = quotesPath(query)
  useEffect(() => {
    void load(quotesQueryOf(address.slice(address.indexOf("?") + 1)), true)
  }, [load, address])

  // A search reached from the address, by Back or a link, replaces what was typed.
  const [searched, setSearched] = useState(query.search)
  if (query.search !== searched) {
    setSearched(query.search)
    setTyped(query.search)
  }

  useEffect(() => {
    void loadPeople()
  }, [loadPeople])

  // The search typed reaches the address, and so the list, after a pause.
  useEffect(() => {
    if (typed === query.search) return
    const timer = window.setTimeout(() => go({ search: typed }, { replace: true }), 250)
    return () => window.clearTimeout(timer)
  })

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.defaultPrevented || isTypingTarget(event.target) || event.ctrlKey || event.metaKey || event.altKey) return
      if (event.key === "/") {
        event.preventDefault()
        searchBox.current?.focus()
      } else if ((event.key === "n" || event.key === "N") && editor) {
        event.preventDefault()
        void navigate(newQuotePath())
      }
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [editor])

  const archive = async (quote: WorkbenchAppQuote) => {
    try {
      const { quote: archived } = await changeAppQuote(quote.id, "archive", quote.version)
      void load(query, true)
      addToast("success", `Archived ${quote.reference}`, {
        label: "Undo",
        run: () => {
          void changeAppQuote(archived.id, "restore", archived.version)
            .then(() => load(query, true))
            .catch((reason: unknown) => addToast("error", `Could not restore ${quote.reference}: ${apiErrorMessage(reason)}`))
        },
      })
    } catch (reason: unknown) {
      addToast("error", `Could not archive ${quote.reference}: ${apiErrorMessage(reason)}`)
    }
  }

  const restore = async (quote: WorkbenchAppQuote) => {
    try {
      await changeAppQuote(quote.id, "restore", quote.version)
      void load(query, true)
      addToast("success", `Restored ${quote.reference}`)
    } catch (reason: unknown) {
      addToast("error", `Could not restore ${quote.reference}: ${apiErrorMessage(reason)}`)
    }
  }

  const view = VIEWS.find((each) => each.view === query.view) ?? VIEWS[0]
  const stage = STAGES.find((each) => each.stage === query.stage)
  const filtered = query.search !== "" || query.stage !== null || query.assignee !== null
  const span = 6 + columns.fields.length + (columns.headline === null ? 0 : 1)

  const onRowKey = (event: ReactKeyboardEvent, quote: WorkbenchAppQuote) => {
    if (event.key === "Enter" && event.target === event.currentTarget) void navigate(quotePath(quote.id))
  }

  return (
    <div className="flex min-h-0 flex-1" data-testid="quotes-page">
      <nav aria-label="Lists" className="flex w-[200px] shrink-0 flex-col gap-3 overflow-y-auto p-2" style={{ borderRight: "1px solid var(--chrome-border)", background: "var(--bg-panel)" }}>
        <RailGroup title="Quotes">
          {VIEWS.map((each) => (
            <RailItem
              key={each.view}
              testId={`list-${each.view}`}
              icon={each.icon}
              label={each.label}
              count={counts[each.view]}
              picked={query.view === each.view}
              onPick={() => go({ view: each.view, stage: null, assignee: null })}
            />
          ))}
        </RailGroup>
        <RailGroup title="Stage">
          {STAGES.map((each) => (
            <RailItem
              key={each.stage}
              testId={`stage-filter-${each.stage}`}
              icon={each.icon}
              label={each.label}
              count={counts[each.stage]}
              picked={query.stage === each.stage}
              onPick={() => go({ stage: query.stage === each.stage ? null : each.stage })}
            />
          ))}
        </RailGroup>
      </nav>

      <main className="flex min-w-0 flex-1 flex-col" aria-label="Quotes">
        <div className="flex flex-wrap items-center gap-2 px-4 py-2" style={{ borderBottom: "1px solid var(--border)" }}>
          <h2 className="text-[13px] font-semibold" style={{ color: "var(--text-primary)" }}>
            {view.label}
            {stage !== undefined && <span style={{ color: "var(--text-secondary)" }}> · {stage.label}</span>}
          </h2>
          <div className="ml-auto flex flex-wrap items-center gap-2">
            <label className="focus-within:ring-accent-ring flex items-center gap-1.5 rounded-md px-1.5" style={{ background: "var(--bg-input)", border: "1px solid var(--border)" }}>
              <Search size={12} aria-hidden="true" style={{ color: "var(--text-muted)" }} />
              <input
                ref={searchBox}
                aria-label="Search quotes"
                value={typed}
                onChange={(event) => setTyped(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Escape") setTyped("")
                }}
                placeholder="Search reference, name… ( / )"
                maxLength={200}
                className="w-56 bg-transparent py-1 text-xs outline-none"
                style={{ color: "var(--text-primary)" }}
              />
            </label>
            {query.view === "team" && (
              <select aria-label="Assigned to" value={query.assignee ?? ""} onChange={(event) => go({ assignee: event.target.value || null })} className={SELECT} style={SELECT_STYLE}>
                <option value="">Anyone</option>
                {people.map((person) => (
                  <option key={person} value={person}>
                    {person === principal ? `${person} (you)` : person}
                  </option>
                ))}
              </select>
            )}
            <select aria-label="Order" value={query.sort} onChange={(event) => go({ sort: event.target.value as QuotesQuery["sort"] })} className={SELECT} style={SELECT_STYLE}>
              {SORTS.map((each) => (
                <option key={each.sort} value={each.sort}>
                  {each.label}
                </option>
              ))}
            </select>
            <button type="button" className={ICON_BUTTON} onClick={() => void load(query, true)} title="Refresh the list" aria-label="Refresh the list">
              <RotateCw size={12} />
            </button>
            {editor && (
              <a href={newQuotePath()} onClick={(event) => followLink(event, newQuotePath())} className={FILL_BUTTON} style={FILL_STYLE} title="Start a new quote (N)" data-testid="new-quote">
                <Plus size={13} aria-hidden="true" /> New quote
              </a>
            )}
          </div>
        </div>

        <div className="min-h-0 flex-1 overflow-auto">
          <table className="w-full border-collapse text-xs" aria-label={view.label}>
            <thead className="sticky top-0 z-10" style={{ background: "var(--bg-elevated)" }}>
              <tr className="text-left text-[11px] font-semibold tracking-wider uppercase" style={{ color: "var(--text-secondary)" }}>
                <th className="h-7 w-28 px-3 font-semibold">Ref</th>
                <th className="h-7 px-2 font-semibold">{columns.title}</th>
                {columns.fields.map((column) => (
                  <th key={column.key} className={`h-7 px-2 font-semibold ${column.numeric ? "text-right" : ""}`}>
                    {column.label}
                  </th>
                ))}
                {columns.headline !== null && <th className="h-7 px-2 text-right font-semibold">{columns.headline.label}</th>}
                <th className="h-7 w-20 px-2 font-semibold">Stage</th>
                <th className="h-7 px-2 font-semibold">With</th>
                <th className="h-7 w-24 px-2 font-semibold">Saved</th>
                <th className="h-7 w-24 px-2" aria-label="Actions" />
              </tr>
            </thead>
            <tbody>
              {status === "loading" &&
                Array.from({ length: 6 }, (_, row) => (
                  <tr key={`placeholder-${row}`} aria-hidden="true" className="h-7" style={{ borderBottom: "1px solid var(--border-subtle)" }}>
                    <td colSpan={span} className="px-3">
                      <div className="h-2.5 animate-pulse rounded" style={{ background: "var(--bg-hover)", width: `${60 + ((row * 13) % 35)}%` }} />
                    </td>
                  </tr>
                ))}
              {status === "error" && (
                <tr>
                  <td colSpan={span} className="px-3 py-6 text-center">
                    <span role="alert" style={{ color: "var(--danger-text)" }}>
                      The quotes could not be read: {error}
                    </span>{" "}
                    <button type="button" className={BUTTON} onClick={() => void load(query, true)}>
                      <RotateCw size={12} /> Retry
                    </button>
                  </td>
                </tr>
              )}
              {status === "ready" && quotes.length === 0 && (
                <tr>
                  <td colSpan={span} className="px-3 py-10 text-center" style={{ color: "var(--text-secondary)" }} data-testid="quotes-empty">
                    {filtered ? (
                      <>
                        No quotes match.{" "}
                        <button type="button" className={BUTTON} onClick={() => { setTyped(""); go({ search: "", stage: null, assignee: null }) }}>
                          Clear search
                        </button>
                      </>
                    ) : (
                      <>
                        {view.empty}{" "}
                        {query.view !== "archived" && editor && (
                          <a href={newQuotePath()} onClick={(event) => followLink(event, newQuotePath())} className={BUTTON}>
                            <Plus size={12} /> New quote
                          </a>
                        )}
                      </>
                    )}
                  </td>
                </tr>
              )}
              {status !== "loading" &&
                quotes.map((quote) => (
                  <tr
                    key={quote.id}
                    tabIndex={0}
                    data-testid={`quote-row-${quote.reference}`}
                    onClick={() => void navigate(quotePath(quote.id))}
                    onKeyDown={(event) => onRowKey(event, quote)}
                    className="group/row h-7 cursor-pointer transition-colors outline-none hover:bg-[var(--bg-hover)] focus-visible:bg-[var(--bg-hover)]"
                    style={{ borderBottom: "1px solid var(--border-subtle)" }}
                  >
                    <td className="px-3 font-mono text-[11px]">
                      <a href={quotePath(quote.id)} onClick={(event) => { event.stopPropagation(); followLink(event, quotePath(quote.id)) }} style={{ color: "var(--text-accent)" }}>
                        {quote.reference}
                      </a>
                    </td>
                    <td className="max-w-[280px] truncate px-2 font-medium" style={{ color: "var(--text-primary)" }} title={quote.title}>
                      {quote.title}
                    </td>
                    {columns.fields.map((column, index) => (
                      <td key={column.key} className={`max-w-[200px] truncate px-2 ${column.numeric ? "text-right tabular-nums" : ""}`} style={{ color: "var(--text-secondary)" }}>
                        {listValue(column, quote.fields[index])}
                      </td>
                    ))}
                    {columns.headline !== null && (
                      <td className="px-2 text-right tabular-nums" style={{ color: "var(--text-primary)" }}>
                        {listValue(columns.headline, quote.headline)}
                      </td>
                    )}
                    <td className="px-2">
                      <span className="inline-flex gap-1">
                        <StageChip stage={quote.stage} />
                        {quote.archived_at !== null && query.view !== "archived" && <StageChip stage="archived" />}
                      </span>
                    </td>
                    <td className="max-w-[180px] truncate px-2" style={{ color: "var(--text-secondary)" }} title={quote.assignee}>
                      {quote.assignee === principal ? "You" : quote.assignee}
                    </td>
                    <td className="px-2 whitespace-nowrap" style={{ color: "var(--text-secondary)" }} title={`${fullTime(quote.last_saved_at)} by ${quote.last_saved_by}`}>
                      {relativeTime(quote.last_saved_at)}
                    </td>
                    <td className="px-2" onClick={(event) => event.stopPropagation()}>
                      <span className="flex justify-end gap-0.5 opacity-0 group-focus-within/row:opacity-100 group-hover/row:opacity-100">
                        <button type="button" className={ICON_BUTTON} onClick={() => void navigate(quotePath(quote.id))} title={`Open ${quote.reference}`} aria-label={`Open ${quote.reference}`}>
                          <FolderOpen size={12} />
                        </button>
                        {editor && (
                        <button
                          type="button"
                          className={ICON_BUTTON}
                          onClick={() => void navigate(newQuotePath({ quoteId: quote.id, revision: quote.latest, relation: "duplicate" }))}
                          title={`Duplicate ${quote.reference} as a new quote`}
                          aria-label={`Duplicate ${quote.reference}`}
                        >
                          <Copy size={12} />
                        </button>
                        )}
                        {!editor ? null : quote.archived_at === null ? (
                          <button type="button" className={ICON_BUTTON} onClick={() => void archive(quote)} title={`Archive ${quote.reference}`} aria-label={`Archive ${quote.reference}`}>
                            <Archive size={12} />
                          </button>
                        ) : (
                          <button type="button" className={ICON_BUTTON} onClick={() => void restore(quote)} title={`Restore ${quote.reference}`} aria-label={`Restore ${quote.reference}`}>
                            <ArchiveRestore size={12} />
                          </button>
                        )}
                      </span>
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
          {status === "ready" && quotes.length > 0 && (
            <div className="flex items-center gap-2 px-3 py-2 text-[11px]" style={{ color: "var(--text-muted)" }}>
              <FileText size={12} aria-hidden="true" />
              {next === null ? `${quotes.length} ${quotes.length === 1 ? "quote" : "quotes"}` : `First ${quotes.length}`}
              {next !== null && (
                <button type="button" className={BUTTON} onClick={() => void more()} disabled={loadingMore}>
                  {loadingMore ? "Loading…" : "Load more"}
                </button>
              )}
              {error !== null && <span style={{ color: "var(--danger-text)" }}>{error}</span>}
            </div>
          )}
        </div>
      </main>
    </div>
  )
}
