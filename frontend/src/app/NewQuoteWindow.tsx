/**
 * The New quote window (specs/workbench), over the page it was asked for from: the
 * reference, then the columns of quote_details the underwriter types, drawn as a Collection
 * draws them, with Create and Cancel; while its creation is unanswered it takes no typing,
 * and with no answer it offers Try again, sending what was sent, and Close, keeping it.
 */
import { useEffect, useMemo, useRef, useState } from "react"
import type { FormSpec } from "../api/types"
import ModalShell from "../components/ModalShell"
import { ModalFormHeader } from "../components/ModalForm"
import { cellKey } from "../utils/sheetValues"
import type { SheetVisibility } from "../utils/sheetVisibility"
import { COLLECTION_OPTIONS, QUOTE_DETAILS_ID, type SampleValue } from "../utils/workbenchForm"
import { FieldBoxes } from "../workbench/sheet/FieldBoxes"
import { NO_MARKS, fieldLabel } from "../workbench/sheet/fieldCells"
import { sheetColours } from "../workbench/sheet/sheetColours"
import { SheetValuesContext, type SheetValues } from "../workbench/useSheetValues"
import { navigate, quotePath, useAppRoute } from "./route"
import useAppStore from "./useAppStore"
import useNewQuoteStore, { REFERENCE_COLUMN, windowFields, windowProblems, type NewQuoteWindow as Shown } from "./useNewQuoteStore"

const BUTTON = "px-3 py-1.5 text-[12px] font-medium rounded-md transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
const QUIET_STYLE = { color: "var(--text-secondary)" }
const OTHER_STYLE = { color: "var(--text-primary)", border: "1px solid var(--border)" }
const YES_STYLE = { background: "var(--structure-action)", color: "var(--text-on-accent)" }

// The window's fields are a Collection of their own: these only fill the sheet's shape.
const NO_VISIBILITY: SheetVisibility = { pages: new Set(), sections: new Set(), widgets: new Set(), fields: new Set(), hidden: new Set() }
const NOTHING_LOCKED: ReadonlySet<string> = new Set()
/** The window's one row is set cell by cell: there are no rows to add or delete. */
const KEEP_ROWS = () => {}

export default function NewQuoteWindow() {
  const shown = useNewQuoteStore((s) => s.shown)
  const form = useAppStore((s) => s.form)
  // Back and Forward, or any move of the page, close a window that has sent nothing.
  const where = JSON.stringify(useAppRoute())
  useEffect(() => {
    const open = useNewQuoteStore.getState().shown
    if (open !== null && open.address !== `${window.location.pathname}${window.location.search}`) useNewQuoteStore.getState().leave()
  }, [where])
  if (shown === null || form === null) return null
  return <WindowBody shown={shown} form={form} />
}

