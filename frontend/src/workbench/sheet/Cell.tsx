/** An input cell on a sheet, the control its column's type calls for, and a field's label (specs/workbench). */
import { Info } from "lucide-react"
import { type CSSProperties, useState } from "react"
import { CommittedTextField, INPUT_STYLE } from "../../components/form"
import { formattedNumber } from "../../utils/sheetValues"
import { type SampleValue } from "../../utils/workbenchForm"
import { isNumeric } from "../columnTypes"
import { BOX_CLASS, type Fillable, type Found, GRID_CELL_CLASS, PROBLEM_STYLE, fieldLabel } from "./fieldCells"

/** A field's label, with a star when an input must be filled in and a mark naming its help. */
export function FieldLabel({ found }: { found: Found }) {
  return (
    <>
      {fieldLabel(found)}
      {found?.table.role === "input" && found.column.required && <span style={{ color: "var(--danger)" }}> *</span>}
      {found?.column.help && (
        <span title={found.column.help} className="ml-1 inline-flex align-[-1px] normal-case" style={{ color: "var(--text-secondary)" }}>
          <Info size={11} aria-hidden="true" />
          <span className="sr-only">{found.column.help}</span>
        </span>
      )}
    </>
  )
}

/** Where a tick box sits across its box, by its text's alignment: at the box's start while none is set. */
const TICK_PLACE: Record<string, CSSProperties["justifyContent"]> = { center: "center", right: "flex-end" }

/**
 * The control a field's type calls for, holding what the sheet has for it, or its
 * column's placeholder while empty. A number column's format shows a number while the
 * cell is not being typed in, the text as typed while it is, and its placeholder while
 * that is a number.
 */
export function Cell({
  found,
  value,
  onChange,
  label,
  height,
  box,
  font,
  textStyle,
  problem,
  disabled,
}: {
  found: Fillable
  value: SampleValue | undefined
  onChange: (value: SampleValue) => void
  label: string
  /** Its height class: a Table's rows' or a Collection's boxes'. */
  height: string
  /** In a Collection, a box of its own, its border and its fill; in a Table, null: part of the grid, taking the fill of the cell it is in. */
  box: CSSProperties | null
  /** Its text's colour, or null for its own. */
  font: string | null
  /** How its value's text is set: its size, its weight and where it sits across its cell or its box. */
  textStyle?: CSSProperties
  /** What breaks the column's rules in the value, or null. */
  problem: string | null
  /** The value shows but takes no typing: the quote is read only. */
  disabled: boolean
}) {
  const [typing, setTyping] = useState(false)
  const { column } = found
  const text = typeof value === "string" ? value : ""
  const hint = formattedNumber(column, column.placeholder) ?? column.placeholder
  const boxed = box !== null
  const className = `${boxed ? BOX_CLASS : GRID_CELL_CLASS} ${height}`
  const style = {
    ...(boxed ? { ...INPUT_STYLE, ...box } : {}),
    ...(font === null ? {} : { color: font }),
    ...textStyle,
    ...(problem === null ? {} : PROBLEM_STYLE),
  }
  // A required column's cell says so to a screen reader, as its label's mark does on the
  // sheet; one breaking its column's rules says why.
  const marked = {
    ...(column.required ? { "aria-required": true as const } : {}),
    ...(problem === null ? {} : { "aria-invalid": true as const, title: problem }),
  }
  if (column.type === "bool") {
    const tick = (
      <input
        {...marked}
        type="checkbox"
        aria-label={label}
        checked={value === true}
        onChange={(event) => onChange(event.target.checked)}
        disabled={disabled}
        className="size-4"
        style={{ accentColor: "var(--accent)", ...(boxed || problem === null ? {} : PROBLEM_STYLE) }}
      />
    )
    // In a Collection, in a box as the other fields are, placed across it as its text is; in a grid, in its cell.
    return boxed ? (
      <div className={`flex w-full items-center rounded-md px-2.5 ${height}`} style={{ ...style, justifyContent: TICK_PLACE[String(textStyle?.textAlign)] }}>
        {tick}
      </div>
    ) : (
      tick
    )
  }
  if (column.options.length > 0) {
    return (
      <select {...marked} aria-label={label} value={text} onChange={(event) => onChange(event.target.value)} disabled={disabled} className={className} style={style}>
        <option value="">{hint}</option>
        {/* A value held that the options no longer give is priced as held, so it shows, marked. */}
        {text !== "" && !column.options.includes(text) && <option value={text}>{text}</option>}
        {column.options.map((option) => (
          <option key={option} value={option}>
            {formattedNumber(column, option) ?? option}
          </option>
        ))}
      </select>
    )
  }
  if (column.type === "date") {
    // Committed on blur or Enter, as text is: a year typed digit by digit is one edit.
    return <CommittedTextField {...marked} type="date" aria-label={label} value={text} onCommit={onChange} disabled={disabled} className={className} style={style} />
  }
  return (
    <CommittedTextField
      {...marked}
      aria-label={label}
      value={typing ? text : (formattedNumber(column, text) ?? text)}
      onCommit={onChange}
      onFocus={() => setTyping(true)}
      onBlur={() => setTyping(false)}
      placeholder={hint || undefined}
      inputMode={isNumeric(column.type) ? "decimal" : undefined}
      disabled={disabled}
      className={`${className} ${isNumeric(column.type) ? "text-right tabular-nums" : ""}`}
      style={style}
    />
  )
}
