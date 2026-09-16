import { Plus, X } from "lucide-react"
import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react"

import { STEP_CATALOGUE, type StepKindInfo } from "./catalogue"
import type { StepKind } from "./types"

type AddableKind = Exclude<StepKind, "source">

/** Chooser sections, in reading order. */
const GROUPS: Array<{ title: string; kinds: AddableKind[] }> = [
  { title: "Rows", kinds: ["filter", "sort", "unique", "limit"] },
  { title: "Columns", kinds: ["with_column", "select", "drop", "rename", "cast", "fill_null"] },
  { title: "Combine", kinds: ["join", "concat", "group_by", "pivot", "unpivot"] },
  { title: "Values", kinds: ["variable"] },
]

/**
 * The `Add step` control that sits under the last step: a full-width button
 * that opens, in place, a chooser of every step kind by name, grouped as
 * rows, columns, combine and values (what a kind does is its tooltip). Arrow keys move between kinds, Escape closes and
 * returns focus to the button, and choosing a kind hands it back to the editor.
 */
export default function AddStepMenu({
  onAdd,
  disabled = false,
  buttonRef,
}: {
  onAdd: (kind: AddableKind) => void
  disabled?: boolean
  buttonRef?: (element: HTMLButtonElement | null) => void
}) {
  const [open, setOpen] = useState(false)
  const menuId = useId()
  const button = useRef<HTMLButtonElement | null>(null)
  const itemRefs = useRef<Array<HTMLButtonElement | null>>([])
  const wasOpen = useRef(false)
  const sections = GROUPS.map((group) => ({
    title: group.title,
    items: group.kinds
      .map((kind) => STEP_CATALOGUE.find((info) => info.kind === kind))
      .filter((info): info is StepKindInfo => info !== undefined),
  }))
  const items: StepKindInfo[] = sections.flatMap((section) => section.items)

  useEffect(() => {
    if (open) itemRefs.current[0]?.focus()
    else if (wasOpen.current) button.current?.focus()
    wasOpen.current = open
  }, [open])

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (!open) return
    const focused = itemRefs.current.findIndex((el) => el === document.activeElement)
    if (event.key === "Escape") {
      event.preventDefault()
      setOpen(false)
    } else if (event.key === "ArrowDown" || event.key === "ArrowRight") {
      event.preventDefault()
      itemRefs.current[(focused + 1) % items.length]?.focus()
    } else if (event.key === "ArrowUp" || event.key === "ArrowLeft") {
      event.preventDefault()
      itemRefs.current[(focused - 1 + items.length) % items.length]?.focus()
    }
  }

  return (
    <div className="shrink-0" onKeyDown={onKeyDown}>
      {!open ? (
        <button
          type="button"
          ref={(el) => {
            button.current = el
            buttonRef?.(el)
          }}
          disabled={disabled}
          aria-haspopup="menu"
          aria-expanded={false}
          aria-controls={menuId}
          onClick={() => setOpen(true)}
          className="add-row-btn focus-ring flex w-full items-center justify-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-medium disabled:opacity-50 disabled:cursor-not-allowed"
          style={{ color: "var(--text-secondary)", border: "1px dashed var(--border)" }}
        >
          <Plus size={13} aria-hidden="true" />
          Add step
        </button>
      ) : (
        <div
          id={menuId}
          role="menu"
          aria-label="Add step"
          className="grid gap-2.5 rounded-lg p-2.5"
          style={{ background: "var(--bg-input)", border: "1px solid var(--border)" }}
        >
          <div className="flex items-center justify-between">
            <span className="text-[10px] font-bold uppercase tracking-[0.08em]" style={{ color: "var(--text-muted)" }}>
              Add a step
            </span>
            <button type="button" onClick={() => setOpen(false)} aria-label="Close" className="icon-danger-btn focus-ring rounded p-0.5">
              <X size={12} aria-hidden="true" />
            </button>
          </div>
          {sections.map((section) => (
            <div key={section.title} role="group" aria-label={section.title} className="grid gap-1">
              <div className="flex items-center gap-2">
                <span className="text-[10px] font-semibold uppercase tracking-[0.08em]" style={{ color: "var(--text-muted)" }}>
                  {section.title}
                </span>
                <span aria-hidden="true" className="h-px flex-1" style={{ background: "var(--border-subtle)" }} />
              </div>
              <div className="flex flex-wrap gap-1.5">
                {section.items.map((info) => {
                  const index = items.indexOf(info)
                  return (
                    <button
                      key={info.kind}
                      type="button"
                      role="menuitem"
                      title={info.description}
                      ref={(el) => {
                        itemRefs.current[index] = el
                      }}
                      onClick={() => {
                        setOpen(false)
                        onAdd(info.kind)
                      }}
                      className="add-row-btn focus-ring rounded-full px-2.5 py-1 text-xs font-medium"
                      style={{ color: "var(--text-primary)", background: "var(--bg-elevated)", border: "1px solid var(--border)" }}
                    >
                      {info.label}
                    </button>
                  )
                })}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
