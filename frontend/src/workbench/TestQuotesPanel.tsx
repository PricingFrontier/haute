/**
 * The test quotes' panel (specs/workbench, Test quotes): what Test quotes opens beside the
 * sheets in Preview, on Haute's side panel. The quote on the sheets is saved here under a
 * name, or over the test quote opened last; the test-quote folder's files are listed by name,
 * each opened back onto the sheets, ticked for a run, accepted when a run moved it, or
 * deleted. A run shows each quote unchanged, moved (each field, the expected value beside the
 * new), no longer comparing or failed, with why; a file the panel cannot open shows why.
 */
import { Check, ChevronDown, ChevronRight, ClipboardCheck, FolderOpen, Play, Replace, Save, Square, Trash2 } from "lucide-react"
import { useState, type FormEvent } from "react"
import type { WorkbenchTestQuoteMove } from "../api/types"
import { SidePanel } from "../haute-ui"
import { confirmed } from "../stores/useQuestionStore"
import useWorkbenchTestQuotesStore, { type QuoteRun } from "../stores/useWorkbenchTestQuotesStore"
import useWorkbenchViewStore from "../stores/useWorkbenchViewStore"

const MIN_WIDTH = 320
/** At most half the window, so the sheet stays in view. */
const maxWidth = () => Math.max(MIN_WIDTH, Math.floor(window.innerWidth / 2))
const BUTTON = "toolbar-btn inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium disabled:opacity-50"
const ICON_BUTTON = "toolbar-btn inline-flex items-center rounded-md p-1 disabled:opacity-50"

type Outcome = Extract<QuoteRun, { status: "done" }>["outcome"]
const OUTCOME_TEXT: Record<Outcome, string> = {
  unchanged: "Unchanged",
  moved: "Moved",
  no_longer_compares: "No longer compares",
  failed: "Failed",
}
const OUTCOME_COLOR: Record<Outcome, string> = {
  unchanged: "var(--success)",
  moved: "var(--warning)",
  no_longer_compares: "var(--warning)",
  failed: "var(--danger)",
}

/** A value as a move shows it: text as it is, empty as such, anything else as JSON, kept short. */
function shown(value: unknown): string {
  const text = value === null || value === undefined ? "empty" : typeof value === "string" ? value : JSON.stringify(value)
  return text.length > 80 ? `${text.slice(0, 79)}…` : text
}

function MoveLine({ move }: { move: WorkbenchTestQuoteMove }) {
  const field = move.column === null ? move.table : `${move.table}.${move.column}`
  const place = move.row === null ? field : `${field} (${move.row})`
  return (
    <li className="flex flex-wrap items-baseline gap-x-1.5 font-mono text-[11px]">
      <span style={{ color: "var(--text-primary)" }}>{place}</span>
      {move.column === null ? (
        <span style={{ color: "var(--text-secondary)" }}>{move.after === true ? "row added" : "row removed"}</span>
      ) : (
        <span style={{ color: "var(--text-secondary)" }}>
          {shown(move.before)} → <span style={{ color: "var(--warning)" }}>{shown(move.after)}</span>
        </span>
      )}
    </li>
  )
}

