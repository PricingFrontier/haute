/**
 * The Quotes page (specs/workbench): where the underwriter app opens. On the left, the
 * lists (My work, Team, Archived) and the stages of the one showing, each with its count;
 * above the list its name, the search, in Team an assignee, the order and New quote, which
 * opens the New quote window over the list as it is; the list itself a table at Haute's
 * density, a page at a time, each row always offering Read, Edit (for one who may change
 * quotes, unless someone else is editing it) and a menu of what is done to the quote as a
 * whole: Duplicate, Renew and Create
 * alternative (the window again, filled from the quote), Archive or Restore, and Copy link. The
 * list showing is in the page's address.
 */
import {
  Archive,
  ArchiveRestore,
  BadgeCheck,
  CircleDashed,
  Copy,
  FileText,
  GitBranchPlus,
  Link2,
  MoreHorizontal,
  Plus,
  RefreshCcw,
  RotateCw,
  Search,
  UserRound,
  Users,
  type LucideIcon,
} from "lucide-react"
import { useEffect, useRef, useState, type MouseEvent } from "react"
import { changeAppQuote, type QuotesQuery } from "../api/app"
import type { FormSpec, WorkbenchAppQuote } from "../api/types"
import { QUOTE_DETAILS, type QuoteDetailName } from "../utils/workbenchForm"
import { inModalDialog, isFormControl } from "../hooks/keyboardTargets"
import { useRequestKind } from "../hooks/useRequestKind"
import useToastStore from "../stores/useToastStore"
import { answer, under } from "../utils/answers"
import { relativeTime } from "../utils/relativeTime"
import { appErrorMessage, detailLabel, fullTime, listColumns, listValue, QUOTES_RECHECK_MS } from "./quoteValues"
import { navigate, newQuotePath, quotePath, quotesPath, quotesQueryOf } from "./route"
import { BUTTON, Collapsible, FILL_BUTTON, FILL_STYLE, ICON_BUTTON, Menu, ROW_BUTTON, ROW_FILL_BUTTON, StageChip, type MenuItem } from "./ui"
import useAppStore from "./useAppStore"
import useNewQuoteStore from "./useNewQuoteStore"
import { editQuote } from "./useOpenQuoteStore"
import useQuoteListStore from "./useQuoteListStore"

const VIEWS: ReadonlyArray<{ view: QuotesQuery["view"]; label: string; icon: LucideIcon; empty: string }> = [
  { view: "mine", label: "My work", icon: UserRound, empty: "No quotes are with you yet." },
  { view: "team", label: "Team", icon: Users, empty: "No quotes yet." },
  { view: "archived", label: "Archived", icon: Archive, empty: "Nothing is archived." },
]
const STAGES: ReadonlyArray<{ stage: NonNullable<QuotesQuery["stage"]>; label: string; icon: LucideIcon }> = [
  { stage: "draft", label: "Draft", icon: CircleDashed },
  { stage: "final", label: "Final", icon: BadgeCheck },
]
/** The orders, each by the quote_details column it is by, named as the schema names it, or by when saved. */
const SORTS: ReadonlyArray<{ sort: QuotesQuery["sort"]; by: QuoteDetailName | null }> = [
  { sort: "saved", by: null },
  { sort: "reference", by: QUOTE_DETAILS.quote_reference },
  { sort: "insured", by: QUOTE_DETAILS.insured },
]
const sortLabel = (by: QuoteDetailName | null, form: FormSpec | null): string => (by === null ? "Newest saved" : detailLabel(form, by))
const SELECT = "focus-ring rounded-md px-1.5 py-1 text-xs"
const SELECT_STYLE = { border: "1px solid var(--border)", color: "var(--text-primary)" }

/** New quote's link: the window over the list; a press with a modifier opens its address as the browser would. */
function openNewQuote(event: MouseEvent): void {
  if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return
  event.preventDefault()
  useNewQuoteStore.getState().openNew()
}

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

