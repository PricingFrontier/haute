/** A component's Order: its fields and a Collection's spacers, moved by dragging or Alt+arrows (specs/workbench). */
import { GripVertical, SeparatorHorizontal, X } from "lucide-react"
import type { KeyboardEvent, ReactNode } from "react"
import type { FieldRef, FormSpec } from "../../api/types"
import { QuietAction } from "../../components/form"
import useListReorder, { type RowDrag } from "../../hooks/useListReorder"
import useRefocusAfterMove, { altArrowMove, type RefocusAfterMove } from "../../hooks/useRefocusAfterMove"
import useWorkbenchFormStore from "../../stores/useWorkbenchFormStore"
import { addSpacer, fieldColumn, moveInOrder, orderItems, readableName, removeSpacer, toggleField, type FieldsWidget } from "../../utils/workbenchForm"
import { COLUMN_TYPES } from "../columnTypes"

const fieldKey = (field: FieldRef) => `${field.table}:${field.column}`

/**
 * The fields a component shows, in order, with a Collection's spacers among them and Add
 * spacer under them: drag a row, or Alt+Up and Alt+Down on it, to move it.
 */
export function FieldOrder({ form, widget }: { form: FormSpec; widget: FieldsWidget }) {
  const change = useWorkbenchFormStore((s) => s.change)
  const dragFor = useListReorder((from, to) => change((spec) => moveInOrder(spec, widget.id, from, to)))
  // A row moved by Alt+arrows takes the focus again in its new place.
  const refocus = useRefocusAfterMove<HTMLDivElement>()
  const items = orderItems(widget)
  // A field is numbered among the fields, and a spacer keyed by its count among the spacers.
  const rows: ReactNode[] = []
  let fields = 0
  let spacers = 0
  items.forEach((item, place) => {
    const rowKey = item.kind === "field" ? fieldKey(item.field) : `spacer-${spacers}`
    const placed = { widget, place, count: items.length, drag: dragFor(place), rowKey, refocus }
    if (item.kind === "field") {
      fields += 1
      rows.push(<OrderRow key={rowKey} {...placed} form={form} field={item.field} number={fields} />)
    } else {
      rows.push(<SpacerRow key={rowKey} {...placed} />)
      spacers += 1
    }
  })
  return (
    <>
      <div role="list" className="flex flex-col gap-0.5 rounded-md p-1" style={{ border: "1px solid var(--border)", background: "var(--bg-elevated)" }}>
        {rows}
      </div>
      {widget.type === "collection" && (
        <QuietAction onClick={() => change((spec) => addSpacer(spec, widget.id))} className="self-start">
          Add spacer
        </QuietAction>
      )}
    </>
  )
}

/**
 * Where a row of the order sits: its component, its place among `count` rows, its part in
 * dragging them, and its key in the list, which a keyboard move focuses again (`refocus`).
 */
interface OrderPlace {
  widget: FieldsWidget
  place: number
  count: number
  drag: RowDrag
  rowKey: string
  refocus: RefocusAfterMove<HTMLDivElement>
}

/** A row of the order, named `label`: picked up by its grip, or moved by Alt+Up and Alt+Down. */
function OrderRowFrame({ widget, place, count, drag, rowKey, refocus, label, children }: OrderPlace & { label: string; children: ReactNode }) {
  const change = useWorkbenchFormStore((s) => s.change)
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const to = altArrowMove(event, "vertical", place, count)
    if (to === null) return
    refocus.moved(rowKey)
    change((spec) => moveInOrder(spec, widget.id, place, to))
  }
  return (
    <div
      ref={refocus.register(rowKey)}
      tabIndex={0}
      role="listitem"
      aria-label={label}
      aria-keyshortcuts="Alt+ArrowUp Alt+ArrowDown"
      draggable
      onDragStart={drag.onStart}
      onDragOver={drag.onOver}
      onDrop={drag.onDrop}
      onDragEnd={drag.onEnd}
      onKeyDown={onKeyDown}
      data-drop-target={drag.target ? "true" : undefined}
      className="group focus-ring flex cursor-grab items-center gap-1.5 rounded px-1 py-0.5 hover-bg active:cursor-grabbing"
      style={{ opacity: drag.dragging ? 0.5 : 1, boxShadow: drag.target ? "0 -2px 0 0 var(--accent)" : undefined }}
    >
      <GripVertical size={12} aria-hidden="true" className="shrink-0" style={{ color: "var(--text-muted)" }} />
      {children}
    </div>
  )
}

/** A Collection's spacer in its order, with a cross that removes it. */
function SpacerRow(placed: OrderPlace) {
  const change = useWorkbenchFormStore((s) => s.change)
  return (
    <OrderRowFrame {...placed} label="Spacer">
      <span className="w-4 shrink-0" aria-hidden="true" />
      <span className="flex size-4 shrink-0 items-center justify-center" style={{ color: "var(--text-muted)" }}>
        <SeparatorHorizontal size={12} aria-hidden="true" />
      </span>
      <span className="min-w-0 flex-1 truncate" style={{ color: "var(--text-muted)" }}>
        Spacer
      </span>
      <button
        type="button"
        onClick={() => change((spec) => removeSpacer(spec, placed.widget.id, placed.place))}
        aria-label="Remove spacer"
        title="Remove it"
        className="icon-danger-btn focus-ring grid size-5 shrink-0 place-items-center rounded opacity-0 group-hover:opacity-100 focus-visible:opacity-100"
      >
        <X size={12} aria-hidden="true" />
      </button>
    </OrderRowFrame>
  )
}

function OrderRow({ form, field, number, ...placed }: OrderPlace & { form: FormSpec; field: FieldRef; number: number }) {
  const change = useWorkbenchFormStore((s) => s.change)
  const { widget } = placed
  const found = fieldColumn(form, field)
  const label = found ? found.column.label || readableName(found.column.name) : "Missing column"
  const type = found ? COLUMN_TYPES[found.column.type] : null
  return (
    <OrderRowFrame {...placed} label={`${number}. ${label}`}>
      <span className="w-4 shrink-0 text-right tabular-nums" style={{ color: "var(--text-muted)" }}>
        {number}
      </span>
      {type && (
        <span title={type.label} className={`flex size-4 shrink-0 items-center justify-center ${type.color}`}>
          <type.icon size={12} aria-hidden="true" />
        </span>
      )}
      <span className="min-w-0 flex-1 truncate" style={{ color: found ? "var(--text-primary)" : "var(--warning)" }}>
        {label}
      </span>
      {found && (
        <span className="shrink-0 truncate font-mono" style={{ color: "var(--text-muted)" }}>
          {found.table.name}
        </span>
      )}
      <button
        type="button"
        onClick={() => change((spec) => toggleField(spec, widget.id, field))}
        aria-label={`Stop showing ${label}`}
        title="Stop showing it"
        className="icon-danger-btn focus-ring grid size-5 shrink-0 place-items-center rounded opacity-0 group-hover:opacity-100 focus-visible:opacity-100"
      >
        <X size={12} aria-hidden="true" />
      </button>
    </OrderRowFrame>
  )
}
