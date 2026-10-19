/** The menu a right-click on a grid's row opens, and the box asking how many rows to insert (specs/workbench). */
import { ChevronRight, Plus, Trash2 } from "lucide-react"
import { useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent, type MouseEvent } from "react"
import { createPortal } from "react-dom"
import { ModalFormActions, ModalFormHeader, ModalTextInput } from "../../components/ModalForm"
import ModalShell from "../../components/ModalShell"
import { typeHere } from "./fieldCells"

/** Where rows are inserted: before the row the menu is for, or after it. */
export type Side = "above" | "below"

/** A grid's words for its rows as drawn: a transposed grid's rows are its columns. */
const WORDS = {
  rows: { one: "Row", many: "Rows", above: "Above", below: "Below" },
  columns: { one: "Column", many: "Columns", above: "Left", below: "Right" },
} as const

/** How far the menu keeps from the window's edges. */
const EDGE = 4

// The entry with the focus shows it as one under the pointer does, by its fill.
const ITEM = "hover-chrome flex w-full items-center gap-2.5 px-3 py-1.5 text-left text-[12px] whitespace-nowrap outline-none"
const PANEL = { background: "var(--bg-panel)", border: "1px solid var(--border-bright)" }

const noMenu = (event: MouseEvent) => event.preventDefault()

/**
 * A grid row's menu at the pointer, kept inside the window: Insert, a row or as many as
 * typed above or below it, and Delete Row; transposed, columns left or right of it. It is
 * drawn on the document's body, as the sheet's zoom would scale and place it, and closes
 * on a press outside it, a scroll, the window's resize or blur, or Escape, which gives
 * the focus back to what held it. A Multiple entry swaps it for a box asking how many.
 */
