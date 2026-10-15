import { memo, useMemo } from "react"
import type { FormSchema } from "../api/types"
import useWorkbenchViewStore from "../stores/useWorkbenchViewStore"
import { HANDLES, type Handle } from "../utils/sheetGeometry"
import { shownFields, widgetName, type Widget } from "../utils/workbenchForm"
import { startTransform } from "./sheetInteractions"
import { ShowWhenMark } from "./ShowWhenEditor"
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
 * A component on the sheet (specs/workbench): its picture, inert while building, cells
 * and all, so a press anywhere on it lands on its frame, which selects it, by the pointer
 * (taking the focus) or by the keyboard's focus, and drags it and, once selected, grows
 * eight handles that resize it. In Preview the frame does nothing: the sheet is an underwriter's, the picture
 * scrolls and only the cells respond. A problem with what it shows draws the frame dashed
 * in the warning colour, naming the problem on hover and to a screen reader. While
 * building, a component with conditions carries an eye naming them, top right, or top left
 * on a right-aligned Heading so it never covers the end of the text, and one a new quote
 * hides is drawn faded and its eye struck through. It is drawn again only when what it is
 * given changes: the fields it shows are its own and `schema`'s.
 */
export default memo(function SheetWidget({
  widget,
  schema,
  selected,
  problem,
  editable,
  hidden,
  conditions,
  layer,
}: {
  widget: Widget
  schema: FormSchema
  selected: boolean
  /** What stops it showing what it should, or null. */
  problem: string | null
  /** While building: selected, moved and resized by its frame. */
  editable: boolean
  /** The sheet's values hide it: drawn faded while building (Preview does not draw it). */
  hidden: boolean
  /** Its conditions as a sentence, for its eye while building; null for none, or in Preview. */
  conditions: string | null
  /**
   * Its place in the stack: what it covers where it overlaps another, the form's order of
   * the components and the selected one above them, apart from the order they are read in.
   */
  layer: number
}) {
  const frame = selected
    ? "border-[var(--accent)]"
    : problem !== null
      ? "border-dashed border-[var(--warning)]"
      : editable
        ? "border-transparent group-hover:border-[var(--border-bright)]"
        : "border-transparent"
  const select = useWorkbenchViewStore((s) => s.select)
  const fields = useMemo(() => shownFields(schema, widget), [schema, widget])
  const problemId = `${widget.id}-problem`
  return (
    <div
      role="group"
      aria-label={widgetName(widget)}
      aria-describedby={problem === null ? undefined : problemId}
      title={problem ?? undefined}
      tabIndex={editable ? 0 : undefined}
      data-testid={`sheet-widget-${widget.id}`}
      data-widget={widget.id}
      data-selected={selected ? "true" : undefined}
      className={`group focus-ring absolute ${editable ? "cursor-move" : ""}`}
      style={{ left: widget.x, top: widget.y, width: widget.w, height: widget.h, zIndex: layer }}
      onPointerDown={
        editable
          ? (event) => {
              startTransform(event, widget.id)
              // The press's default, which would focus the frame, is prevented to drag it.
              if (event.button === 0) event.currentTarget.focus({ preventScroll: true })
            }
          : undefined
      }
      onFocus={editable ? (event) => { if (event.target === event.currentTarget) select(widget.id) } : undefined}
    >
      <div
        className={`h-full ${editable ? "pointer-events-none" : ""}`}
        inert={editable}
        data-hidden={hidden ? "true" : undefined}
        style={hidden ? { opacity: 0.45 } : undefined}
      >
        <WidgetBody widget={widget} fields={fields} />
      </div>
      <div className={`pointer-events-none absolute -inset-1 rounded-md border ${frame}`} />
      {conditions !== null && (
        <ShowWhenMark
          hidden={hidden}
          title={conditions}
          className={`absolute top-1 ${widget.type === "heading" && widget.align === "right" ? "left-1" : "right-1"} z-10 rounded p-0.5`}
          style={{ background: "var(--bg-elevated)", boxShadow: "0 0 0 1px var(--border)" }}
        />
      )}
      {problem !== null && (
        <span id={problemId} className="sr-only">
          {problem}
        </span>
      )}
      {editable && selected && HANDLES.map((handle) => <ResizeHandle key={handle} id={widget.id} handle={handle} />)}
    </div>
  )
})

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
