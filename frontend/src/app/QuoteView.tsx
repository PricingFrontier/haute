/**
 * A quote in the underwriter app (specs/workbench): the sheets under the quote's bar (back
 * to Quotes, its reference, name and stage, Save and Mark final or Reopen, a menu of what
 * else it takes, History, the zoom, and why it has no price), a line of its revision, who
 * saved it, the release that priced it, where it came from and who it is with, and the
 * notices of anything that stands in the way. The quote is priced as it is typed. Values
 * not saved are kept in the browser and leaving with them asks first.
 */
import {
  Archive,
  ArchiveRestore,
  ArrowLeft,
  BadgeCheck,
  Copy,
  GitBranchPlus,
  History,
  Link2,
  LockOpen,
  MoreHorizontal,
  RefreshCcw,
} from "lucide-react"
import { useEffect, useRef, useState } from "react"
import { changeAppQuote } from "../api/app"
import { apiErrorMessage } from "../api/errors"
import type { WorkbenchAppQuote } from "../api/types"
import { CommittedTextField } from "../components/form"
import { ToolbarButton, ToolbarColumn, ZoomInOut } from "../haute-ui"
import { hasModifier, inModalDialog, saveCommittingField } from "../hooks/keyboardTargets"
import useToastStore from "../stores/useToastStore"
import useWorkbenchPreviewStore from "../stores/useWorkbenchPreviewStore"
import useWorkbenchViewStore, { ZOOM_STEP } from "../stores/useWorkbenchViewStore"
import { canonicalJson } from "../utils/canonicalJson"
import { quoteTyped } from "../utils/sheetValues"
import { quotedSchema } from "../utils/sheetVisibility"
import { QUOTE_DETAILS_ID } from "../utils/workbenchForm"
import PageTabs from "../workbench/PageTabs"
import SheetCanvas from "../workbench/SheetCanvas"
import AppHeader from "./AppHeader"
import HistoryPanel from "./HistoryPanel"
import { basisOf, fullTime, relativeTime, valuesKey } from "./quoteValues"
import { followLink, navigate, newQuotePath, quotePath, revisionPath, routeOf, setLeaveGuard, type AppRoute } from "./route"
import { BUTTON, Menu, Notice, StageChip, type MenuItem } from "./ui"
import useAppStore, { confirm } from "./useAppStore"
import useOpenQuoteStore, { busy, canEdit, readOnlyReason } from "./useOpenQuoteStore"

type QuoteRoute = Extract<AppRoute, { page: "new" } | { page: "quote" }>

const RELATION_LABEL = { duplicate: "Duplicate", renewal: "Renewal", alternative: "Alternative" } as const

/** The quote_details column that names a quote, typed in the bar. */
const NAME_COLUMN = "qd_quote_name"

/** A quote just saved as new, on the sheets already: its address is moved to without opening it again. */
let adopted: string | null = null

/**
 * Save the open quote, and, for a new one, move to its own address once it has one: a save
 * that first repeats a create whose answer was lost makes the quote though the values typed
 * after may still be unsaved.
 */
async function saveOpen(): Promise<void> {
  const before = useOpenQuoteStore.getState().quote
  const saved = await useOpenQuoteStore.getState().save()
  const made = useOpenQuoteStore.getState().quote
  if (before === null && made !== null && routeOf(window.location).page === "new") {
    adopted = made.id
    await navigate(quotePath(made.id), { replace: true, force: true })
  }
  if (saved !== null) useToastStore.getState().addToast("success", `Saved ${saved.reference}, revision ${saved.latest}`)
}

/** Restore `archived`, as its archive left it: through the open quote while it is open, else on its own. */
async function undoArchive(archived: WorkbenchAppQuote): Promise<void> {
  const open = useOpenQuoteStore.getState()
  if (open.quote?.id === archived.id) {
    await open.change("restore", {}, archived)
    return
  }
  try {
    await changeAppQuote(archived.id, "restore", archived.version, {})
  } catch (reason: unknown) {
    useToastStore.getState().addToast("error", `Could not restore ${archived.reference}: ${apiErrorMessage(reason)}`)
  }
}

