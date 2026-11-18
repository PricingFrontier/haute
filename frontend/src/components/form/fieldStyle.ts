/**
 * The look the panels' fields share (specs/frontend-shared): the input's colours, and the
 * class of a small field in a side panel, with a denser one for the schema's rows.
 */
export const INPUT_STYLE = {
  background: "var(--bg-input)",
  border: "1px solid var(--border)",
  color: "var(--text-primary)",
} as const

/** A small field in a side panel: a select, a text or a number box. */
export const FIELD_CLASS = "focus-ring min-w-0 rounded-md px-1.5 py-1 text-xs"

/** The same, in the schema editor's dense rows, a column to a row. */
export const COMPACT_FIELD_CLASS = "focus-ring min-w-0 rounded-md px-1.5 py-0.5 text-xs"
