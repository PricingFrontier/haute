/** A one-row input column's default (specs/workbench). */
import { useState } from "react"
import type { SchemaColumn } from "../../api/types"
import { COMPACT_FIELD_CLASS, CommittedTextField, ConfigCheckbox, INPUT_STYLE } from "../../components/form"
import { formattedNumber } from "../../utils/sheetValues"

/**
 * What a new quote starts a one-row input column with, as its cells take a value: ticked
 * or not, one of the allowed values while it has some (one no longer allowed kept in view,
 * for its problem to name), a date, or typed. Blank or unticked, it has none. A number
 * column's format shows its values as a cell shows them, the text as typed while it is
 * being typed in.
 */
export function DefaultField({ column, set }: { column: SchemaColumn; set: (patch: Partial<SchemaColumn>) => void }) {
  const [typing, setTyping] = useState(false)
  if (column.type === "bool") {
    return (
      <ConfigCheckbox
        label={`${column.name} starts ticked`}
        checked={column.default_value === true}
        onChange={(ticked) => set({ default_value: ticked ? true : "" })}
      />
    )
  }
  const text = typeof column.default_value === "string" ? column.default_value : ""
  const label = `Default of ${column.name}`
  if (column.options.length > 0) {
    return (
      <select
        aria-label={label}
        value={text}
        onChange={(e) => set({ default_value: e.target.value })}
        className={`${COMPACT_FIELD_CLASS} w-full`}
        style={INPUT_STYLE}
      >
        <option value="" />
        {[...column.options, ...(text === "" || column.options.includes(text) ? [] : [text])].map((option) => (
          <option key={option} value={option}>
            {formattedNumber(column, option) ?? option}
          </option>
        ))}
      </select>
    )
  }
  return (
    <CommittedTextField
      aria-label={label}
      type={column.type === "date" ? "date" : "text"}
      value={typing ? text : (formattedNumber(column, text) ?? text)}
      onCommit={(value) => set({ default_value: value.trim() })}
      onFocus={() => setTyping(true)}
      onBlur={() => setTyping(false)}
      className={`${COMPACT_FIELD_CLASS} w-full`}
      style={INPUT_STYLE}
    />
  )
}
