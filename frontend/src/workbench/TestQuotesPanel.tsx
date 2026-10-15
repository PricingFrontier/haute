/**
 * The test quotes panel (specs/workbench): what Test quotes opens on the right in Preview, on
 * Haute's side panel. The quote on the sheets is saved here under a name; the project's test
 * quotes are listed, the most recently saved first, each opened back onto the sheets, ticked
 * for a run, accepted when a run changed it, or deleted. A run shows each quote unchanged,
 * changed (every difference by its place, the accepted value beside the new) or failed, and
 * says when the pipeline, the source or the form has changed since it started.
 */
import { Check, ChevronDown, ChevronRight, FlaskConical, FolderOpen, Play, Save, Trash2 } from "lucide-react"
import { useEffect, useState, type FormEvent } from "react"
import type { WorkbenchTestQuoteChange } from "../api/types"
import { SidePanel } from "../haute-ui"
import useWorkbenchFormStore from "../stores/useWorkbenchFormStore"
import { usePricingContext } from "../stores/useWorkbenchPricingStore"
import useWorkbenchTestQuotesStore, { runIsStale, type QuoteRun } from "../stores/useWorkbenchTestQuotesStore"
import useWorkbenchViewStore from "../stores/useWorkbenchViewStore"

const MIN_WIDTH = 320
/** At most half the window, so the sheet stays in view. */
const maxWidth = () => Math.max(MIN_WIDTH, Math.floor(window.innerWidth / 2))
const BUTTON = "toolbar-btn inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium disabled:opacity-50"
const ICON_BUTTON = "toolbar-btn inline-flex items-center rounded-md p-1 disabled:opacity-50"

const STATUS_TEXT: Record<QuoteRun["status"], string> = {
  running: "Running…",
  unchanged: "Unchanged",
  changed: "Changed",
  failed: "Failed",
}
const STATUS_COLOR: Record<QuoteRun["status"], string> = {
  running: "var(--text-muted)",
  unchanged: "var(--success)",
  changed: "var(--warning)",
  failed: "var(--danger)",
}

/** A value as a change shows it: text as it is, anything else as JSON, kept short. */
function shown(value: unknown): string {
  const text = typeof value === "string" ? value : JSON.stringify(value)
  return text.length > 80 ? `${text.slice(0, 79)}…` : text
}

