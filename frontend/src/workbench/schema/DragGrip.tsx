/** The grip a schema table or column is dragged by (specs/workbench). */
import { GripVertical } from "lucide-react"
import { type RefObject } from "react"
import type { RowDrag } from "../../hooks/useListReorder"
import { HOVER_ONLY } from "./schemaText"

/**
 * The grip that picks up a table or a column, showing `image` (its whole block or row) as it
 * is dragged; a space as wide while it cannot move (`drag` undefined), so names line up.
 * Only the grip is draggable, so selecting a name's text never starts a drag.
 */
export function DragGrip({ drag, image }: { drag: RowDrag | undefined; image: RefObject<HTMLElement | null> }) {
  if (drag === undefined) return <span className="w-3 shrink-0" aria-hidden="true" />
  return (
    <span
      draggable
      onDragStart={(event) => {
        drag.onStart(event)
        if (event.dataTransfer && image.current) event.dataTransfer.setDragImage(image.current, 8, 8)
      }}
      onDragEnd={drag.onEnd}
      title="Drag to move"
      aria-hidden="true"
      className={`flex w-3 shrink-0 cursor-grab items-center self-center active:cursor-grabbing ${HOVER_ONLY}`}
      style={{ color: "var(--text-muted)" }}
    >
      <GripVertical size={12} />
    </span>
  )
}