/** Save the values on the sheets as a new quote and open it. */
async function saveOpenAsNew(): Promise<void> {
  const quote = await useOpenQuoteStore.getState().saveAsNew()
  if (quote === null) return
  adopted = quote.id
  await navigate(quotePath(quote.id), { force: true })
  useToastStore.getState().addToast("success", `Saved as ${quote.reference}`)
}

export default function QuoteView({ route }: { route: QuoteRoute }) {
  const status = useOpenQuoteStore((s) => s.status)
  const loadError = useOpenQuoteStore((s) => s.loadError)
  const quote = useOpenQuoteStore((s) => s.quote)
  const past = useOpenQuoteStore((s) => s.past)
  const olderSheets = useOpenQuoteStore((s) => s.olderSheets)
  const baseline = useOpenQuoteStore((s) => s.baseline)
  const historyOpen = useOpenQuoteStore((s) => s.historyOpen)
  const values = useWorkbenchPreviewStore((s) => s.quote)
  const reason = readOnlyReason({ quote, past, olderSheets })
  // Only a quote open, and as it may change, has values of its own not yet saved.
  const dirty = status === "ready" && reason === null && valuesKey(values) !== baseline
  const [unkept, setUnkept] = useState(false)

  const routeKey = route.page === "new" ? `new:${route.from?.quoteId ?? ""}:${route.from?.revision ?? ""}:${route.from?.relation ?? ""}` : `quote:${route.id}:${route.revision ?? ""}`
  useEffect(() => {
    // A question asked about the quote the page has left is nobody's now.
    useAppStore.getState().answer(null)
    const open = useOpenQuoteStore.getState()
    if (route.page === "new") {
      void open.openNew(route.from)
      return
    }
    // Just saved as new: it is on the sheets already.
    const justSaved = adopted === route.id && route.revision === null && open.quote?.id === route.id
    adopted = null
    if (!justSaved) void open.openQuote(route.id, route.revision)
    // The route's key says all of it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [routeKey])

  // Leaving with values not saved asks first; closing the tab warns.
  useEffect(() => {
    if (!dirty) return
    setLeaveGuard(() =>
      confirm(
        "Leave without saving?",
        "Your changes to this quote are not saved. This browser keeps them, offered back when you open the quote again, until you save or discard them.",
        "Leave",
      ),
    )
    const warn = (event: BeforeUnloadEvent) => event.preventDefault()
    window.addEventListener("beforeunload", warn)
    return () => {
      setLeaveGuard(null)
      window.removeEventListener("beforeunload", warn)
    }
  }, [dirty])

  // Values not saved are kept in the browser as they are typed, and at once as the quote is
  // left or the page goes, so the last keys typed are kept too.
  const typing = useRef(false)
  useEffect(() => {
    if (!dirty) return
    typing.current = true
    const timer = window.setTimeout(() => {
      typing.current = false
      setUnkept(!useOpenQuoteStore.getState().keepDraft())
    }, 400)
    return () => window.clearTimeout(timer)
  }, [dirty, values])
  useEffect(() => {
    const flush = () => {
      if (!typing.current) return
      typing.current = false
      useOpenQuoteStore.getState().keepDraft()
    }
    window.addEventListener("pagehide", flush)
    return () => {
      window.removeEventListener("pagehide", flush)
      flush()
    }
  }, [routeKey])

  // Leaving the quote for the Quotes page ends it: an answer still to come opens nothing, and
  // a question asked about it is dropped.
  useEffect(() => () => {
    useAppStore.getState().answer(null)
    useOpenQuoteStore.getState().close()
  }, [])

  // Ctrl+S saves, a field being typed in committed first.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.defaultPrevented || inModalDialog(event.target) || !hasModifier(event)) return
      if (event.key.toLowerCase() === "s") {
        event.preventDefault()
        saveCommittingField(event.target, () => void saveOpen())
      }
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [])

  return (
    <>
      <QuoteBar dirty={dirty} />
      {status === "loading" && (
        <div role="status" className="flex flex-1 items-center justify-center text-[13px]" style={{ color: "var(--text-muted)" }}>
          Opening the quote…
        </div>
      )}
      {status === "error" && (
        <div role="alert" className="flex flex-1 flex-col items-center justify-center gap-2 p-8 text-[13px]" style={{ color: "var(--danger-text)" }}>
          The quote could not be opened: {loadError}
          <a href="/" onClick={(event) => followLink(event, "/")} className={BUTTON}>
            <ArrowLeft size={12} /> Back to Quotes
          </a>
        </div>
      )}
      {status === "ready" && (
        <div className="flex min-h-0 flex-1">
          <main className="relative flex min-h-0 min-w-0 flex-1 flex-col" aria-label="Sheets">
            <QuoteLine />
            <QuoteNotices unkept={unkept} />
            <div className="px-4 pt-2">
              <PageTabs readOnly />
            </div>
            <SheetCanvas />
          </main>
          {historyOpen && quote !== null && <HistoryPanel />}
        </div>
      )}
    </>
  )
}

/** The quote's bar: back, its identity, its actions and the zoom. */
function QuoteBar({ dirty }: { dirty: boolean }) {
  const form = useAppStore((s) => s.form)
  const release = useAppStore((s) => s.release)
  const status = useOpenQuoteStore((s) => s.status)
  const quote = useOpenQuoteStore((s) => s.quote)
  const revision = useOpenQuoteStore((s) => s.revision)
  const past = useOpenQuoteStore((s) => s.past)
  const olderSheets = useOpenQuoteStore((s) => s.olderSheets)
  const source = useOpenQuoteStore((s) => s.source)
  const saving = useOpenQuoteStore((s) => s.saving)
  const working = useOpenQuoteStore(busy)
  const historyOpen = useOpenQuoteStore((s) => s.historyOpen)
  const change = useOpenQuoteStore((s) => s.change)
  const setHistoryOpen = useOpenQuoteStore((s) => s.setHistoryOpen)
  const values = useWorkbenchPreviewStore((s) => s.quote)
  const appSet = useWorkbenchPreviewStore((s) => s.locked)
  const setCell = useWorkbenchPreviewStore((s) => s.setCell)
  const price = useWorkbenchPreviewStore((s) => s.price)
  const error = useWorkbenchPreviewStore((s) => s.error)
  const zoomBy = useWorkbenchViewStore((s) => s.zoomBy)
  const addToast = useToastStore((s) => s.addToast)
  const reason = readOnlyReason({ quote, past, olderSheets })
  const editor = release?.can_edit ?? true
  const ready = status === "ready" && form !== null
  // The details the app sets are not typing, nor are the defaults a new quote starts with.
  const typed = form !== null && quoteTyped(quotedSchema(form.schema, appSet), values)
  const priced = form !== null && price !== null && price.basis === basisOf(form, values)
  // Its last pricing failed: a save keeps it unpriced.
  const unpriceable = !priced && error !== null
  const closed = reason !== null
  // The quote's name is its quote_details.quote_name cell, on the sheets or not.
  const name = values[QUOTE_DETAILS_ID]?.[0]?.[NAME_COLUMN]
  // A saved quote whose values are as saved, priced as saved on this release, or unpriced as
  // saved, has nothing new to save; a price made since, or one of another release, has.
  const savedTables = revision?.tables ?? null
  const samePrice = priced
    ? savedTables !== null && revision?.release === release?.id && canonicalJson(price.tables) === canonicalJson(savedTables)
    : savedTables === null
  const nothingNew = quote !== null && !dirty && samePrice

  const saveTitle = closed
    ? "This quote does not take changes as it is"
    : !typed
      ? "Type the quote first"
      : unpriceable
        ? "Save the quote unpriced: it could not be priced (Ctrl+S)"
        : "Save the quote with its price (Ctrl+S)"

  const finalBlock = !editor
    ? "Changing quotes needs the app's edit role"
    : quote === null
      ? "Save the quote first"
      : dirty
        ? "Save your changes first"
        : !quote.release || quote.stage === "draft"
          ? "Save the quote with its price first"
          : quote.release !== release?.id
            ? "Priced on an earlier release: save it again first"
            : closed
              ? "This quote does not take changes as it is"
              : null

  const markFinal = async () => {
    if (quote === null || revision === null) return
    const yes = await confirm(
      `Mark ${quote.reference} final?`,
      `Revision ${revision.number} is pinned as the final quotation. It cannot be saved over until someone reopens it, giving a reason; the final stays in its history.`,
      "Mark final",
    )
    if (yes && (await change("final", { revision: revision.number }, quote)) !== null) addToast("success", `${quote.reference} is final`)
  }

  const reopen = async () => {
    if (quote === null) return
    const answer = await useAppStore.getState().ask({
      title: `Reopen ${quote.reference}?`,
      text: "Its final stays in its history; the next save is an amendment under the same reference.",
      confirm: "Reopen",
      reason: { label: "Why it is being reopened" },
    })
    if (answer !== null && (await change("reopen", { reason: answer.reason }, quote)) !== null) addToast("success", `${quote.reference} is reopened`)
  }

  const archive = async () => {
    if (quote === null) return
    const archived = await change("archive", {}, quote)
    if (archived === null) return
    addToast("success", `Archived ${quote.reference}`, { label: "Undo", run: () => void undoArchive(archived) })
  }

  const derive = (relation: "duplicate" | "renewal" | "alternative") => {
    if (quote === null || revision === null) return
    void navigate(newQuotePath({ quoteId: quote.id, revision: revision.number, relation }))
  }

  const copyLink = async () => {
    if (quote === null) return
    try {
      await navigator.clipboard.writeText(`${window.location.origin}${quotePath(quote.id)}`)
      addToast("success", `Link to ${quote.reference} copied`)
    } catch {
      addToast("error", "This browser would not copy the link")
    }
  }

  const items: MenuItem[] = [
    { label: "Duplicate", icon: Copy, run: () => derive("duplicate"), disabled: quote === null || !editor, title: "A new quote with this revision's values" },
    { label: "Renew", icon: RefreshCcw, run: () => derive("renewal"), disabled: quote === null || !editor, title: "A new quote renewing this one, from this revision's values" },
    { label: "Create alternative", icon: GitBranchPlus, run: () => derive("alternative"), disabled: quote === null || !editor, title: "A new quote offering other terms, from this revision's values" },
    quote?.archived
      ? { label: "Restore", icon: ArchiveRestore, run: () => void change("restore", {}, quote), disabled: working || !editor }
      : { label: "Archive", icon: Archive, run: () => void archive(), disabled: quote === null || working || !editor },
    { label: "Copy link", icon: Link2, run: () => void copyLink(), disabled: quote === null },
  ]

  return (
    <AppHeader label="Quote toolbar">
      <a
        href="/"
        onClick={(event) => followLink(event, "/")}
        className="toolbar-btn toolbar-action w-auto"
        style={{ width: "auto" }}
        title="Back to Quotes"
        data-testid="back-to-quotes"
      >
        <ArrowLeft size={13} aria-hidden="true" /> Quotes
      </a>
      <div className="flex min-w-[180px] max-w-[320px] flex-col gap-0.5" data-testid="quote-identity">
        <div className="flex items-center gap-1.5">
          <span className="font-mono text-[12px] font-semibold" style={{ color: "var(--text-accent)" }} data-testid="quote-reference">
            {quote?.reference ?? "New quote"}
          </span>
          {quote !== null && <StageChip stage={quote.stage} />}
          {quote?.archived && <StageChip stage="archived" />}
          {dirty && (
            <span className="text-[10px]" style={{ color: "var(--warning)" }} data-testid="quote-unsaved">
              Unsaved
            </span>
          )}
        </div>
        <CommittedTextField
          aria-label="Quote name"
          value={typeof name === "string" ? name : ""}
          placeholder={quote === null ? (source === null ? "Name this quote" : `${RELATION_LABEL[source.relation]} of ${source.reference}`) : quote.title}
          disabled={!ready || closed}
          title="The quote's name, saved with it"
          onCommit={(text) => setCell(QUOTE_DETAILS_ID, 0, NAME_COLUMN, text.trim())}
          maxLength={200}
          className="focus-ring w-full rounded px-1 py-0.5 text-[12px]"
          style={{ background: "transparent", border: "1px solid var(--border-subtle)", color: "var(--text-primary)" }}
        />
      </div>
      <ToolbarColumn role="group" aria-label="Save">
        <button type="button" className="toolbar-fill toolbar-fill-save" onClick={() => void saveOpen()} disabled={!ready || working || closed || !typed || nothingNew} title={nothingNew ? "Nothing has changed since the quote was saved" : saveTitle} data-testid="save-quote">
          {saving ? "Saving…" : unpriceable && !closed ? "Save unpriced" : "Save"}
        </button>
        {quote?.stage === "final" ? (
          <ToolbarButton icon={LockOpen} onClick={() => void reopen()} disabled={working || !editor || past || olderSheets !== null || quote.archived} title="Reopen the quote for an amendment, giving a reason">
            Reopen
          </ToolbarButton>
        ) : (
          <ToolbarButton icon={BadgeCheck} onClick={() => void markFinal()} disabled={working || finalBlock !== null} title={finalBlock ?? "Pin this revision as the final quotation"}>
            Mark final
          </ToolbarButton>
        )}
      </ToolbarColumn>
      <ToolbarColumn role="group" aria-label="More">
        <Menu label="More" icon={MoreHorizontal} items={items} testId="quote-more" />
        <ToolbarButton icon={History} onClick={() => setHistoryOpen(!historyOpen)} aria-pressed={historyOpen} disabled={quote === null} title="The quote's revisions, note and activity">
          History
        </ToolbarButton>
      </ToolbarColumn>
      <ZoomInOut onZoomIn={() => zoomBy(ZOOM_STEP)} onZoomOut={() => zoomBy(-ZOOM_STEP)} />
      {error !== null && (
        <span data-testid="app-pricing-error" className="max-w-96 truncate text-xs" style={{ color: "var(--warning)" }} title={error}>
          {error}
        </span>
      )}
    </AppHeader>
  )
}

/** The line under the bar: the revision, who saved it, the release, where it came from, who it is with. */
function QuoteLine() {
  const release = useAppStore((s) => s.release)
  const people = useAppStore((s) => s.people)
  const loadPeople = useAppStore((s) => s.loadPeople)
  const quote = useOpenQuoteStore((s) => s.quote)
  const revision = useOpenQuoteStore((s) => s.revision)
  const source = useOpenQuoteStore((s) => s.source)
  const working = useOpenQuoteStore(busy)
  const change = useOpenQuoteStore((s) => s.change)

  useEffect(() => {
    void loadPeople()
  }, [loadPeople])

  const muted = { color: "var(--text-secondary)" }
  const assign = async (to: string) => {
    if (quote === null) return
    let assignee = to
    if (to === "") {
      const answer = await useAppStore.getState().ask({
        title: `Assign ${quote.reference}`,
        text: "Name who the quote is with now, as the app shows who is signed in.",
        confirm: "Assign",
        reason: { label: "Their sign-in name" },
      })
      if (answer === null) return
      assignee = answer.reason
    }
    if (assignee !== quote.assignee) void change("assignee", { assignee }, quote)
  }
  const choices = [...new Set([...(quote === null ? [] : [quote.assignee]), ...(release === null ? [] : [release.principal]), ...people])].sort()

  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-1.5 text-[11px]" style={{ borderBottom: "1px solid var(--border)" }} data-testid="quote-line">
      {quote === null || revision === null ? (
        <span style={muted}>
          Not saved yet
          {source !== null && (
            <>
              {" · "}
              {RELATION_LABEL[source.relation]} of{" "}
              <a href={revisionPath(source.quoteId, source.revision)} onClick={(event) => followLink(event, revisionPath(source.quoteId, source.revision))} style={{ color: "var(--text-accent)" }}>
                {source.reference} revision {source.revision}
              </a>
            </>
          )}
        </span>
      ) : (
        <>
          <span style={muted}>
            Revision {revision.number}
            {revision.number !== quote.latest && ` of ${quote.latest}`}
          </span>
          <span style={muted} title={fullTime(revision.saved_at)}>
            Saved {relativeTime(revision.saved_at)} by {revision.saved_by}
          </span>
          <span style={muted}>
            {revision.tables === null || revision.tables === undefined
              ? "Unpriced"
              : revision.release === release?.id
                ? `Priced on this release`
                : `Priced on release ${revision.release ?? "?"}`}
          </span>
          {quote.relation !== null && quote.source_quote_id !== null && quote.source_revision !== null && (
            <span style={muted}>
              {RELATION_LABEL[quote.relation]} of{" "}
              <a
                href={revisionPath(quote.source_quote_id, quote.source_revision)}
                onClick={(event) => followLink(event, revisionPath(quote.source_quote_id ?? "", quote.source_revision ?? 1))}
                style={{ color: "var(--text-accent)" }}
              >
                {quote.source_reference} revision {quote.source_revision}
              </a>
            </span>
          )}
          <label className="ml-auto flex items-center gap-1.5" style={muted}>
            With
            <select
              aria-label="Assigned to"
              value={quote.assignee}
              disabled={working || quote.archived || !canEdit()}
              onChange={(event) => void assign(event.target.value)}
              className="focus-ring rounded px-1 py-0.5 text-[11px]"
              style={{ border: "1px solid var(--border)", color: "var(--text-primary)" }}
            >
              {choices.map((person) => (
                <option key={person} value={person}>
                  {person === release?.principal ? `${person} (you)` : person}
                </option>
              ))}
              <option value="">Someone else…</option>
            </select>
          </label>
        </>
      )}
    </div>
  )
}

