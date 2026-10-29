/** What a Table's and a Collection's looks share in the Display pane: their labels' rows, their frame, a width and each column's own look and Show when (specs/workbench). */
import { ChevronDown, ChevronRight, X } from "lucide-react"
import { type ReactNode, useState } from "react"
import type { CollectionWidget, FieldRef, FormSpec, TableInputWidget } from "../../api/types"
import { ConfigCheckbox, EditorLabel, FIELD_CLASS, INPUT_STYLE, LabelledRow, QuietAction, SelectRow, ValidatedTextField, wholeNumber } from "../../components/form"
import useWorkbenchFormStore from "../../stores/useWorkbenchFormStore"
import { columnKey, takesNothingTyped } from "../../utils/sheetVisibility"
import {
  COLUMN_WIDTH_MAX,
  COLUMN_WIDTH_MIN,
  LABEL_WIDTH_MAX,
  LABEL_WIDTH_MIN,
  columnLook,
  fieldColumn,
  hasOwnLook,
  setColumnLook,
  showWhenText,
  updateWidget,
  type ColumnLookOptions,
  type SharedLook,
} from "../../utils/workbenchForm"
import { fieldLabel } from "../sheet/fieldCells"
import { ShowWhenEditor, ShowWhenMark } from "../ShowWhenEditor"
import { ColourRow } from "./ColourRow"
import { ALIGNS, CASES, HEADER_SIZES, WEIGHTS } from "./lookChoices"

/** A component whose look the Display pane sets: a Table or a Collection. */
export type LookWidget = TableInputWidget | CollectionWidget

/** The colours a Table and a Collection share. */
type SharedColour = "header_color" | "header_font_color" | "frame_color"

/** Each change to the look a Table and a Collection share, one edit. */
function useSharedLook(widget: LookWidget) {
  const change = useWorkbenchFormStore((s) => s.change)
  return {
    set: (patch: Partial<SharedLook>) => change((spec) => updateWidget(spec, widget.id, patch)),
    colour: (key: SharedColour) => (spec: FormSpec, value: string | null) => updateWidget(spec, widget.id, { [key]: value }),
  }
}

/** A width typed: blank for Auto, or a whole number of pixels, or of percent. */
const pixels = (text: string): string | null => (text.trim() === "" ? null : wholeNumber({ min: COLUMN_WIDTH_MIN, max: COLUMN_WIDTH_MAX })(text))
const percent = (text: string): string | null => (text.trim() === "" ? null : wholeNumber({ min: LABEL_WIDTH_MIN, max: LABEL_WIDTH_MAX })(text))

/** A width in pixels, or, given `share`, in percent of its field, blank for Auto. */
export function WidthRow({
  label = "Width",
  testId,
  value,
  onCommit,
  share = false,
}: {
  label?: string
  testId: string
  value: number | null
  onCommit: (width: number | null) => void
  share?: boolean
}) {
  return (
    <LabelledRow label={label}>
      <ValidatedTextField
        dataTestId={testId}
        value={value === null ? "" : String(value)}
        placeholder="Auto"
        onCommit={(text) => onCommit(text.trim() === "" ? null : Number(text))}
        validate={share ? percent : pixels}
        containerClassName="w-20"
        className={`${FIELD_CLASS} w-full tabular-nums`}
        style={INPUT_STYLE}
      />
      {share && (
        <span className="text-xs" style={{ color: "var(--text-muted)" }}>
          %
        </span>
      )}
    </LabelledRow>
  )
}

/**
 * Its labels' case, size, place, fill, font, weight and wrapping, a Table's header's or a
 * Collection's labels', `children` after the size; `name` says whose they are in each
 * colour's label.
 */
export function HeaderRows({ widget, name, children }: { widget: LookWidget; name: string; children?: ReactNode }) {
  const { set, colour } = useSharedLook(widget)
  return (
    <>
      <SelectRow label="Case" value={widget.header} choices={CASES} onChange={(header) => set({ header })} />
      <SelectRow
        label="Size"
        value={widget.header_font_size === null ? "auto" : String(widget.header_font_size)}
        choices={HEADER_SIZES}
        onChange={(size) => set({ header_font_size: size === "auto" ? null : (Number(size) as SharedLook["font_size"]) })}
      />
      {children}
      <SelectRow label="Align" value={widget.header_align} choices={ALIGNS} onChange={(header_align) => set({ header_align })} />
      <ColourRow beside label="Fill" name={`${name} fill`} value={widget.header_color} update={colour("header_color")} />
      <ColourRow beside label="Font" name={`${name} font`} value={widget.header_font_color} update={colour("header_font_color")} />
      <LabelledRow>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
          <ConfigCheckbox label="Bold" checked={widget.header_bold} onChange={(header_bold) => set({ header_bold })} />
          <ConfigCheckbox label="Wrap" checked={widget.header_wrap} onChange={(header_wrap) => set({ header_wrap })} />
        </div>
      </LabelledRow>
    </>
  )
}

