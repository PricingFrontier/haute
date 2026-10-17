/**
 * How a component looks on a sheet (specs/workbench): a Collection's fields as labelled
 * boxes and a Table's as the columns of a grid, drawn as its options say, a Chart's
 * plotted (`ChartBody`), and a Heading as its text. An input column takes the values the
 * sheet holds, none while building, as a new quote holds none, and the sample or the
 * underwriter app's quote in Preview, through the control its type calls for, marked when
 * the value breaks the column's rules; an output column is shaded and shows what pricing
 * those values gave its row, dimmed until the next answer. Pressing a cell types there.
 */
import useWorkbenchFormStore from "../stores/useWorkbenchFormStore"
import useWorkbenchViewStore from "../stores/useWorkbenchViewStore"
import { BLANK_THEME, type ShownField, type Widget } from "../utils/workbenchForm"
import ChartBody from "./ChartBody"
import { FieldBoxes } from "./sheet/FieldBoxes"
import { LINE_WIDTH } from "./sheet/fieldCells"
import { FieldGrid } from "./sheet/FieldGrid"
import { HeadingBody } from "./sheet/HeadingBody"
import { sheetColours } from "./sheet/sheetColours"

export default function WidgetBody({ widget, fields }: { widget: Widget; fields: ShownField[] }) {
  const previewing = useWorkbenchViewStore((s) => s.section === "preview")
  const colours = sheetColours(useWorkbenchFormStore((s) => s.form?.theme ?? BLANK_THEME))
  if (widget.type === "heading") return <HeadingBody widget={widget} colours={colours} />
  // A Table or a Collection without its frame sits on the sheet as its title and fields alone.
  const framed = widget.type === "chart" || widget.frame
  // A Table's frame, and the rule under its title, in its own colour and weight while it sets them.
  const frame = widget.type === "tableInput" ? (widget.frame_color ?? colours.frame) : colours.frame
  const frameWidth = widget.type === "tableInput" ? LINE_WIDTH[widget.frame_weight] : 1
  return (
    <div
      className={`flex h-full flex-col overflow-hidden ${framed ? "rounded-lg" : ""}`}
      style={framed ? { border: `${frameWidth}px solid ${frame}`, background: colours.background } : undefined}
    >
      {widget.title && (
        <div className="px-3 py-2 text-xs font-semibold" style={{ borderBottom: `1px solid ${frame}`, color: colours.font ?? "var(--text-primary)" }}>
          {widget.title}
        </div>
      )}
      {fields.length === 0 ? (
        // Fields are chosen while building: in Preview, which has no panel, it is its frame alone.
        previewing ? null : (
          <div
            className="m-3 grid flex-1 place-items-center rounded-md text-center text-[11px]"
            style={{ border: "1px dashed var(--border)", color: "var(--text-secondary)" }}
          >
            Choose its fields from the schema in the panel on the right
          </div>
        )
      ) : widget.type === "chart" ? (
        <ChartBody widget={widget} />
      ) : widget.type === "collection" ? (
        <FieldBoxes fields={fields} columns={widget.columns} options={widget} colours={colours} />
      ) : (
        <FieldGrid fields={fields} rows={widget.rows} options={widget} colours={colours} />
      )}
    </div>
  )
}
