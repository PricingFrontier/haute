import { Plus } from "lucide-react"
import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react"

import { NODE_GROUP_COLORS } from "../../../theme/colors"
import { STEP_CATALOGUE, type StepKindInfo } from "./catalogue"
import type { StepKind } from "./types"

type AddableKind = Exclude<StepKind, "source">

const GROUPS: Array<{ title: string; kinds: AddableKind[] }> = [
  { title: "Rows", kinds: ["filter", "sort", "unique", "limit"] },
  { title: "Columns", kinds: ["with_column", "select", "drop", "rename", "cast", "fill_null"] },
  { title: "Combine", kinds: ["join", "concat", "group_by"] },
  { title: "Values", kinds: ["variable"] },
]

/**
 * The `Add step` button and its grouped popover. Arrow keys move between
 * items, Escape and outside clicks close it, and choosing an item hands the
 * kind back to the editor.
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
  const rootRef = useRef<HTMLDivElement>(null)
  const itemRefs = useRef<Array<HTMLButtonElement | null>>([])
  const items: StepKindInfo[] = GROUPS.flatMap((group) =>
    group.kinds.map((kind) => STEP_CATALOGUE.find((info) => info.kind === kind)).filter((info): info is StepKindInfo => info !== undefined),
  )

  useEffect(() => {
    if (!open) return
    const onDocumentClick = (event: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) setOpen(false)
    }
    document.addEventListener("mousedown", onDocumentClick)
    itemRefs.current[0]?.focus()
    return () => document.removeEventListener("mousedown", onDocumentClick)
  }, [open])

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (!open) return
    const focused = itemRefs.current.findIndex((el) => el === document.activeElement)
    if (event.key === "Escape") {
      event.preventDefault()
      setOpen(false)
    } else if (event.key === "ArrowDown") {
      event.preventDefault()
      itemRefs.current[(focused + 1) % items.length]?.focus()
    } else if (event.key === "ArrowUp") {
      event.preventDefault()
      itemRefs.current[(focused - 1 + items.length) % items.length]?.focus()
    }
  }

  return (
    <div ref={rootRef} className="relative shrink-0" onKeyDown={onKeyDown}>
      <button
        type="button"
        ref={buttonRef}
        disabled={disabled}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={menuId}
        onClick={() => setOpen((v) => !v)}
        className="focus-ring inline-flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs font-semibold disabled:opacity-50 disabled:cursor-not-allowed"
        style={{ color: "var(--text-on-accent)", background: NODE_GROUP_COLORS.transform }}
      >
        <Plus size={13} aria-hidden="true" />
        Add step
      </button>
      {open && (
        <div
          id={menuId}
          role="menu"
          aria-label="Add step"
          className="absolute right-0 z-20 mt-1 w-64 max-h-[28rem] overflow-y-auto rounded-lg p-1 shadow-lg"
          style={{ background: "var(--bg-elevated)", border: "1px solid var(--border-bright)" }}
        >
          {GROUPS.map((group) => (
            <div key={group.title} role="group" aria-label={group.title} className="py-1">
              <div className="px-2 pb-1 text-[10px] font-bold uppercase tracking-[0.08em]" style={{ color: "var(--text-muted)" }}>
                {group.title}
              </div>
              {group.kinds.map((kind) => {
                const info = STEP_CATALOGUE.find((entry) => entry.kind === kind)
                if (!info) return null
                const index = items.indexOf(info)
                return (
                  <button
                    key={kind}
                    type="button"
                    role="menuitem"
                    ref={(el) => {
                      itemRefs.current[index] = el
                    }}
                    onClick={() => {
                      setOpen(false)
                      onAdd(kind)
                    }}
                    className="focus-ring w-full text-left rounded-md px-2 py-1.5 hover:bg-[var(--chrome-hover)]"
                  >
                    <div className="text-xs font-medium" style={{ color: "var(--text-primary)" }}>
                      {info.label}
                    </div>
                    <div className="text-[10px]" style={{ color: "var(--text-muted)" }}>
                      {info.description}
                    </div>
                  </button>
                )
              })}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