/** Whether it is framed and, while it is, the frame's weight and colour. */
export function FrameRows({ widget }: { widget: LookWidget }) {
  const { set, colour } = useSharedLook(widget)
  return (
    <>
      <LabelledRow>
        <ConfigCheckbox label="Frame" checked={widget.frame} onChange={(frame) => set({ frame })} />
      </LabelledRow>
      {widget.frame && (
        <>
          <SelectRow label="Weight" value={widget.frame_weight} choices={WEIGHTS} onChange={(frame_weight) => set({ frame_weight })} />
          <ColourRow beside label="Colour" name="the frame" value={widget.frame_color} update={colour("frame_color")} />
        </>
      )}
    </>
  )
}

/** What a column's look calls its label and its values: a Table's header and cells, a Collection's label and box. */
export interface LookHeadings {
  label: string
  values: string
}

/**
 * Its fields in its order, each opened to its own look, its label's and its values' as
 * `headings` names them, with a width while `sized`, and its Show when; one that has a look
 * is marked with a dot, and one with conditions with an eye.
 */
export function ColumnLooks({ form, widget, headings, sized }: { form: FormSpec; widget: LookWidget; headings: LookHeadings; sized: boolean }) {
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
        const key = columnKey(field.table, field.column)
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
              {look.show_when.conditions.length > 0 && <ShowWhenMark hidden={false} title={showWhenText(form, look.show_when)} />}
              {hasOwnLook(look) && (
                <span className="size-1.5 shrink-0 rounded-full" style={{ background: "var(--accent)" }}>
                  <span className="sr-only">, has a look of its own</span>
                </span>
              )}
            </button>
            {opened && <ColumnLookFields form={form} widget={widget} field={field} label={label} look={look} headings={headings} sized={sized} />}
          </div>
        )
      })}
    </div>
  )
}

/**
 * One column's look: its label's fill, font and place, its values' fill, font and place,
 * its width while `sized`, and bold; Clear while it has one, which keeps its Show when; and
 * its Show when.
 */
function ColumnLookFields({
  form,
  widget,
  field,
  label,
  look,
  headings,
  sized,
}: {
  form: FormSpec
  widget: LookWidget
  field: FieldRef
  label: string
  look: ColumnLookOptions
  headings: LookHeadings
  sized: boolean
}) {
  const change = useWorkbenchFormStore((s) => s.change)
  const set = (patch: Partial<ColumnLookOptions> | "clear") => change((spec) => setColumnLook(spec, widget.id, field, patch))
  const colour = (key: "header_color" | "header_font_color" | "cell_color" | "font_color") => (spec: FormSpec, value: string | null) =>
    setColumnLook(spec, widget.id, field, { [key]: value })
  const labelName = `${label}'s ${headings.label.toLowerCase()}`
  return (
    <div role="group" aria-label={`${label}'s look`} className="flex flex-col gap-1.5 px-1 pt-1 pb-2">
      <EditorLabel as="div">{headings.label}</EditorLabel>
      <ColourRow beside label="Fill" name={`${labelName} fill`} value={look.header_color} update={colour("header_color")} />
      <ColourRow beside label="Font" name={`${labelName} font`} value={look.header_font_color} update={colour("header_font_color")} />
      <SelectRow label="Align" name={`${labelName} align`} value={look.header_align} choices={ALIGNS} onChange={(header_align) => set({ header_align })} />
      <EditorLabel as="div">{headings.values}</EditorLabel>
      <ColourRow beside label="Fill" name={`${label}'s fill`} value={look.cell_color} update={colour("cell_color")} />
      <ColourRow beside label="Font" name={`${label}'s font`} value={look.font_color} update={colour("font_color")} />
      <SelectRow label="Align" name={`${label}'s align`} value={look.align} choices={ALIGNS} onChange={(align) => set({ align })} />
      {sized && <WidthRow testId={`column-width-${columnKey(field.table, field.column)}`} value={look.width} onCommit={(width) => set({ width })} />}
      <LabelledRow>
        <ConfigCheckbox label="Bold" checked={look.bold} onChange={(bold) => set({ bold })} />
      </LabelledRow>
      {hasOwnLook(look) && (
        <QuietAction icon={X} onClick={() => set("clear")} className="self-start">
          Clear
        </QuietAction>
      )}
      <EditorLabel as="div">Show when</EditorLabel>
      <ShowWhenEditor form={form} showWhen={look.show_when} priced={takesNothingTyped(form, field)} onChange={(show_when) => set({ show_when })} />
    </div>
  )
}
