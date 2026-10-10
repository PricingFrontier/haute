import useWorkbenchViewStore from "../stores/useWorkbenchViewStore"
import { HANDLES, type Handle } from "../utils/sheetGeometry"
import { widgetName, type ShownField, type Widget } from "../utils/workbenchForm"
import { startTransform } from "./sheetInteractions"
import WidgetBody from "./WidgetBody"

const HANDLE_CLASS: Record<Handle, string> = {
  nw: "-top-1.5 -left-1.5 cursor-nw-resize",
  n: "-top-1.5 left-1/2 -translate-x-1/2 cursor-n-resize",
  ne: "-top-1.5 -right-1.5 cursor-ne-resize",
  e: "top-1/2 -right-1.5 -translate-y-1/2 cursor-e-resize",
  se: "-right-1.5 -bottom-1.5 cursor-se-resize",
  s: "-bottom-1.5 left-1/2 -translate-x-1/2 cursor-s-resize",
  sw: "-bottom-1.5 -left-1.5 cursor-sw-resize",
  w: "top-1/2 -left-1.5 -translate-y-1/2 cursor-w-resize",
}

/**
 * A component on the sheet (specs/workbench): its picture, inert but for its cells while
 * building, in a frame that selects it, by the pointer or by the keyboard's focus, and
 * drags it and, once selected, grows eight handles that resize it. In Preview the frame
 * does nothing: the sheet is an underwriter's, the picture scrolls and only the cells
 * respond. A problem with what it shows draws the frame dashed in the warning colour,
 * naming the problem on hover and to a screen reader.
 */
export default function SheetWidget({
  widget,
  fields,
  selected,
  problem,
  editable,
}: {
  widget: Widget
  fields: ShownField[]
  selected: boolean
  /** What stops it showing what it should, or null. */
  problem: string | null
  /** While building: selected, moved and resized by its frame. */
  editable: boolean
}) {
  const frame = selected
    ? "border-[var(--accent)]"
    : problem !== null
      ? "border-dashed border-[var(--warning)]"
      : editable
        ? "border-transparent group-hover:border-[var(--border-bright)]"
        : "border-transparent"
  const select = useWorkbenchViewStore((s) => s.select)
  const problemId = `${widget.id}-problem`
  return (
    <div
      role="group"
      aria-label={widgetName(widget)}
      aria-describedby={problem === null ? undefined : problemId}
      title={problem ?? undefined}
      tabIndex={editable ? 0 : undefined}
      data-testid={`sheet-widget-${widget.id}`}
      data-selected={selected ? "true" : undefined}
      className={`group focus-ring absolute ${editable ? "cursor-move" : ""}`}
      style={{ left: widget.x, top: widget.y, width: widget.w, height: widget.h, zIndex: selected ? 5 : 1 }}
      onPointerDown={editable ? (event) => startTransform(event, widget.id) : undefined}
      onFocus={editable ? (event) => { if (event.target === event.currentTarget) select(widget.id) } : undefined}
    >
      <div className={`h-full ${editable ? "pointer-events-none" : ""}`}>
        <WidgetBody widget={widget} fields={fields} />
      </div>
      <div className={`pointer-events-none absolute -inset-1 rounded-md border ${frame}`} />
      {problem !== null && (
        <span id={problemId} className="sr-only">
          {problem}
        </span>
      )}
      {editable && selected && HANDLES.map((handle) => <ResizeHandle key={handle} id={widget.id} handle={handle} />)}
    </div>
  )
}

function ResizeHandle({ id, handle }: { id: string; handle: Handle }) {
  return (
    <div
      data-handle={handle}
      onPointerDown={(event) => startTransform(event, id, handle)}
      className={`absolute size-2.5 rounded-sm border ${HANDLE_CLASS[handle]}`}
      style={{ borderColor: "var(--accent)", background: "var(--bg-base)" }}
    />
  )
}
