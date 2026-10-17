/** A Table's look in its Display pane: its text, its header, its lines, its frame and each column's own look (specs/workbench). */
import { ChevronDown, ChevronRight, X } from "lucide-react"
import { useState } from "react"
import type { FieldRef, FormSpec, TableInputWidget } from "../../api/types"
import { ConfigCheckbox, EditorLabel, FIELD_CLASS, INPUT_STYLE, LabelledRow, QuietAction, SelectRow, ValidatedTextField, wholeNumber } from "../../components/form"
import useWorkbenchFormStore from "../../stores/useWorkbenchFormStore"
import {
  COLUMN_WIDTH_MAX,
  COLUMN_WIDTH_MIN,
  PLAIN_COLUMN_LOOK,
  columnLook,
  fieldColumn,
  setColumnLook,
  updateWidget,
  type ColumnLookOptions,
  type TableOptions,
} from "../../utils/workbenchForm"
import { fieldLabel } from "../sheet/fieldCells"
import { ColourRow } from "./ColourRow"
import { ALIGNS, CASES, DENSITIES, DIRECTIONS, GRIDLINES, HEADER_SIZES, STYLES, TEXT_SIZES, WEIGHTS } from "./lookChoices"
import { Section } from "./Section"

/** A Table's own colours. */
type TableColour = "header_color" | "header_font_color" | "grid_color" | "frame_color"

/** A width typed: blank for as wide as the grid makes it, or a whole number of pixels. */
const width = (text: string): string | null => (text.trim() === "" ? null : wholeNumber({ min: COLUMN_WIDTH_MIN, max: COLUMN_WIDTH_MAX })(text))

/**
 * A Table's Look, Header, Lines, Frame and Columns: its text's size, row numbers, groups and
 * stripes; its header's case, size, direction, fill, font, weight and wrapping; its lines'
 * gridlines, style, weight and colour; whether it is framed, and the frame's weight and
 * colour; and its columns, each opened to its own look.
 */
export function TableLook({ form, widget }: { form: FormSpec; widget: TableInputWidget }) {
  const change = useWorkbenchFormStore((s) => s.change)
  const set = (patch: Partial<TableOptions>) => change((spec) => updateWidget(spec, widget.id, patch))
  const colour = (key: TableColour) => (spec: FormSpec, value: string | null) => updateWidget(spec, widget.id, { [key]: value } as Partial<TableOptions>)
  return (
    <>
      <Section title="Look">
        <SelectRow label="Density" value={widget.density} choices={DENSITIES} onChange={(density) => set({ density })} />
        <SelectRow label="Text size" value={String(widget.font_size)} choices={TEXT_SIZES} onChange={(size) => set({ font_size: Number(size) as TableOptions["font_size"] })} />
        <LabelledRow>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
            <ConfigCheckbox label="Row numbers" checked={widget.row_numbers} onChange={(row_numbers) => set({ row_numbers })} />
            <ConfigCheckbox label="Groups" checked={widget.groups} onChange={(groups) => set({ groups })} />
            <ConfigCheckbox label="Striped rows" checked={widget.striped} onChange={(striped) => set({ striped })} />
          </div>
        </LabelledRow>
      </Section>
      <Section title="Header">
        <SelectRow label="Case" value={widget.header} choices={CASES} onChange={(header) => set({ header })} />
        <SelectRow
          label="Size"
          value={widget.header_font_size === null ? "auto" : String(widget.header_font_size)}
          choices={HEADER_SIZES}
          onChange={(size) => set({ header_font_size: size === "auto" ? null : (Number(size) as TableOptions["font_size"]) })}
        />
        <SelectRow label="Direction" value={widget.header_orientation} choices={DIRECTIONS} onChange={(header_orientation) => set({ header_orientation })} />
        <ColourRow beside label="Fill" name="the header's fill" value={widget.header_color} update={colour("header_color")} />
        <ColourRow beside label="Font" name="the header's font" value={widget.header_font_color} update={colour("header_font_color")} />
        <LabelledRow>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
            <ConfigCheckbox label="Bold" checked={widget.header_bold} onChange={(header_bold) => set({ header_bold })} />
            <ConfigCheckbox label="Wrap" checked={widget.header_wrap} onChange={(header_wrap) => set({ header_wrap })} />
          </div>
        </LabelledRow>
      </Section>
      <Section title="Lines">
        <SelectRow label="Gridlines" value={widget.gridlines} choices={GRIDLINES} onChange={(gridlines) => set({ gridlines })} />
        <SelectRow label="Style" value={widget.grid_style} choices={STYLES} onChange={(grid_style) => set({ grid_style })} />
        <SelectRow label="Weight" value={widget.grid_weight} choices={WEIGHTS} onChange={(grid_weight) => set({ grid_weight })} />
        <ColourRow beside label="Colour" name="the lines" value={widget.grid_color} update={colour("grid_color")} />
      </Section>
      <Section title="Frame">
        <LabelledRow>
          <ConfigCheckbox label="Frame" checked={widget.frame} onChange={(frame) => set({ frame })} />
        </LabelledRow>
        {widget.frame && (
          <>
            <SelectRow label="Weight" value={widget.frame_weight} choices={WEIGHTS} onChange={(frame_weight) => set({ frame_weight })} />
            <ColourRow beside label="Colour" name="the frame" value={widget.frame_color} update={colour("frame_color")} />
          </>
        )}
      </Section>
      <Section title="Columns">
        <ColumnLooks form={form} widget={widget} />
      </Section>
    </>
  )
}