function ChangeLine({ change }: { change: WorkbenchTestQuoteChange }) {
  return (
    <li className="flex flex-wrap items-baseline gap-x-1.5 font-mono text-[11px]">
      <span style={{ color: "var(--text-primary)" }}>{change.path}</span>
      {change.kind === "added" ? (
        <span style={{ color: "var(--text-secondary)" }}>added {shown(change.priced)}</span>
      ) : change.kind === "removed" ? (
        <span style={{ color: "var(--text-secondary)" }}>removed (was {shown(change.accepted)})</span>
      ) : (
        <span style={{ color: "var(--text-secondary)" }}>
          {shown(change.accepted)} → <span style={{ color: "var(--warning)" }}>{shown(change.priced)}</span>
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
  const saving = useWorkbenchTestQuotesStore((s) => s.saving)
  const selected = useWorkbenchTestQuotesStore((s) => s.selected)
  const runs = useWorkbenchTestQuotesStore((s) => s.runs)
  const running = useWorkbenchTestQuotesStore((s) => s.running)
  const accepting = useWorkbenchTestQuotesStore((s) => s.accepting)
  const runBasis = useWorkbenchTestQuotesStore((s) => s.runBasis)
  const setOpen = useWorkbenchTestQuotesStore((s) => s.setOpen)
  const load = useWorkbenchTestQuotesStore((s) => s.load)
  const save = useWorkbenchTestQuotesStore((s) => s.save)
  const openQuote = useWorkbenchTestQuotesStore((s) => s.openQuote)
  const toggle = useWorkbenchTestQuotesStore((s) => s.toggle)
  const run = useWorkbenchTestQuotesStore((s) => s.run)
  const accept = useWorkbenchTestQuotesStore((s) => s.accept)
  const remove = useWorkbenchTestQuotesStore((s) => s.remove)
  const panelWidth = useWorkbenchViewStore((s) => s.panelWidth)
  const setPanelWidth = useWorkbenchViewStore((s) => s.setPanelWidth)
  const form = useWorkbenchFormStore((s) => s.form)
  // Read so the panel redraws when what a run priced on changes.
  usePricingContext()
  const [name, setName] = useState("")
  const [expanded, setExpanded] = useState<string | null>(null)

  useEffect(() => {
    void load()
  }, [load])

  const outdated = runIsStale(runBasis, form)
  // Said once the run is over; an accept waits for both.
  const stale = !running && outdated
  const ran = Object.values(runs)
  const counts = {
    changed: ran.filter((r) => r.status === "changed").length,
    failed: ran.filter((r) => r.status === "failed").length,
    unchanged: ran.filter((r) => r.status === "unchanged").length,
  }
  const done = ran.length - ran.filter((r) => r.status === "running").length

  const onSave = async (event: FormEvent) => {
    event.preventDefault()
    if (await save(name.trim())) setName("")
  }

  return (
    <SidePanel
      width={panelWidth}
      onWidthChange={setPanelWidth}
      minWidth={MIN_WIDTH}
      maxWidth={maxWidth}
      testId="workbench-test-quotes"
      title="Test quotes"
      icon={<FlaskConical size={14} style={{ color: "var(--accent)" }} />}
      onClose={() => setOpen(false)}
    >
      <div className="flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto p-2.5 text-xs">
        <form onSubmit={(event) => { void onSave(event) }} className="flex items-center gap-1.5" aria-label="Save the quote on the sheets">
          <input
            aria-label="Test quote name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="Name the quote on the sheets"
            maxLength={200}
            className="focus-ring min-w-0 flex-1 rounded-md px-1.5 py-1 text-xs"
            style={{ background: "var(--bg-input)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
          />
          <button type="submit" className={BUTTON} disabled={saving || name.trim() === ""} title="Price the quote on the sheets in full and save it as a test quote">
            <Save size={12} /> {saving ? "Saving…" : "Save quote"}
          </button>
        </form>

        <div className="flex flex-wrap items-center gap-1.5">
          <button
            type="button"
            className={BUTTON}
            disabled={running || accepting || quotes.length === 0}
            onClick={() => { void run() }}
            title="Price the test quotes on the pipeline as it is now, one request each, and compare each with its accepted response"
          >
            <Play size={12} /> {selected.length > 0 ? `Run ${selected.length} ticked` : "Run all"}
          </button>
          <span role="status" style={{ color: "var(--text-secondary)" }}>
            {running
              ? `Running ${Math.min(done + 1, ran.length)} of ${ran.length}`
              : ran.length > 0
                ? `${counts.changed} changed, ${counts.failed} failed, ${counts.unchanged} unchanged`
                : loading
                  ? "Loading…"
                  : null}
          </span>
        </div>
        {stale && (
          <p role="alert" className="rounded-md px-2 py-1" style={{ color: "var(--warning)", border: "1px solid var(--warning)" }}>
            The pipeline, the data source or the form has changed since this run: run again before accepting.
          </p>
        )}
        {error !== null && (
          <p role="alert" style={{ color: "var(--danger)" }}>
            {error}
          </p>
        )}
        {notice !== null && <p style={{ color: "var(--text-secondary)" }}>{notice}</p>}

        {quotes.length === 0 && !loading ? (
          <p style={{ color: "var(--text-muted)" }}>
            No test quotes yet. Key a quote into the sheets, name it and save it here.
          </p>
        ) : (
          <ul aria-label="Test quotes" className="flex flex-col">
            {quotes.map((quote) => {
              const result = runs[quote.id]
              const open = expanded === quote.id
              const detail = result !== undefined && (result.changes.length > 0 || result.error !== null || result.dropped.length > 0)
              return (
                <li key={quote.id} className="flex flex-col border-b py-1" style={{ borderColor: "var(--border)" }}>
                  <div className="flex items-center gap-1.5">
                    <input
                      type="checkbox"
                      aria-label={`Tick ${quote.name} for a run`}
                      checked={selected.includes(quote.id)}
                      onChange={() => toggle(quote.id)}
                    />
                    <button
                      type="button"
                      className="flex min-w-0 flex-1 items-center gap-1 text-left"
                      onClick={() => setExpanded(open ? null : quote.id)}
                      aria-expanded={detail ? open : undefined}
                      disabled={!detail}
                      title={`Revision ${quote.number}, saved by ${quote.saved_by || "nobody named"} at ${quote.saved_at}`}
                    >
                      {detail ? open ? <ChevronDown size={12} /> : <ChevronRight size={12} /> : <span className="w-3" />}
                      <span className="truncate font-medium" style={{ color: "var(--text-primary)" }}>{quote.name}</span>
                    </button>
                    {result !== undefined && (
                      <span className="shrink-0" style={{ color: STATUS_COLOR[result.status] }} data-testid={`test-quote-status-${quote.id}`}>
                        {STATUS_TEXT[result.status]}
                      </span>
                    )}
                    <button type="button" className={ICON_BUTTON} onClick={() => { void openQuote(quote.id) }} title={`Open ${quote.name} on the sheets`} aria-label={`Open ${quote.name}`}>
                      <FolderOpen size={12} />
                    </button>
                    {result?.status === "changed" && (
                      <button
                        type="button"
                        className={ICON_BUTTON}
                        disabled={running || accepting || outdated}
                        onClick={() => { void accept(quote.id) }}
                        title={`Accept the new response as ${quote.name}'s`}
                        aria-label={`Accept ${quote.name}`}
                      >
                        <Check size={12} />
                      </button>
                    )}
                    <button
                      type="button"
                      className={ICON_BUTTON}
                      onClick={() => { if (window.confirm(`Delete the test quote '${quote.name}'?`)) void remove(quote.id) }}
                      title={`Delete ${quote.name}`}
                      aria-label={`Delete ${quote.name}`}
                    >
                      <Trash2 size={12} />
                    </button>
                  </div>
                  {open && result !== undefined && (
                    <div className="ml-6 mt-1 flex flex-col gap-1">
                      {result.error !== null && <p style={{ color: "var(--danger)" }}>{result.error}</p>}
                      {result.dropped.length > 0 && (
                        <p style={{ color: "var(--text-secondary)" }}>No place on the sheets for: {result.dropped.join(", ")}</p>
                      )}
                      {result.changes.length > 0 && (
                        <ul aria-label={`What changed in ${quote.name}`} className="flex flex-col gap-0.5">
                          {result.changes.map((change) => <ChangeLine key={change.path} change={change} />)}
                        </ul>
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
