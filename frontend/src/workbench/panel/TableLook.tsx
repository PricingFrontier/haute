/** A Table's look in its Display pane: its text, its header, its lines, its frame and each column's own look (specs/workbench). */
import type { FormSpec, TableInputWidget } from "../../api/types"
import { ConfigCheckbox, LabelledRow, SelectRow } from "../../components/form"
import useWorkbenchFormStore from "../../stores/useWorkbenchFormStore"
import { updateWidget, type TableOptions } from "../../utils/workbenchForm"
import { ColourRow } from "./ColourRow"
import { DENSITIES, DIRECTIONS, GRIDLINES, STYLES, TEXT_SIZES, WEIGHTS } from "./lookChoices"
import { ColumnLooks, FrameRows, HeaderRows } from "./lookParts"
import { Section } from "./Section"

/** What a Table's columns' looks call their label and their values. */
const COLUMN_HEADINGS = { label: "Header", values: "Cells" }

/**
 * A Table's Look, Header, Lines, Frame and Columns: its text's size, row numbers, groups and
 * stripes; its header's case, size, direction, fill, font, weight and wrapping; its lines'
 * gridlines, style, weight and colour; whether it is framed, and the frame's weight and
 * colour; and its columns, each opened to its own look, with no width while transposed.
 */
export function TableLook({ form, widget }: { form: FormSpec; widget: TableInputWidget }) {
  const change = useWorkbenchFormStore((s) => s.change)
  const set = (patch: Partial<TableOptions>) => change((spec) => updateWidget(spec, widget.id, patch))
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
        <HeaderRows widget={widget} name="the header's">
          <SelectRow label="Direction" value={widget.header_orientation} choices={DIRECTIONS} onChange={(header_orientation) => set({ header_orientation })} />
        </HeaderRows>
      </Section>
      <Section title="Lines">
        <SelectRow label="Gridlines" value={widget.gridlines} choices={GRIDLINES} onChange={(gridlines) => set({ gridlines })} />
        <SelectRow label="Style" value={widget.grid_style} choices={STYLES} onChange={(grid_style) => set({ grid_style })} />
        <SelectRow label="Weight" value={widget.grid_weight} choices={WEIGHTS} onChange={(grid_weight) => set({ grid_weight })} />
        <ColourRow
          beside
          label="Colour"
          name="the lines"
          value={widget.grid_color}
          update={(spec, grid_color) => updateWidget(spec, widget.id, { grid_color })}
        />
      </Section>
      <Section title="Frame">
        <FrameRows widget={widget} />
      </Section>
      <Section title="Columns">
        <ColumnLooks form={form} widget={widget} headings={COLUMN_HEADINGS} sized={!widget.transposed} />
      </Section>
    </>
  )
}