/** The Table's columns in its order, each opened to its own look; one that has a look is marked. */
function ColumnLooks({ form, widget }: { form: FormSpec; widget: TableInputWidget }) {
  const [open, setOpen] = useState<ReadonlySet<string>>(() => new Set())
  const toggle = (key: string) =>
    setOpen((was) => {
      const next = new Set(was)
      if (!next.delete(key)) next.add(key)
      return next
    })
  return (
    <div className="flex flex-col gap-0.5 rounded-md p-1" style={{ border: "1px solid var(--border)", background: "var(--bg-elevated)" }}>
      {widget.fields.map((field) => {
        const key = `${field.table}:${field.column}`
        const label = fieldLabel(fieldColumn(form, field))
        const look = columnLook(widget, field)
        const opened = open.has(key)
        return (
          <div key={key} className="flex flex-col">
            <button
              type="button"
              aria-expanded={opened}
              onClick={() => toggle(key)}
              className="focus-ring flex h-6 items-center gap-1.5 rounded px-1 text-left transition-colors hover:bg-[var(--bg-hover)]"
            >
              {opened ? <ChevronDown size={12} aria-hidden="true" /> : <ChevronRight size={12} aria-hidden="true" />}
              <span className="min-w-0 flex-1 truncate" style={{ color: "var(--text-primary)" }}>
                {label}
              </span>
              {look !== PLAIN_COLUMN_LOOK && (
                <span className="size-1.5 shrink-0 rounded-full" style={{ background: "var(--accent)" }}>
                  <span className="sr-only">, has a look of its own</span>
                </span>
              )}
            </button>
            {opened && <ColumnLookFields widget={widget} field={field} label={label} look={look} />}
          </div>
        )
      })}
    </div>
  )
}

/** One column's look: its header's fill and font, its cells' fill and font, where its values sit, its width unless transposed, and bold; Clear while it has one. */
function ColumnLookFields({ widget, field, label, look }: { widget: TableInputWidget; field: FieldRef; label: string; look: ColumnLookOptions }) {
  const change = useWorkbenchFormStore((s) => s.change)
  const set = (patch: Partial<ColumnLookOptions> | "clear") => change((spec) => setColumnLook(spec, widget.id, field, patch))
  const colour = (key: "header_color" | "header_font_color" | "cell_color" | "font_color") => (spec: FormSpec, value: string | null) =>
    setColumnLook(spec, widget.id, field, { [key]: value })
  return (
    <div role="group" aria-label={`${label}'s look`} className="flex flex-col gap-1.5 px-1 pt-1 pb-2">
      <EditorLabel as="div">Header</EditorLabel>
      <ColourRow beside label="Fill" name={`${label}'s header fill`} value={look.header_color} update={colour("header_color")} />
      <ColourRow beside label="Font" name={`${label}'s header font`} value={look.header_font_color} update={colour("header_font_color")} />
      <EditorLabel as="div">Cells</EditorLabel>
      <ColourRow beside label="Fill" name={`${label}'s fill`} value={look.cell_color} update={colour("cell_color")} />
      <ColourRow beside label="Font" name={`${label}'s font`} value={look.font_color} update={colour("font_color")} />
      <SelectRow label="Align" value={look.align} choices={ALIGNS} onChange={(align) => set({ align })} />
      {!widget.transposed && (
        <LabelledRow label="Width">
          <ValidatedTextField
            dataTestId={`column-width-${field.table}:${field.column}`}
            value={look.width === null ? "" : String(look.width)}
            placeholder="Auto"
            onCommit={(text) => set({ width: text.trim() === "" ? null : Number(text) })}
            validate={width}
            containerClassName="w-20"
            className={`${FIELD_CLASS} w-full tabular-nums`}
            style={INPUT_STYLE}
          />
        </LabelledRow>
      )}
      <LabelledRow>
        <ConfigCheckbox label="Bold" checked={look.bold} onChange={(bold) => set({ bold })} />
      </LabelledRow>
      {look !== PLAIN_COLUMN_LOOK && (
        <QuietAction icon={X} onClick={() => set("clear")} className="self-start">
          Clear
        </QuietAction>
      )}
    </div>
  )
}
