/** A Collection's look in its Display pane: its text, its labels, its boxes, its frame and each field's own look (specs/workbench). */
import type { CollectionWidget, FormSpec } from "../../api/types"
import { SelectRow } from "../../components/form"
import useWorkbenchFormStore from "../../stores/useWorkbenchFormStore"
import { updateWidget, type CollectionOptions } from "../../utils/workbenchForm"
import { ColourRow } from "./ColourRow"
import { ALIGNS, DENSITIES, POSITIONS, STYLES, TEXT_SIZES, WEIGHTS } from "./lookChoices"
import { ColumnLooks, FrameRows, HeaderRows, WidthRow } from "./lookParts"
import { Section } from "./Section"

/** What a Collection's fields' looks call their label and their values. */
const FIELD_HEADINGS = { label: "Label", values: "Box" }

/**
 * A Collection's Look, Labels, Boxes, Frame and Fields, as a Table's are set: its density
 * and its text's size; its labels' position, their width while beside their boxes, their
 * case, size, place, fill, font, weight and wrapping; its boxes' borders' style, weight and
 * colour, where their values sit and their width; whether it is framed, and the frame's
 * weight and colour; and its fields, each opened to its own look.
 */
export function CollectionLook({ form, widget }: { form: FormSpec; widget: CollectionWidget }) {
  const change = useWorkbenchFormStore((s) => s.change)
  const set = (patch: Partial<CollectionOptions>) => change((spec) => updateWidget(spec, widget.id, patch))
  return (
    <>
      <Section title="Look">
        <SelectRow label="Density" value={widget.density} choices={DENSITIES} onChange={(density) => set({ density })} />
        <SelectRow label="Text size" value={String(widget.font_size)} choices={TEXT_SIZES} onChange={(size) => set({ font_size: Number(size) as CollectionOptions["font_size"] })} />
      </Section>
      <Section title="Labels">
        <SelectRow label="Position" value={widget.labels} choices={POSITIONS} onChange={(labels) => set({ labels })} />
        {widget.labels === "beside" && <WidthRow share testId="label-width" value={widget.label_width} onCommit={(label_width) => set({ label_width })} />}
        <HeaderRows widget={widget} name="the labels'">
          <SelectRow label="Align" value={widget.header_align} choices={ALIGNS} onChange={(header_align) => set({ header_align })} />
        </HeaderRows>
      </Section>
      <Section title="Boxes">
        <SelectRow label="Style" value={widget.box_style} choices={STYLES} onChange={(box_style) => set({ box_style })} />
        <SelectRow label="Weight" value={widget.box_weight} choices={WEIGHTS} onChange={(box_weight) => set({ box_weight })} />
        <ColourRow beside label="Colour" name="the boxes" value={widget.box_color} update={(spec, box_color) => updateWidget(spec, widget.id, { box_color })} />
        <SelectRow label="Align" value={widget.box_align} choices={ALIGNS} onChange={(box_align) => set({ box_align })} />
        <WidthRow testId="box-width" value={widget.box_width} onCommit={(box_width) => set({ box_width })} />
      </Section>
      <Section title="Frame">
        <FrameRows widget={widget} />
      </Section>
      <Section title="Fields">
        <ColumnLooks form={form} widget={widget} headings={FIELD_HEADINGS} sized />
      </Section>
    </>
  )
}
