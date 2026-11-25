/**
 * Validation in a toolbar (specs/workbench): outlined green while the quote on the sheets
 * passes every check marking it final makes, red while it fails any, saying how many fail,
 * and the box it opens under it, as Display opens one: the checks, those failing first, then
 * those passing, each by its table and its column's label with what it asks for. The checks
 * are `quoteChecks`' on the `values` typed and the `tables` their price gave as last made
 * (null for none). Pressing outside the box, or Escape, closes it, Escape
 * giving the focus back to the button.
 */
import { ShieldAlert, ShieldCheck } from "lucide-react"
import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react"
import type { FormSpec } from "../api/types"
import { EditorLabel } from "../components/form"
import { ToolbarButton } from "../haute-ui"
import useClickOutside from "../hooks/useClickOutside"
import { checkLine, quoteChecks, type QuoteCheck } from "../utils/quoteChecks"
import type { PricedRows, RowsByTable } from "../utils/sheetValues"

interface ValidationButtonProps {
  /** The form whose checks are made; null until it is read. */
  form: FormSpec | null
  values: RowsByTable
  tables: Readonly<Record<string, PricedRows>> | null
  disabled?: boolean
}

const NO_CHECKS: QuoteCheck[] = []

export default function ValidationButton({ form, values, tables, disabled = false }: ValidationButtonProps) {
  const checks = useMemo(() => (form === null ? NO_CHECKS : quoteChecks(form, values, tables)), [form, values, tables])
  const failing = checks.filter((check) => !check.passed)
  const passing = checks.filter((check) => check.passed)
  const [open, setOpen] = useState(false)
  if (disabled && open) setOpen(false)
  const box = useRef<HTMLDivElement>(null)
  const dialog = useRef<HTMLDivElement>(null)
  // The box takes the focus as it opens.
  useEffect(() => {
    if (open) dialog.current?.focus()
  }, [open])
  const close = useCallback(() => setOpen(false), [])
  useClickOutside(box, close, open)
  const onKeyDown = (event: KeyboardEvent) => {
    if (event.key !== "Escape" || !open) return
    // Not the sheet's own Escape, which would deselect.
    event.preventDefault()
    setOpen(false)
    box.current?.querySelector<HTMLButtonElement>(":scope > button")?.focus()
  }
  const passes = failing.length === 0
  // Disabled, it says nothing of the quote: the same button in Build as before the form is read.
  const off = disabled || form === null
  const colour = passes ? "var(--success)" : "var(--danger)"

  return (
    <div ref={box} className="relative" onKeyDown={onKeyDown}>
      <ToolbarButton
        icon={passes ? ShieldCheck : ShieldAlert}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-pressed={open}
        onClick={() => setOpen(!open)}
        disabled={off}
        title={passes ? "The quote passes every check marking it final makes" : `${failing.length} ${failing.length === 1 ? "check fails" : "checks fail"}: the quote cannot be marked final yet`}
        data-testid="validation-button"
        data-state={passes ? "passing" : "failing"}
        // Inside the button's edge, leaving its focus ring its own.
        style={off ? undefined : { boxShadow: `inset 0 0 0 1.5px ${colour}` }}
      >
        Validation{passes || off ? "" : ` (${failing.length})`}
      </ToolbarButton>
      {open && form !== null && (
        <div
          ref={dialog}
          role="dialog"
          aria-label="Validation"
          tabIndex={-1}
          data-testid="validation-box"
          className="absolute top-full left-0 z-50 mt-1 flex max-h-[28rem] w-[26rem] flex-col gap-3 overflow-y-auto rounded-lg p-3 text-xs shadow-2xl"
          style={{ background: "var(--bg-panel)", border: "1px solid var(--border)" }}
        >
          {checks.length === 0 && <span style={{ color: "var(--text-muted)" }}>The form has nothing to check.</span>}
          <CheckList title="Failing" checks={failing} form={form} colour="var(--danger-text)" testId="validation-failing" />
          <CheckList title="Passing" checks={passing} form={form} colour="var(--success)" testId="validation-passing" />
        </div>
      )}
    </div>
  )
}

/** The checks of one kind of answer, under their count; nothing for none. */
function CheckList({ title, checks, form, colour, testId }: { title: string; checks: readonly QuoteCheck[]; form: FormSpec; colour: string; testId: string }) {
  if (checks.length === 0) return null
  return (
    <div className="flex flex-col gap-1.5">
      <EditorLabel as="div">
        {title} ({checks.length})
      </EditorLabel>
      <ul className="flex flex-col gap-1" data-testid={testId}>
        {checks.map((check) => {
          const line = checkLine(form, check)
          return (
            <li key={`${check.table}:${check.column}:${check.kind}`} className="flex items-baseline gap-2">
              <span aria-hidden="true" className="h-1.5 w-1.5 shrink-0 rounded-full" style={{ background: colour }} />
              <span className="min-w-0">
                <span className="font-medium" style={{ color: "var(--text-primary)" }}>
                  {line.name}
                </span>
                {(line.rule !== "" || line.rows !== "") && <span style={{ color: "var(--text-secondary)" }}> {[line.rule, line.rows].filter((part) => part !== "").join(" · ")}</span>}
              </span>
            </li>
          )
        })}
      </ul>
    </div>
  )
}
