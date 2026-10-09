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
 * A component on the sheet while building (specs/workbench): its picture, inert, in a
 * frame that selects it and drags it, and, once selected, eight handles that resize it.
 * A problem with what it shows draws its frame dashed in the warning colour, naming the
 * problem on hover.
 */
export default function SheetWidget({
  widget,
  fields,
  selected,
  problem,
}: {
  widget: Widget
  fields: ShownField[]
  selected: boolean
  /** What stops it showing what it should, or null. */
  problem: string | null
}) {
  const frame = selected
    ? "border-[var(--accent)]"
    : problem !== null
      ? "border-dashed border-[var(--warning)]"
      : "border-transparent group-hover:border-[var(--border-strong)]"
  return (
    <div
      role="group"
      aria-label={widgetName(widget)}
      data-testid={`sheet-widget-${widget.id}`}
      data-selected={selected ? "true" : undefined}
      className="group absolute cursor-move"
      style={{ left: widget.x, top: widget.y, width: widget.w, height: widget.h, zIndex: selected ? 5 : 1 }}
      onPointerDown={(event) => startTransform(event, widget.id)}
    >
      <div className="pointer-events-none h-full">
        <WidgetBody widget={widget} fields={fields} />
      </div>
      <div className={`pointer-events-none absolute -inset-1 rounded-md border ${frame}`} title={problem ?? undefined} />
      {selected && HANDLES.map((handle) => <ResizeHandle key={handle} id={widget.id} handle={handle} />)}
    </div>
  )
}

function ResizeHandle({ id, handle }: { id: string; handle: Handle }) {
  return (
    <div
      data-handle={handle}
      onPointerDown={(event) => startTransform(event, id, handle)}
      className={`absolute size-2.5 rounded-sm border bg-white ${HANDLE_CLASS[handle]}`}
      style={{ borderColor: "var(--accent)" }}
    />
  )
}
