/**
 * A quote in the underwriter app (specs/workbench): the sheets under the quote's bar (back
 * to Quotes, its reference and option, name and stage, Save and Mark final or Amend, a menu
 * of what else it takes, History, the zoom, and why it has no price), a line of who saved it,
 * the release that priced it, where it came from and who it is with, and the notices of
 * anything that stands in the way. The quote is priced as it is typed. Values not saved are
 * kept in the browser and leaving with them asks first.
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
import { Fragment, useEffect, useRef, useState } from "react"
import { changeAppQuote, type QuoteRelation, type QuoteSource } from "../api/app"
import { apiErrorMessage } from "../api/errors"
import type { WorkbenchAppQuote } from "../api/types"
import { CommittedTextField } from "../components/form"
import { ToolbarButton, ToolbarColumn, ZoomInOut } from "../haute-ui"
import { hasModifier, inModalDialog, saveCommittingField } from "../hooks/keyboardTargets"
import useQuestionStore, { confirmed } from "../stores/useQuestionStore"
import useToastStore from "../stores/useToastStore"
import useWorkbenchPreviewStore from "../stores/useWorkbenchPreviewStore"
import useWorkbenchViewStore, { ZOOM_STEP } from "../stores/useWorkbenchViewStore"
import { canonicalJson } from "../utils/canonicalJson"
import { relativeTime } from "../utils/relativeTime"
import { basisOf, quoteEligible } from "../utils/sheetVisibility"
import { QUOTE_DETAILS, QUOTE_DETAILS_ID } from "../utils/workbenchForm"
import PageTabs from "../workbench/PageTabs"
import SheetCanvas from "../workbench/SheetCanvas"
import AppHeader from "./AppHeader"
import HistoryPanel from "./HistoryPanel"
import { fullTime, sameQuote, valuesKey } from "./quoteValues"
import { followLink, navigate, newQuotePath, quotePath, setLeaveGuard, type AppRoute } from "./route"
import { BUTTON, Menu, Notice, ReleaseNotice, StageChip, type MenuItem } from "./ui"
import useAppStore from "./useAppStore"
import useNewQuoteStore from "./useNewQuoteStore"
import useOpenQuoteStore, { busy, canEdit, readOnlyReason } from "./useOpenQuoteStore"

type QuoteRoute = Extract<AppRoute, { page: "new" } | { page: "quote" }>

const RELATION_LABEL = { duplicate: "Duplicate", renewal: "Renewal", alternative: "Alternative" } as const

/** The quote_details column that names a quote, typed in the bar. */
const NAME_COLUMN = QUOTE_DETAILS.quote_name

/** Save the open quote. */
async function saveOpen(): Promise<void> {
  const saved = await useOpenQuoteStore.getState().save()
  if (saved !== null) useToastStore.getState().addToast("success", `Saved ${saved.label}`)
}

/**
 * Make the alternative the page asks for, as the underwriter asked for it, and move to its
 * own address once it has one, unless the page has moved on meanwhile.
 */
async function makeAsked(from: QuoteSource): Promise<void> {
  const asked = window.location.href
  const made = await useOpenQuoteStore.getState().openNew(from)
  if (made === null || window.location.href !== asked) return
  await navigate(quotePath(made.reference, made.option), { replace: true, force: true })
}

/** Restore `archived`, as its archive left it: through the open quote while it is open, else on its own. */
async function undoArchive(archived: WorkbenchAppQuote): Promise<void> {
  const open = useOpenQuoteStore.getState()
  if (open.quote !== null && sameQuote(open.quote, archived)) {
    await open.change("restore", {}, archived)
    return
  }
  try {
    await changeAppQuote(archived.reference, archived.option, "restore", archived.version, {})
  } catch (reason: unknown) {
    useToastStore.getState().addToast("error", `Could not restore ${archived.label}: ${apiErrorMessage(reason)}`)
  }
}