/** What stands in the way, and what can be done about it, one notice each. */
function QuoteNotices({ unkept }: { unkept: boolean }) {
  const quote = useOpenQuoteStore((s) => s.quote)
  const revision = useOpenQuoteStore((s) => s.revision)
  const past = useOpenQuoteStore((s) => s.past)
  const olderSheets = useOpenQuoteStore((s) => s.olderSheets)
  const conflict = useOpenQuoteStore((s) => s.conflict)
  const releaseChanged = useOpenQuoteStore((s) => s.releaseChanged)
  const error = useOpenQuoteStore((s) => s.error)
  const moved = useOpenQuoteStore((s) => s.moved)
  const dropped = useOpenQuoteStore((s) => s.dropped)
  const recovered = useOpenQuoteStore((s) => s.recovered)
  const working = useOpenQuoteStore(busy)
  const dismiss = useOpenQuoteStore((s) => s.dismiss)
  const change = useOpenQuoteStore((s) => s.change)
  const restoreDraft = useOpenQuoteStore((s) => s.restoreDraft)
  const discardDraft = useOpenQuoteStore((s) => s.discardDraft)
  const moveToCurrentSheets = useOpenQuoteStore((s) => s.moveToCurrentSheets)
  const reason = readOnlyReason({ quote, past, olderSheets })

  const openNewest = async () => {
    if (quote === null) return
    if (await navigate(quotePath(quote.id), { force: true })) void useOpenQuoteStore.getState().openQuote(quote.id, null)
  }

  return (
    <>
      {releaseChanged && (
        <Notice tone="danger" testId="notice-release" actions={<button type="button" className={BUTTON} onClick={() => window.location.reload()}>Reload</button>}>
          This page was drawn for an earlier release of the app. Reload it to carry on: your changes stay in this browser.
        </Notice>
      )}
      {conflict !== null && (
        <Notice
          tone="warning"
          testId="notice-conflict"
          actions={
            <>
              <button type="button" className={BUTTON} onClick={() => void openNewest()}>
                Open the newest
              </button>
              <button type="button" className={BUTTON} onClick={() => void saveOpenAsNew()} disabled={working}>
                Save mine as a new quote
              </button>
            </>
          }
          onDismiss={() => dismiss("conflict")}
        >
          {conflict.message} Your changes are still on the sheets.
        </Notice>
      )}
      {error !== null && (
        <Notice tone="danger" testId="notice-error" onDismiss={() => dismiss("error")}>
          {error}
        </Notice>
      )}
      {unkept && (
        <Notice tone="warning" testId="notice-unkept">
          This browser could not keep your changes: save them before you leave.
        </Notice>
      )}
      {recovered !== null && (
        <Notice
          tone="info"
          testId="notice-recovered"
          actions={
            <>
              <button type="button" className={BUTTON} onClick={restoreDraft}>
                Restore them
              </button>
              <button type="button" className={BUTTON} onClick={discardDraft}>
                Discard
              </button>
            </>
          }
        >
          You have changes to this quote from {relativeTime(recovered.keptAt)} that were not saved to the server.
          {quote !== null && recovered.version !== null && recovered.version !== quote.version && " The quote has been saved since then."}
        </Notice>
      )}
      {reason === "past" && quote !== null && revision !== null && (
        <Notice tone="info" testId="notice-past" actions={<button type="button" className={BUTTON} onClick={() => void openNewest()}>Open the newest</button>}>
          Revision {revision.number} as it was saved, read only. The newest is revision {quote.latest}.
        </Notice>
      )}
      {reason === "older-sheets" && (
        <Notice tone="info" testId="notice-older-sheets" actions={canEdit() && <button type="button" className={BUTTON} onClick={() => void moveToCurrentSheets()}>Move to current sheets</button>}>
          Saved on earlier sheets: it shows as it was keyed in, read only. Move it to the current sheets to change it.
        </Notice>
      )}
      {reason === "archived" && quote !== null && (
        <Notice tone="info" testId="notice-archived" actions={canEdit() && <button type="button" className={BUTTON} onClick={() => void change("restore", {}, quote)} disabled={working}>Restore</button>}>
          Archived{quote.archived_by !== null && ` by ${quote.archived_by}`}{quote.archived_at !== null && ` ${relativeTime(quote.archived_at)}`}, read only. Restore it to change it.
        </Notice>
      )}
      {!canEdit() && (
        <Notice tone="info" testId="notice-reader">
          You can read quotes in this app; changing them needs the {useAppStore.getState().release?.edit_role ?? "edit"} role.
        </Notice>
      )}
      {reason === "final" && quote !== null && quote.final_at !== null && (
        <Notice tone="info" testId="notice-final">
          Final at revision {quote.final_revision}, marked by {quote.final_by} {relativeTime(quote.final_at)}. Reopen it, giving a reason, to change it.
        </Notice>
      )}
      {moved && (
        <Notice tone="warning" testId="notice-moved" onDismiss={() => dismiss("moved")}>
          The app priced the quote again as it saved it, and the price changed: the sheets show the price saved.
        </Notice>
      )}
      {dropped.length > 0 && (
        <Notice tone="warning" testId="notice-dropped" onDismiss={() => dismiss("dropped")}>
          No place on the current sheets for: {dropped.join(", ")}.
        </Notice>
      )}
    </>
  )
}