export default function QuotesPage({ query }: { query: QuotesQuery }) {
  const form = useAppStore((s) => s.form)
  const release = useAppStore((s) => s.release)
  const people = useAppStore((s) => s.people)
  const loadPeople = useAppStore((s) => s.loadPeople)
  const quotes = useQuoteListStore((s) => s.quotes)
  const counts = useQuoteListStore((s) => s.counts)
  const status = useQuoteListStore((s) => s.status)
  const error = useQuoteListStore((s) => s.error)
  const storeRefused = useQuoteListStore((s) => s.storeRefused)
  const refusals = useQuoteListStore((s) => s.refusals)
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

  // A list the quote store refused is asked for again on the store's own bound, so tables
  // put right in SQL list the quotes with no reload; each answer that refuses it again
  // arms the next (`refusals`), the same refusal as the last among them.
  useEffect(() => {
    if (status !== "error" || !storeRefused) return
    const timer = window.setTimeout(() => void useQuoteListStore.getState().reload(), QUOTES_RECHECK_MS)
    return () => window.clearTimeout(timer)
  }, [status, storeRefused, refusals])

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
      // A key pressed in a window over the page, New quote's among them, is the window's, and
      // one in a control is the control's: a select's type-ahead takes its letters.
      if (event.defaultPrevented || inModalDialog(event.target) || isFormControl(event.target) || event.ctrlKey || event.metaKey || event.altKey) return
      if (event.key === "/") {
        event.preventDefault()
        searchBox.current?.focus()
      } else if ((event.key === "n" || event.key === "N") && editor) {
        event.preventDefault()
        useNewQuoteStore.getState().openNew()
      }
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [editor])

  // An archive or a restore answers while the page shows.
  const page = useRequestKind()
  const archive = async (quote: WorkbenchAppQuote) => {
    await answer([under(page)], changeAppQuote(quote.reference, quote.option, "archive", quote.version, {}), {
      ok: ({ quote: archived }) => {
        // The list shown once it is answered, which may be another than the one archived from.
        void useQuoteListStore.getState().reload()
        addToast("success", `Archived ${quote.label}`, {
          label: "Undo",
          run: () => {
            // The list shown when Undo is pressed, which may be another than the one archived from.
            void answer([under(page)], changeAppQuote(archived.reference, archived.option, "restore", archived.version, {}), {
              ok: () => useQuoteListStore.getState().reload(),
              failed: (reason) => addToast("error", `Could not restore ${quote.label}: ${appErrorMessage(reason, { oneLine: true })}`),
            })
          },
        })
      },
      failed: (reason) => addToast("error", `Could not archive ${quote.label}: ${appErrorMessage(reason, { oneLine: true })}`),
    })
  }

  const restore = async (quote: WorkbenchAppQuote) => {
    await answer([under(page)], changeAppQuote(quote.reference, quote.option, "restore", quote.version, {}), {
      ok: () => {
        void useQuoteListStore.getState().reload()
        addToast("success", `Restored ${quote.label}`)
      },
      failed: (reason) => addToast("error", `Could not restore ${quote.label}: ${appErrorMessage(reason, { oneLine: true })}`),
    })
  }

  const copyLink = async (quote: WorkbenchAppQuote) => {
    try {
      await navigator.clipboard.writeText(`${window.location.origin}${quotePath(quote.reference, quote.option)}`)
      addToast("success", `Link to ${quote.label} copied`)
    } catch {
      addToast("error", "This browser would not copy the link")
    }
  }

  /** What is done to `quote` as a whole, from its row: for one who may only read quotes, Copy link alone. */
  const actions = (quote: WorkbenchAppQuote): MenuItem[] => {
    const from = { reference: quote.reference, option: quote.option }
    const derive = (relation: "duplicate" | "renewal" | "alternative") => () => void useNewQuoteStore.getState().openDerived({ ...from, relation }, { label: quote.label })
    const changing: MenuItem[] = !editor
      ? []
      : [
          { label: "Duplicate", icon: Copy, run: derive("duplicate"), title: "A new quote with this quote's values" },
          { label: "Renew", icon: RefreshCcw, run: derive("renewal"), title: "A new quote renewing this one, from its values" },
          { label: "Create alternative", icon: GitBranchPlus, run: derive("alternative"), title: "A new option under this reference, from this quote's values" },
          quote.archived ? { label: "Restore", icon: ArchiveRestore, run: () => void restore(quote) } : { label: "Archive", icon: Archive, run: () => void archive(quote) },
        ]
    return [...changing, { label: "Copy link", icon: Link2, run: () => void copyLink(quote) }]
  }

  const view = VIEWS.find((each) => each.view === query.view) ?? VIEWS[0]
  const stage = STAGES.find((each) => each.stage === query.stage)
  const filtered = query.search !== "" || query.stage !== null || query.assignee !== null
  const span = 6 + columns.length

  return (
    <div className="flex min-h-0 flex-1" data-testid="quotes-page">
      <nav aria-label="Lists" className="flex w-[200px] shrink-0 flex-col gap-3 overflow-y-auto p-2" style={{ borderRight: "1px solid var(--chrome-border)", background: "var(--bg-panel)" }}>
        <Collapsible title="Quotes">
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
        </Collapsible>
        <Collapsible title="Stage">
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
        </Collapsible>
      </nav>

      <main className="flex min-w-0 flex-1 flex-col" aria-label="Quotes">
        <div className="flex flex-wrap items-center gap-2 px-4 py-2" style={{ borderBottom: "1px solid var(--border)" }}>
          <h2 className="text-[13px] font-semibold" style={{ color: "var(--text-primary)" }}>
            {view.label}
            {stage !== undefined && <span style={{ color: "var(--text-secondary)" }}> · {stage.label}</span>}
          </h2>
          <div className="ml-auto flex flex-wrap items-center gap-2">
            <label className="flex items-center gap-1.5 rounded-md border border-[var(--border)] px-1.5 focus-within:border-[var(--accent-ring)] focus-within:shadow-[0_0_0_2px_var(--accent-soft)]" style={{ background: "var(--bg-input)" }}>
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
                  {sortLabel(each.by, form)}
                </option>
              ))}
            </select>
            <button type="button" className={ICON_BUTTON} onClick={() => void load(query, true)} title="Refresh the list" aria-label="Refresh the list">
              <RotateCw size={12} />
            </button>
            {editor && (
              <a href={newQuotePath()} onClick={openNewQuote} className={FILL_BUTTON} style={FILL_STYLE} title="Start a new quote (N)" data-testid="new-quote">
                <Plus size={13} aria-hidden="true" /> New quote
              </a>
            )}
          </div>
        </div>

        <div className="min-h-0 flex-1 overflow-auto">
          <table className="w-full border-collapse text-xs" aria-label={view.label}>
            <thead className="sticky top-0 z-10" style={{ background: "var(--bg-elevated)" }}>
              <tr className="text-left text-[11px] font-semibold tracking-wider whitespace-nowrap uppercase" style={{ color: "var(--text-secondary)" }}>
                <th className="h-7 px-3 font-semibold">{detailLabel(form, QUOTE_DETAILS.quote_reference)}</th>
                <th className="h-7 px-2 font-semibold">{detailLabel(form, QUOTE_DETAILS.quote_name)}</th>
                {columns.map((column) => (
                  <th key={column.key} className="h-7 px-2 font-semibold">
                    {column.label}
                  </th>
                ))}
                <th className="h-7 w-20 px-2 font-semibold">Stage</th>
                <th className="h-7 px-2 font-semibold">With</th>
                <th className="h-7 w-24 px-2 font-semibold">Saved</th>
                <th className="h-7 px-2" aria-label="Actions" />
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
                    {/* A refusal naming what does not fit reads as it is written: a table to a line, its columns under it. */}
                    <span role="alert" className="inline-block whitespace-pre-wrap text-left align-top" style={{ color: "var(--danger-text)" }} data-testid="quotes-error">
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
                          <a href={newQuotePath()} onClick={openNewQuote} className={BUTTON}>
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
                  // A row opens nothing, its reference included: Read and Edit open the quote.
                  <tr
                    key={quote.label}
                    data-testid={`quote-row-${quote.label}`}
                    className="h-7 transition-colors hover:bg-[var(--bg-hover)]"
                    style={{ borderBottom: "1px solid var(--border-subtle)" }}
                  >
                    <td className="px-3 font-mono text-[11px] whitespace-nowrap">
                      <span style={{ color: "var(--text-accent)" }}>{quote.label}</span>
                    </td>
                    <td className="max-w-[280px] truncate px-2 font-medium" style={{ color: "var(--text-primary)" }} title={quote.title}>
                      {quote.title}
                    </td>
                    {columns.map((column) => (
                      <td key={column.key} className="max-w-[200px] truncate px-2 whitespace-nowrap" style={{ color: "var(--text-secondary)" }}>
                        {listValue(column, quote.details[column.key])}
                      </td>
                    ))}
                    <td className="px-2">
                      <span className="inline-flex gap-1">
                        <StageChip stage={quote.stage} />
                        {quote.archived && query.view !== "archived" && <StageChip stage="archived" />}
                      </span>
                    </td>
                    <td className="max-w-[180px] truncate px-2" style={{ color: "var(--text-secondary)" }} title={quote.assignee}>
                      {quote.assignee === principal ? "You" : quote.assignee}
                    </td>
                    <td className="px-2 whitespace-nowrap" style={{ color: "var(--text-secondary)" }} title={`${fullTime(quote.saved_at)} by ${quote.saved_by}`}>
                      {relativeTime(new Date(quote.saved_at))}
                    </td>
                    <td className="px-2">
                      <span className="flex items-center justify-end gap-0.5">
                        <button
                          type="button"
                          className={ROW_BUTTON}
                          onClick={() => void navigate(quotePath(quote.reference, quote.option))}
                          title={`Read ${quote.label}, changing nothing`}
                          aria-label={`Read ${quote.label}`}
                        >
                          Read
                        </button>
                        {editor && (
                          <button
                            type="button"
                            className={ROW_FILL_BUTTON}
                            style={FILL_STYLE}
                            onClick={() => void editQuote(quote)}
                            title={`Edit ${quote.label}, held as yours while you do`}
                            aria-label={`Edit ${quote.label}`}
                          >
                            Edit
                          </button>
                        )}
                        <Menu label={`Actions for ${quote.label}`} icon={MoreHorizontal} items={actions(quote)} testId={`quote-actions-${quote.label}`} />
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