export default function TestQuotesPanel() {
  const quotes = useWorkbenchTestQuotesStore((s) => s.quotes)
  const loading = useWorkbenchTestQuotesStore((s) => s.loading)
  const error = useWorkbenchTestQuotesStore((s) => s.error)
  const notice = useWorkbenchTestQuotesStore((s) => s.notice)
  const busy = useWorkbenchTestQuotesStore((s) => s.busy)
  const opened = useWorkbenchTestQuotesStore((s) => s.opened)
  const ticked = useWorkbenchTestQuotesStore((s) => s.ticked)
  const runs = useWorkbenchTestQuotesStore((s) => s.runs)
  const running = useWorkbenchTestQuotesStore((s) => s.running)
  const setOpen = useWorkbenchTestQuotesStore((s) => s.setOpen)
  const save = useWorkbenchTestQuotesStore((s) => s.save)
  const openQuote = useWorkbenchTestQuotesStore((s) => s.openQuote)
  const toggle = useWorkbenchTestQuotesStore((s) => s.toggle)
  const run = useWorkbenchTestQuotesStore((s) => s.run)
  const stop = useWorkbenchTestQuotesStore((s) => s.stop)
  const accept = useWorkbenchTestQuotesStore((s) => s.accept)
  const acceptAll = useWorkbenchTestQuotesStore((s) => s.acceptAll)
  const remove = useWorkbenchTestQuotesStore((s) => s.remove)
  const panelWidth = useWorkbenchViewStore((s) => s.panelWidth)
  const setPanelWidth = useWorkbenchViewStore((s) => s.setPanelWidth)
  const [name, setName] = useState("")
  const [expanded, setExpanded] = useState<string | null>(null)

  const results = Object.values(runs).filter((r): r is Extract<QuoteRun, { status: "done" }> => r.status === "done")
  const count = (outcome: Outcome) => results.filter((r) => r.outcome === outcome).length
  const acceptable = results.filter((r) => r.outcome === "moved" || r.outcome === "no_longer_compares").length
  const openable = quotes.filter((quote) => quote.problem === null)
  const waiting = busy || running !== null

  const onSave = async (event: FormEvent) => {
    event.preventDefault()
    if (await save({ name: name.trim() })) setName("")
  }
  const onReplace = async () => {
    if (opened === null) return
    if (await confirmed({ title: `Replace the test quote '${opened}' with the quote on the sheets?`, confirm: "Replace" })) {
      await save({ replace: opened })
    }
  }

  return (
    <SidePanel
      width={panelWidth}
      onWidthChange={setPanelWidth}
      minWidth={MIN_WIDTH}
      maxWidth={maxWidth}
      testId="workbench-test-quotes"
      title="Test quotes"
      icon={<ClipboardCheck size={14} style={{ color: "var(--accent)" }} />}
      onClose={() => setOpen(false)}
    >
      <div className="flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto p-2.5 text-xs">
        <form onSubmit={(event) => { void onSave(event) }} className="flex items-center gap-1.5" aria-label="Save the quote on the sheets">
          <input
            aria-label="Test quote name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="Name"
            maxLength={100}
            className="focus-ring min-w-0 flex-1 rounded-md px-1.5 py-1 text-xs"
            style={{ background: "var(--bg-input)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
          />
          <button type="submit" className={BUTTON} disabled={waiting || name.trim() === ""}>
            <Save size={12} /> Save as test quote
          </button>
        </form>
        {opened !== null && (
          <button type="button" className={`${BUTTON} self-start`} disabled={waiting} onClick={() => { void onReplace() }}>
            <Replace size={12} /> Replace {opened}
          </button>
        )}

        <div className="flex flex-wrap items-center gap-1.5">
          {running === null ? (
            <button type="button" className={BUTTON} disabled={busy || openable.length === 0} onClick={() => { void run() }}>
              <Play size={12} /> {ticked.length > 0 ? `Run ${ticked.length} ticked` : "Run all"}
            </button>
          ) : (
            <button type="button" className={BUTTON} disabled={running.stopping} onClick={stop}>
              <Square size={12} /> Stop
            </button>
          )}
          <button type="button" className={BUTTON} disabled={waiting || acceptable === 0} onClick={() => { void acceptAll() }}>
            <Check size={12} /> Accept all
          </button>
          <span role="status" style={{ color: "var(--text-secondary)" }}>
            {running !== null
              ? `Running ${Math.min(running.done + 1, running.of)} of ${running.of}`
              : results.length > 0
                ? `${count("moved")} moved, ${count("no_longer_compares")} no longer compare, ${count("failed")} failed, ${count("unchanged")} unchanged`
                : loading
                  ? "Loading…"
                  : busy
                    ? "Saving…"
                    : null}
          </span>
        </div>
        {error !== null && (
          <p role="alert" style={{ color: "var(--danger)" }}>
            {error}
          </p>
        )}
        {notice !== null && <p style={{ color: "var(--text-secondary)" }}>{notice}</p>}

        {quotes.length === 0 && !loading ? (
          <p style={{ color: "var(--text-muted)" }}>No test quotes yet.</p>
        ) : (
          <ul aria-label="Test quotes" className="flex flex-col">
            {quotes.map((quote) => {
              const result = runs[quote.file]
              const done = result?.status === "done" ? result : null
              const open = expanded === quote.file
              const detail =
                quote.problem !== null ||
                (done !== null && (done.moves.length > 0 || done.removed.length > 0 || done.why !== null))
              return (
                <li key={quote.file} className="flex flex-col border-b py-1" style={{ borderColor: "var(--border)" }}>
                  <div className="flex items-center gap-1.5">
                    <input
                      type="checkbox"
                      aria-label={`Tick ${quote.name} for a run`}
                      checked={ticked.includes(quote.file)}
                      disabled={quote.problem !== null}
                      onChange={() => toggle(quote.file)}
                    />
                    <button
                      type="button"
                      className="flex min-w-0 flex-1 items-center gap-1 text-left"
                      onClick={() => setExpanded(open ? null : quote.file)}
                      aria-expanded={detail ? open : undefined}
                      disabled={!detail}
                    >
                      {detail ? open ? <ChevronDown size={12} /> : <ChevronRight size={12} /> : <span className="w-3" />}
                      <span className="truncate font-medium" style={{ color: quote.problem === null ? "var(--text-primary)" : "var(--text-muted)" }}>
                        {quote.name}
                      </span>
                    </button>
                    {result?.status === "running" && <span style={{ color: "var(--text-muted)" }}>Running…</span>}
                    {done !== null && (
                      <span className="shrink-0" style={{ color: OUTCOME_COLOR[done.outcome] }} data-testid={`test-quote-outcome-${quote.file}`}>
                        {OUTCOME_TEXT[done.outcome]}
                      </span>
                    )}
                    {quote.problem === null && (
                      <button type="button" className={ICON_BUTTON} onClick={() => { void openQuote(quote.file) }} aria-label={`Open ${quote.name}`}>
                        <FolderOpen size={12} />
                      </button>
                    )}
                    {(done?.outcome === "moved" || done?.outcome === "no_longer_compares") && (
                      <button type="button" className={ICON_BUTTON} disabled={waiting} onClick={() => { void accept(quote.file) }} aria-label={`Accept ${quote.name}`}>
                        <Check size={12} />
                      </button>
                    )}
                    {quote.problem === null && (
                      <button
                        type="button"
                        className={ICON_BUTTON}
                        disabled={waiting}
                        onClick={() => void (async () => {
                          if (await confirmed({ title: `Delete the test quote '${quote.name}'?`, confirm: "Delete", danger: true })) await remove(quote.file)
                        })()}
                        aria-label={`Delete ${quote.name}`}
                      >
                        <Trash2 size={12} />
                      </button>
                    )}
                  </div>
                  {open && (
                    <div className="ml-6 mt-1 flex flex-col gap-1">
                      {quote.problem !== null && <p style={{ color: "var(--text-secondary)" }}>Cannot open {quote.file}: {quote.problem}.</p>}
                      {done?.why !== null && done?.why !== undefined && (
                        <p style={{ color: done.outcome === "failed" ? "var(--danger)" : "var(--warning)" }}>{done.why}</p>
                      )}
                      {done !== null && done.moves.length > 0 && (
                        <ul aria-label={`What moved in ${quote.name}`} className="flex flex-col gap-0.5">
                          {done.moves.map((move, index) => <MoveLine key={index} move={move} />)}
                        </ul>
                      )}
                      {done !== null && done.removed.length > 0 && (
                        <p style={{ color: "var(--text-secondary)" }}>No longer on the form: {done.removed.join(", ")}</p>
                      )}
                    </div>
                  )}
                </li>
              )
            })}
          </ul>
        )}
      </div>
    </SidePanel>
  )
}