function WindowBody({ shown, form }: { shown: Shown; form: FormSpec }) {
  const setReference = useNewQuoteStore((s) => s.setReference)
  const setDetail = useNewQuoteStore((s) => s.setDetail)
  const create = useNewQuoteStore((s) => s.create)
  const tryAgain = useNewQuoteStore((s) => s.tryAgain)
  const close = useNewQuoteStore((s) => s.close)
  const leave = useNewQuoteStore((s) => s.leave)
  const discard = useNewQuoteStore((s) => s.discard)
  const fields = useMemo(() => windowFields(form), [form])
  const problems = windowProblems(fields, shown.reference, shown.details)
  const fits = Object.keys(problems).length === 0
  const editing = shown.status === "editing"
  // A field being typed in holds what it has not committed yet: Create stays on while it is,
  // the press committing it first and then checking every field.
  const [typingIn, setTypingIn] = useState(false)
  const holder = useRef<HTMLDivElement>(null)

  // An empty required field is marked once Create was pressed, its star saying so before;
  // anything else that does not fit, at once.
  const marked = Object.fromEntries(Object.entries(problems).filter(([, problem]) => shown.checked || problem !== "Required"))
  const values: SheetValues = {
    rows: { [QUOTE_DETAILS_ID]: [{ ...shown.details, [REFERENCE_COLUMN]: shown.reference }] },
    setCell: (_table: string, _row: number, columnId: string, value: SampleValue) =>
      columnId === REFERENCE_COLUMN ? setReference(typeof value === "string" ? value : "") : setDetail(columnId, value),
    setRows: KEEP_ROWS,
    price: null,
    basis: "",
    problems: marked,
    visibility: NO_VISIBILITY,
    quoted: form.schema,
    readOnly: !editing,
    // The reference takes typing here, as it does nowhere else.
    locked: NOTHING_LOCKED,
  }
  // What stops Create while nothing is being typed: the first field that does not fit.
  const blocked = fields.flatMap(({ found }) => {
    const problem = found === null ? undefined : problems[cellKey(found.table.id, 0, found.column.id)]
    return found === null || problem === undefined ? [] : [`${fieldLabel(found)}: ${problem}`]
  })[0]

  // After the shell has taken the focus, the reference takes it, as the window takes typing.
  useEffect(() => {
    if (editing) holder.current?.querySelector<HTMLElement>("input, select")?.focus()
  }, [editing])

  const openTaken = () => {
    if (shown.taken === null) return
    const path = quotePath(shown.taken.quoteId)
    leave()
    void navigate(path)
  }

  return (
    <ModalShell ariaLabel={shown.title} onClose={close} width="w-[560px]" testId="new-quote-window">
      <form
        onSubmit={(event) => {
          event.preventDefault()
          // The field being typed in commits on blur: committed before every field is checked.
          const typed = document.activeElement
          if (typed instanceof HTMLElement && holder.current?.contains(typed)) typed.blur()
          void create()
        }}
      >
        <ModalFormHeader title={shown.title} />
        {shown.status === "loading" ? (
          <div role="status" className="px-4 py-6 text-[12px]" style={QUIET_STYLE}>
            Reading the quote…
          </div>
        ) : shown.status === "failed" ? (
          <div role="alert" className="px-4 py-4 text-[12px]" style={{ color: "var(--danger-text)" }}>
            The quote could not be read: {shown.error}
          </div>
        ) : (
          <SheetValuesContext value={values}>
            <div
              ref={holder}
              className="flex max-h-[60vh] min-h-0 flex-col px-4 pt-3"
              data-testid="new-quote-fields"
              onFocus={(event) => setTypingIn(event.target instanceof HTMLInputElement && event.target.type !== "checkbox")}
              onBlur={() => setTypingIn(false)}
            >
              <FieldBoxes fields={fields} spacers={[]} marks={NO_MARKS} columns={2} options={COLLECTION_OPTIONS} colours={sheetColours(form.theme)} />
            </div>
          </SheetValuesContext>
        )}
        {shown.dropped.length > 0 && (
          <p className="px-4 pt-3 text-[12px]" style={{ color: "var(--warning)" }} data-testid="new-quote-dropped">
            No place on the current sheets for: {shown.dropped.join(", ")}.
          </p>
        )}
        <WindowMessage shown={shown} onOpenTaken={openTaken} />
        <div className="flex justify-end gap-2 p-4">
          {shown.status === "unknown" || shown.status === "offered" ? (
            <>
              {shown.status === "unknown" ? (
                <button type="button" className={BUTTON} style={QUIET_STYLE} onClick={close}>
                  Close
                </button>
              ) : (
                <button type="button" className={BUTTON} style={QUIET_STYLE} onClick={discard}>
                  Discard
                </button>
              )}
              <button type="button" className={BUTTON} style={YES_STYLE} onClick={() => void tryAgain()} data-testid="new-quote-try-again">
                Try again
              </button>
            </>
          ) : (
            <>
              <button type="button" className={BUTTON} style={QUIET_STYLE} onClick={close} disabled={shown.status === "sending"}>
                Cancel
              </button>
              <button
                type="submit"
                className={BUTTON}
                style={YES_STYLE}
                disabled={!editing || (!fits && !typingIn)}
                title={!editing || fits || typingIn ? undefined : blocked}
                // Pressed, it keeps the focus where it is: the field's blur would turn Create off
                // before the press lands. The submit commits that field.
                onMouseDown={(event) => event.preventDefault()}
                data-testid="new-quote-create"
              >
                {shown.status === "sending" ? "Creating…" : "Create"}
              </button>
            </>
          )}
        </div>
      </form>
    </ModalShell>
  )
}

/** Why the last Create did not make the quote, and what can be done about it. */
function WindowMessage({ shown, onOpenTaken }: { shown: Shown; onOpenTaken: () => void }) {
  if (shown.status === "unknown" || shown.status === "offered") {
    return (
      <p role="alert" className="px-4 pt-3 text-[12px]" style={{ color: "var(--warning)" }} data-testid="new-quote-unknown">
        No answer came, so {shown.kept?.reference ?? "the quote"} may have been made.{shown.status === "unknown" && shown.error !== null && ` ${shown.error}`}
      </p>
    )
  }
  if (shown.status !== "editing" || shown.error === null) return null
  return (
    <div role="alert" className="flex items-center gap-2 px-4 pt-3 text-[12px]" style={{ color: "var(--danger-text)" }} data-testid="new-quote-refused">
      <span className="min-w-0 flex-1">{shown.error}</span>
      {shown.taken !== null && (
        <button type="button" className={BUTTON} style={OTHER_STYLE} onClick={onOpenTaken}>
          Open it
        </button>
      )}
      {shown.releaseMoved && (
        <button type="button" className={BUTTON} style={OTHER_STYLE} onClick={() => window.location.reload()}>
          Reload
        </button>
      )}
    </div>
  )
}
