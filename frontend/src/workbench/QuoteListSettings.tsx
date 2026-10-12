/**
 * The quote list setting (specs/workbench), at the foot of the schema editor: what the
 * underwriter app's Quotes page shows of each quote. A title and up to three summary fields
 * from the one-row input tables, and a headline number from the one-row output tables,
 * each picked from the columns that can be one, or none; what a deploy would refuse is
 * said under them.
 */
import { AlertTriangle, ChevronDown, ChevronRight, ListOrdered, Plus, X } from "lucide-react"
import { useState } from "react"
import type { FieldRef, FormSpec } from "../api/types"
import useWorkbenchFormStore from "../stores/useWorkbenchFormStore"
import { MAX_QUOTE_LIST_FIELDS, quoteListChoices, quoteListProblems, setQuoteList, type QuoteListChoice } from "../utils/workbenchForm"

const SELECT = "focus-ring min-w-0 flex-1 rounded-md px-1.5 py-0.5 text-xs"
const SELECT_STYLE = { border: "1px solid var(--border)", color: "var(--text-primary)" }
const ICON = "toolbar-btn inline-flex items-center rounded-md p-0.5"

const valueOf = (ref: FieldRef | null): string => (ref === null ? "" : JSON.stringify([ref.table, ref.column]))
const refOf = (value: string): FieldRef | null => {
  if (value === "") return null
  const [table, column] = JSON.parse(value) as [string, string]
  return { table, column }
}

/** A column picked from `choices`, or none; one no longer among them stays shown, marked missing. */
function Pick({ label, value, choices, onPick, none }: { label: string; value: FieldRef | null; choices: QuoteListChoice[]; onPick: (ref: FieldRef | null) => void; none: string }) {
  const current = valueOf(value)
  const listed = choices.some((choice) => valueOf(choice.ref) === current)
  return (
    <select aria-label={label} value={current} onChange={(event) => onPick(refOf(event.target.value))} className={SELECT} style={SELECT_STYLE}>
      <option value="">{none}</option>
      {!listed && current !== "" && <option value={current}>Not a column it can show</option>}
      {choices.map((choice) => (
        <option key={valueOf(choice.ref)} value={valueOf(choice.ref)}>
          {choice.label}
        </option>
      ))}
    </select>
  )
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-start gap-2">
      <span className="w-20 shrink-0 pt-1 text-[11px]" style={{ color: "var(--text-secondary)" }}>
        {label}
      </span>
      <div className="flex min-w-0 flex-1 flex-col gap-1">{children}</div>
    </div>
  )
}

export default function QuoteListSettings({ form }: { form: FormSpec }) {
  const change = useWorkbenchFormStore((s) => s.change)
  const [open, setOpen] = useState(true)
  const list = form.quote_list
  const fields = quoteListChoices(form, "field")
  const headlines = quoteListChoices(form, "headline")
  const problems = quoteListProblems(form)
  const set = (patch: Partial<FormSpec["quote_list"]>) => change((spec) => setQuoteList(spec, patch))
  const shown = [list.title !== null, list.fields.length > 0, list.headline !== null].filter(Boolean).length

  return (
    <section aria-label="Quote list" data-testid="quote-list-settings" className="mt-2 rounded-md border" style={{ borderColor: "var(--border)", background: "var(--bg-elevated)" }}>
      <button type="button" onClick={() => setOpen(!open)} aria-expanded={open} className="flex w-full items-center gap-1.5 px-2 py-1.5 text-left">
        {open ? <ChevronDown size={14} style={{ color: "var(--text-muted)" }} /> : <ChevronRight size={14} style={{ color: "var(--text-muted)" }} />}
        <ListOrdered size={13} style={{ color: "var(--accent)" }} aria-hidden="true" />
        <span className="font-semibold" style={{ color: "var(--text-primary)" }}>
          Quote list
        </span>
        <span className="text-[11px]" style={{ color: "var(--text-muted)" }}>
          {shown === 0 ? "the reference and name alone" : `${shown} of 3 set`}
        </span>
        {problems.length > 0 && <AlertTriangle size={12} style={{ color: "var(--warning)" }} aria-label="The quote list has problems" />}
      </button>
      {open && (
        <div className="flex flex-col gap-2 px-2.5 pb-2.5">
          <p className="text-[11px]" style={{ color: "var(--text-muted)" }}>
            What the underwriter app&apos;s Quotes page shows of each quote, beside its reference, stage and who it is with.
          </p>
          <Row label="Title">
            <Pick label="Title" value={list.title} choices={fields} onPick={(title) => set({ title })} none="The quote's name" />
          </Row>
          <Row label="Summary">
            {list.fields.map((ref, index) => (
              <div key={`${valueOf(ref)}-${index}`} className="flex items-center gap-1">
                <Pick
                  label={`Summary field ${index + 1}`}
                  value={ref}
                  choices={fields}
                  none="None"
                  onPick={(picked) => set({ fields: picked === null ? list.fields.filter((_, at) => at !== index) : list.fields.map((each, at) => (at === index ? picked : each)) })}
                />
                <button type="button" className={ICON} onClick={() => set({ fields: list.fields.filter((_, at) => at !== index) })} aria-label={`Remove summary field ${index + 1}`} title="Remove">
                  <X size={12} />
                </button>
              </div>
            ))}
            {list.fields.length < MAX_QUOTE_LIST_FIELDS && (
              <button
                type="button"
                disabled={fields.length === 0}
                onClick={() => {
                  const next = fields.find((choice) => !list.fields.some((ref) => valueOf(ref) === valueOf(choice.ref)))
                  if (next !== undefined) set({ fields: [...list.fields, next.ref] })
                }}
                className="flex w-fit items-center gap-1 text-[11px] font-medium disabled:opacity-50"
                style={{ color: "var(--text-accent)" }}
                title={fields.length === 0 ? "Add a one-row input table first" : "Show another column of each quote"}
              >
                <Plus size={11} /> Add a summary field
              </button>
            )}
          </Row>
          <Row label="Headline">
            <Pick label="Headline" value={list.headline} choices={headlines} onPick={(headline) => set({ headline })} none="None" />
          </Row>
          {problems.map((problem) => (
            <p key={problem} role="alert" className="text-[11px]" style={{ color: "var(--warning)" }}>
              {problem}
            </p>
          ))}
        </div>
      )}
    </section>
  )
}
