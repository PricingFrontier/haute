/** The sheets' colours as a component draws them: the form's `theme` resolved, a colour left unset the editor's own (specs/workbench). */
import type { Theme } from "../../api/types"

/**
 * The sheets' colours as drawn: the form's theme, each the editor's own while it sets none, a
 * border colour drawing every line its components draw.
 */
export function sheetColours(theme: Theme) {
  const border = theme.border_color
  return {
    /** A framed component's background. */
    background: theme.background_color ?? "var(--bg-panel)",
    /** A frame, the rule under a title, the line before an output column, and a box's border. */
    frame: border ?? "var(--border)",
    /** The rule under a Table's header. */
    headRule: border ?? "var(--border-bright)",
    /** A Table's gridlines. */
    gridline: border ?? "var(--border-subtle)",
    /** The text, or null for each text's own colour. */
    font: theme.font_color,
    /** The fill of input cells and boxes, or null for their own. */
    input: theme.input_cell_color,
    /** The fill of output cells and boxes, or null for their shade. */
    output: theme.output_cell_color,
    /** The components' background and the sheet's as the theme sets them, or null: a Table's header strip takes them. */
    componentFill: theme.background_color,
    sheetFill: theme.sheet_color,
  }
}

/** The sheets' colours as drawn (`sheetColours`). */
export type SheetColours = ReturnType<typeof sheetColours>