export default function QuoteView({ route }: { route: QuoteRoute }) {
  const status = useOpenQuoteStore((s) => s.status)
  const loadError = useOpenQuoteStore((s) => s.loadError)
  const quote = useOpenQuoteStore((s) => s.quote)
  const baseline = useOpenQuoteStore((s) => s.baseline)
  const historyOpen = useOpenQuoteStore((s) => s.historyOpen)
  const values = useWorkbenchPreviewStore((s) => s.quote)
  const reason = readOnlyReason({ quote })
  // Only a quote open, and as it may change, has values of its own not yet saved.
  const dirty = status === "ready" && reason === null && valuesKey(values) !== baseline
  const [unkept, setUnkept] = useState(false)

  const routeKey = JSON.stringify(route)
  useEffect(() => {
    // A question asked about the quote the page has left is nobody's now.
    useQuestionStore.getState().answer(null)
    if (route.page === "new") {
      // A quote under a new reference is made in the New quote window, over the Quotes page.
      if (route.from?.relation === "alternative") void makeAsked(route.from)
      return
    }
    void useOpenQuoteStore.getState().openRouted(route.reference, route.option)
    // The route's key says all of it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [routeKey])

  // Leaving with values not saved asks first; closing the tab warns.
  useEffect(() => {
    if (!dirty) return
    setLeaveGuard(() =>
      confirmed({
        title: "Leave without saving?",
        text: "Your changes to this quote are not saved. This browser keeps them, offered back when you open the quote again, until you save or discard them.",
        confirm: "Leave",
      }),
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
  // Typed back to the saved values, the draft is reconciled too: kept no more.
  const wasDirty = useRef(false)
  useEffect(() => {
    if (!dirty && !wasDirty.current) return
    wasDirty.current = dirty
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
    useQuestionStore.getState().answer(null)
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
              <PageTabs />
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
  const savedTables = useOpenQuoteStore((s) => s.savedTables)
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
  const reason = readOnlyReason({ quote })
  const editor = release?.can_edit ?? true
  const ready = status === "ready" && form !== null
  // As the server counts typing: what the sheets show, the details the app sets and the defaults aside.
  const typed = form !== null && quoteEligible(form, values, appSet)
  const priced = form !== null && price !== null && price.basis === basisOf(form, values)
  // Its last pricing failed: a save keeps it unpriced.
  const unpriceable = !priced && error !== null
  const closed = reason !== null
  // The quote's name is its quote_details.quote_name cell, on the sheets or not.
  const name = values[QUOTE_DETAILS_ID]?.[0]?.[NAME_COLUMN]
  // A saved quote whose values are as saved, priced as saved on this release, or unpriced as
  // saved, has nothing new to save; a price made since, or one of another release, has.
  const samePrice = priced
    ? savedTables !== null && quote?.priced_release === release?.id && canonicalJson(price.tables) === canonicalJson(savedTables)
    : savedTables === null
  const nothingNew = quote !== null && !dirty && samePrice

  const saveTitle = closed
    ? "This quote does not take changes as it is"
    : !typed
      ? "Type the quote first"
      : unpriceable
        ? "Save the quote unpriced: it could not be priced (Ctrl+S)"
        : "Save the quote with its price (Ctrl+S)"

  // Whether each required field holds a value is the server's to say as the quote is marked final.
  const finalBlock = !editor
    ? "Changing quotes needs the app's edit role"
    : quote === null
      ? "The quote is not open yet"
      : closed
        ? "This quote does not take changes as it is"
        : dirty
          ? "Save your changes first"
          : quote.priced_release === null
            ? "Save the quote with its price first"
            : quote.priced_release !== release?.id
              ? "Priced on an earlier release: save it again first"
              : null

  const markFinal = async () => {
    if (quote === null) return
    const yes = await confirmed({
      title: `Mark ${quote.label} final?`,
      text: "It becomes the final quotation, and takes no save until someone amends it, giving a reason.",
      confirm: "Mark final",
    })
    if (yes && (await change("final", {}, quote)) !== null) addToast("success", `${quote.label} is final`)
  }

  const amend = async () => {
    if (quote === null) return
    const answer = await useQuestionStore.getState().ask({
      title: `Amend ${quote.label}?`,
      text: "It goes back to draft, and its next save replaces the final quotation.",
      confirm: "Amend",
      reason: { label: "Why it is being amended" },
    })
    if (answer !== null && (await change("amend", { reason: answer.reason }, quote)) !== null) addToast("success", `${quote.label} is a draft again`)
  }

  const archive = async () => {
    if (quote === null) return
    const archived = await change("archive", {}, quote)
    if (archived === null) return
    addToast("success", `Archived ${quote.label}`, { label: "Undo", run: () => void undoArchive(archived) })
  }

  const derive = (relation: QuoteRelation) => {
    if (quote === null) return
    const from = { reference: quote.reference, option: quote.option }
    // An alternative is made at once under this reference; a duplicate or a renewal takes one
    // keyed in the New quote window, over this quote, which it leaves as it is.
    if (relation === "alternative") void navigate(newQuotePath({ ...from, relation }))
    else void useNewQuoteStore.getState().openDerived({ ...from, relation }, { label: quote.label })
  }

  const copyLink = async () => {
    if (quote === null) return
    try {
      await navigator.clipboard.writeText(`${window.location.origin}${quotePath(quote.reference, quote.option)}`)
      addToast("success", `Link to ${quote.label} copied`)
    } catch {
      addToast("error", "This browser would not copy the link")
    }
  }

  const items: MenuItem[] = [
    { label: "Duplicate", icon: Copy, run: () => derive("duplicate"), disabled: quote === null || !editor, title: "A new quote with this quote's values" },
    { label: "Renew", icon: RefreshCcw, run: () => derive("renewal"), disabled: quote === null || !editor, title: "A new quote renewing this one, from its values" },
    { label: "Create alternative", icon: GitBranchPlus, run: () => derive("alternative"), disabled: quote === null || !editor, title: "A new option under this reference, from this quote's values" },
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
            {quote?.label ?? "New quote"}
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
          placeholder={quote === null ? "Name this quote" : quote.title}
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
          <ToolbarButton icon={LockOpen} onClick={() => void amend()} disabled={working || !editor || quote.archived} title="Take the quote back to draft to amend it, giving a reason">
            Amend
          </ToolbarButton>
        ) : (
          <ToolbarButton icon={BadgeCheck} onClick={() => void markFinal()} disabled={working || finalBlock !== null} title={finalBlock ?? "Pin the quote as the final quotation"}>
            Mark final
          </ToolbarButton>
        )}
      </ToolbarColumn>
      <ToolbarColumn role="group" aria-label="More">
        <Menu label="More" icon={MoreHorizontal} items={items} testId="quote-more" />
        <ToolbarButton icon={History} onClick={() => setHistoryOpen(!historyOpen)} aria-pressed={historyOpen} disabled={quote === null} title="The quote's note, activity and related quotes">
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

/** The line under the bar: who saved the quote, the release that priced it, where it came from, who it is with. */
function QuoteLine() {
  const release = useAppStore((s) => s.release)
  const people = useAppStore((s) => s.people)
  const loadPeople = useAppStore((s) => s.loadPeople)
  const quote = useOpenQuoteStore((s) => s.quote)
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
      const answer = await useQuestionStore.getState().ask({
        title: `Assign ${quote.label}`,
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
  const sourcePath = quote === null || quote.source_reference === null || quote.source_option === null ? null : quotePath(quote.source_reference, quote.source_option)

  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-1.5 text-[11px]" style={{ borderBottom: "1px solid var(--border)" }} data-testid="quote-line">
      {quote === null ? null : (
        <>
          <span style={muted} title={fullTime(quote.saved_at)}>
            Saved {relativeTime(new Date(quote.saved_at))} by {quote.saved_by}
          </span>
          <span style={muted}>
            {quote.priced_release === null
              ? "Unpriced"
              : quote.priced_release === release?.id
                ? `Priced on this release`
                : `Priced on release ${quote.priced_release}`}
          </span>
          {quote.relation !== null && sourcePath !== null && (
            <span style={muted}>
              {RELATION_LABEL[quote.relation]} of{" "}
              <a href={sourcePath} onClick={(event) => followLink(event, sourcePath)} style={{ color: "var(--text-accent)" }}>
                {quote.source_label}
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

/** The places a refusal names, each set apart, one after another. */
function Places({ places }: { places: readonly string[] }) {
  return (
    <>
      {places.map((place, index) => (
        <Fragment key={`${index}:${place}`}>
          {index > 0 && "; "}
          <span className="font-medium">{place}</span>
        </Fragment>
      ))}
      .
    </>
  )
}

/** What stands in the way, and what can be done about it, one notice each. */
function QuoteNotices({ unkept }: { unkept: boolean }) {
  const quote = useOpenQuoteStore((s) => s.quote)
  const conflict = useOpenQuoteStore((s) => s.conflict)
  const refusal = useOpenQuoteStore((s) => s.refusal)
  const releaseChanged = useAppStore((s) => s.releaseChanged)
  const error = useOpenQuoteStore((s) => s.error)
  const moved = useOpenQuoteStore((s) => s.moved)
  const dropped = useOpenQuoteStore((s) => s.dropped)
  const recovered = useOpenQuoteStore((s) => s.recovered)
  const working = useOpenQuoteStore(busy)
  const dismiss = useOpenQuoteStore((s) => s.dismiss)
  const change = useOpenQuoteStore((s) => s.change)
  const restoreDraft = useOpenQuoteStore((s) => s.restoreDraft)
  const discardDraft = useOpenQuoteStore((s) => s.discardDraft)
  const reason = readOnlyReason({ quote })

  const openNewest = async () => {
    if (quote === null) return
    const { reference, option } = quote
    // Moved to the quote's address, its route opens it; there already, nothing else will.
    if ((await navigate(quotePath(reference, option), { force: true })) === "here") void useOpenQuoteStore.getState().openQuote(reference, option)
  }

  return (
    <>
      {releaseChanged && <ReleaseNotice kept />}
      {conflict !== null && (
        <Notice
          tone="warning"
          testId="notice-conflict"
          actions={
            <>
              <button type="button" className={BUTTON} onClick={() => void openNewest()}>
                Open the newest
              </button>
              <button type="button" className={BUTTON} onClick={() => useNewQuoteStore.getState().openSaveAsNew()} disabled={working}>
                Save mine as a new quote
              </button>
            </>
          }
          onDismiss={() => dismiss("conflict")}
        >
          {conflict.message} Your changes are still on the sheets.
        </Notice>
      )}
      {refusal !== null && (
        <Notice tone="danger" testId={refusal.of === "save" ? "notice-invalid" : "notice-not-ready"} onDismiss={() => dismiss("refusal")}>
          {refusal.of === "save" ? "Not saved, as these values are not of their columns' types: " : "Not marked final, as these have no value: "}
          <Places places={refusal.places} />
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
          You have changes to this quote from {relativeTime(new Date(recovered.keptAt))} that were not saved to the server.
          {quote !== null && recovered.version !== null && recovered.version !== quote.version && " The quote has changed since then."}
        </Notice>
      )}
      {reason === "archived" && quote !== null && (
        <Notice tone="info" testId="notice-archived" actions={canEdit() && <button type="button" className={BUTTON} onClick={() => void change("restore", {}, quote)} disabled={working}>Restore</button>}>
          Archived{quote.archived_by !== null && ` by ${quote.archived_by}`}{quote.archived_at !== null && ` ${relativeTime(new Date(quote.archived_at))}`}, read only. Restore it to change it.
        </Notice>
      )}
      {!canEdit() && (
        <Notice tone="info" testId="notice-reader">
          You can read quotes in this app; changing them needs the {useAppStore.getState().release?.edit_role ?? "edit"} role.
        </Notice>
      )}
      {reason === "final" && quote !== null && quote.final_at !== null && (
        <Notice tone="info" testId="notice-final">
          Marked final by {quote.final_by} {relativeTime(new Date(quote.final_at))}. Amend it, giving a reason, to change it.
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