export function RowMenu({
  row,
  x,
  y,
  transposed,
  onInsert,
  onDelete,
  onClose,
}: {
  row: number
  x: number
  y: number
  transposed: boolean
  onInsert: (side: Side, count: number) => void
  onDelete: () => void
  onClose: () => void
}) {
  const words = transposed ? WORDS.columns : WORDS.rows
  const [inserting, setInserting] = useState(false)
  const [asking, setAsking] = useState<Side | null>(null)
  const [place, setPlace] = useState<{ left: number; top: number } | null>(null)
  const [flip, setFlip] = useState({ left: false, up: false })
  // What held the focus as it opened, a cell's field mostly.
  const [before] = useState(() => document.activeElement)
  const menu = useRef<HTMLDivElement>(null)
  const insert = useRef<HTMLButtonElement>(null)
  const submenu = useRef<HTMLDivElement>(null)
  const items = useRef<(HTMLButtonElement | null)[]>([])
  const subitems = useRef<(HTMLButtonElement | null)[]>([])
  // Opened by a click or a key, the submenu takes the focus; by a hover, it leaves it.
  const focusSubmenu = useRef(false)

  useLayoutEffect(() => {
    if (menu.current === null) return
    const box = menu.current.getBoundingClientRect()
    setPlace({
      left: Math.max(EDGE, Math.min(x, window.innerWidth - EDGE - box.width)),
      top: Math.max(EDGE, Math.min(y, window.innerHeight - EDGE - box.height)),
    })
  }, [x, y])

  // Hidden until placed, it takes the focus once it shows.
  const placed = place !== null
  useEffect(() => {
    if (placed) items.current[0]?.focus({ preventScroll: true })
  }, [placed])

  // The submenu opens to the right of Insert and down, unless the window ends first.
  useLayoutEffect(() => {
    if (!inserting || insert.current === null || submenu.current === null) return
    const item = insert.current.getBoundingClientRect()
    const box = submenu.current.getBoundingClientRect()
    setFlip({ left: item.right + box.width > window.innerWidth - EDGE, up: item.top + box.height > window.innerHeight - EDGE })
  }, [inserting])

  useEffect(() => {
    if (!inserting || !focusSubmenu.current) return
    focusSubmenu.current = false
    subitems.current[0]?.focus({ preventScroll: true })
  }, [inserting])

  useEffect(() => {
    if (asking !== null) return
    const outside = (event: Event) => {
      if (!(event.target instanceof Node && menu.current?.contains(event.target))) onClose()
    }
    const away = () => onClose()
    document.addEventListener("pointerdown", outside, true)
    window.addEventListener("scroll", outside, true)
    window.addEventListener("resize", away)
    window.addEventListener("blur", away)
    return () => {
      document.removeEventListener("pointerdown", outside, true)
      window.removeEventListener("scroll", outside, true)
      window.removeEventListener("resize", away)
      window.removeEventListener("blur", away)
    }
  }, [asking, onClose])

  const openSubmenu = () => {
    if (inserting) subitems.current[0]?.focus({ preventScroll: true })
    else {
      focusSubmenu.current = true
      setInserting(true)
    }
  }
  const closeSubmenu = () => {
    setInserting(false)
    insert.current?.focus({ preventScroll: true })
  }
  const choose = (side: Side, count: number | null) => {
    if (count === null) setAsking(side)
    else {
      onInsert(side, count)
      onClose()
    }
  }

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const inSubmenu = subitems.current.some((item) => item === document.activeElement)
    const list = (inSubmenu ? subitems : items).current
    const at = list.findIndex((item) => item === document.activeElement)
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      const step = event.key === "ArrowDown" ? 1 : -1
      list[at === -1 ? (step === 1 ? 0 : list.length - 1) : (at + step + list.length) % list.length]?.focus({ preventScroll: true })
    } else if (event.key === "ArrowRight" && !inSubmenu && at === 0) openSubmenu()
    else if ((event.key === "ArrowLeft" || event.key === "Escape") && inSubmenu) closeSubmenu()
    else if (event.key === "Escape" || event.key === "Tab") {
      onClose()
      if (before instanceof HTMLElement) before.focus({ preventScroll: true })
    } else return
    event.preventDefault()
    event.stopPropagation()
  }

  if (asking !== null) {
    const side = words[asking]
    return (
      <CountBox
        title={`Insert ${words.many} ${side}`}
        label={words.many}
        onInsert={(count) => {
          onInsert(asking, count)
          onClose()
        }}
        onCancel={onClose}
      />
    )
  }

  const entries: { side: Side; count: number | null; label: string }[] = [
    { side: "above", count: 1, label: `Insert ${words.one} ${words.above}` },
    { side: "below", count: 1, label: `Insert ${words.one} ${words.below}` },
    { side: "above", count: null, label: `Insert Multiple ${words.many} ${words.above}` },
    { side: "below", count: null, label: `Insert Multiple ${words.many} ${words.below}` },
  ]
  return createPortal(
    <div
      ref={menu}
      role="menu"
      aria-label={`${words.one} ${row + 1}`}
      className="fixed z-50 min-w-[160px] rounded-lg py-1 shadow-2xl"
      style={{ ...PANEL, left: place?.left ?? x, top: place?.top ?? y, visibility: placed ? undefined : "hidden" }}
      onKeyDown={onKeyDown}
      onPointerDown={typeHere}
      onContextMenu={noMenu}
    >
      <div role="none" className="relative" onPointerEnter={() => setInserting(true)}>
        <button
          ref={(element) => {
            insert.current = element
            items.current[0] = element
          }}
          type="button"
          role="menuitem"
          aria-haspopup="menu"
          aria-expanded={inserting}
          tabIndex={-1}
          onClick={openSubmenu}
          className={ITEM}
        >
          <Plus size={13} aria-hidden="true" />
          <span className="flex-1">Insert</span>
          <ChevronRight size={13} aria-hidden="true" />
        </button>
        {inserting && (
          <div
            ref={submenu}
            role="menu"
            aria-label="Insert"
            className={`absolute ${flip.left ? "right-full" : "left-full"} ${flip.up ? "bottom-0" : "top-0"} rounded-lg py-1 shadow-2xl`}
            style={PANEL}
          >
            {entries.map((entry, index) => (
              <button
                key={entry.label}
                ref={(element) => {
                  subitems.current[index] = element
                }}
                type="button"
                role="menuitem"
                tabIndex={-1}
                onClick={() => choose(entry.side, entry.count)}
                className={ITEM}
              >
                {entry.label}
              </button>
            ))}
          </div>
        )}
      </div>
      <button
        ref={(element) => {
          items.current[1] = element
        }}
        type="button"
        role="menuitem"
        tabIndex={-1}
        onPointerEnter={() => setInserting(false)}
        onClick={() => {
          onDelete()
          onClose()
        }}
        className={`${ITEM} menu-item--danger`}
      >
        <Trash2 size={13} aria-hidden="true" />
        Delete {words.one}
      </button>
    </div>,
    document.body,
  )
}

/** The box asking how many rows to insert: a whole number from 1, Insert disabled until it holds one. */
function CountBox({ title, label, onInsert, onCancel }: { title: string; label: string; onInsert: (count: number) => void; onCancel: () => void }) {
  const [text, setText] = useState("")
  const input = useRef<HTMLInputElement>(null)
  const count = /^\d+$/.test(text) ? Number(text) : 0
  // After the dialog's own focus, which goes to its frame.
  useEffect(() => {
    input.current?.focus()
  }, [])
  return createPortal(
    <div className="contents" onPointerDown={typeHere}>
      <ModalShell ariaLabel={title} onClose={onCancel} width="w-[280px]">
        <ModalFormHeader title={title} />
        <form
          className="flex flex-col gap-3 p-4"
          onSubmit={(event) => {
            event.preventDefault()
            if (count >= 1) onInsert(count)
          }}
        >
          <label className="flex flex-col gap-1 text-[11px] font-medium" style={{ color: "var(--text-muted)" }}>
            {label}
            <ModalTextInput ref={input} type="number" min={1} step={1} value={text} onChange={(event) => setText(event.target.value)} />
          </label>
          <ModalFormActions onCancel={onCancel} submitLabel="Insert" busyLabel="Inserting…" busy={false} disabled={count < 1} submitTestId="row-count-insert" />
        </form>
      </ModalShell>
    </div>,
    document.body,
  )
}
