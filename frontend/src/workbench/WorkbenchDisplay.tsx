/**
 * Display in the workbench's toolbar while building (specs/workbench), and the box it opens
 * under it, as the pipeline toolbar's Source dropdown opens one, rather than a dialog over
 * the sheet: the sheets' colours, the form's `theme`, each Automatic (the editor's own), one
 * of the swatches, the neutrals then Haute's hues, or a colour of its own from the rainbow
 * square. A colour of its own shows on the sheet at each
 * step of its pick, and the whole pick is one undo step. Pressing outside the box, or
 * Escape, closes it, Escape giving the focus back to Display. While open the box holds the
 * focus and is modal to the keyboard: a key pressed in it, Delete on a swatch among them,
 * never reaches the sheet's shortcuts.
 */
import { SlidersHorizontal } from "lucide-react"
import { useCallback, useEffect, useRef, useState, type KeyboardEvent } from "react"
import type { FormSpec } from "../api/types"
import { ColourSwatches, EditorLabel } from "../components/form"
import { ToolbarButton } from "../haute-ui"
import useWorkbenchFormStore from "../stores/useWorkbenchFormStore"
import { NEUTRAL_SWATCHES, PIVOT_CHART_COLORS } from "../theme/colors"
import { BLANK_THEME, setThemeColour, type ThemeColour } from "../utils/workbenchForm"

/** The swatches each colour offers: white, light grey, dark grey and black, then Haute's hues. */
const SHEET_SWATCHES: readonly string[] = [...NEUTRAL_SWATCHES, ...PIVOT_CHART_COLORS.defaultSeries]

/** The sheets' colours, in the box's order, each by what it colours. */
const THEME_COLOURS: ReadonlyArray<{ key: ThemeColour; label: string }> = [
  { key: "sheet_color", label: "Sheet background" },
  { key: "background_color", label: "Component background" },
  { key: "border_color", label: "Borders" },
  { key: "font_color", label: "Font" },
  { key: "input_cell_color", label: "Input cells" },
  { key: "output_cell_color", label: "Output cells" },
]

export default function WorkbenchDisplay() {
  const ready = useWorkbenchFormStore((s) => s.status === "ready")
  const theme = useWorkbenchFormStore((s) => s.form?.theme ?? BLANK_THEME)
  const [open, setOpen] = useState(false)
  const box = useRef<HTMLDivElement>(null)
  const dialog = useRef<HTMLDivElement>(null)
  // The box takes the focus as it opens.
  useEffect(() => {
    if (open) dialog.current?.focus()
  }, [open])
  // A colour being picked commits as its picker is left: left first, before the box goes.
  const close = useCallback(() => {
    const focused = document.activeElement
    if (focused instanceof HTMLElement && box.current?.contains(focused)) focused.blur()
    setOpen(false)
  }, [])
  // A press outside the box closes it, a component's too, whose press cancels the mouse
  // events after it: the press is heard as it begins, before anything on the sheet stops it.
  useEffect(() => {
    if (!open) return
    const onPress = (event: PointerEvent) => {
      if (box.current !== null && event.target instanceof Node && !box.current.contains(event.target)) close()
    }
    document.addEventListener("pointerdown", onPress, true)
    return () => document.removeEventListener("pointerdown", onPress, true)
  }, [open, close])
  const onKeyDown = (event: KeyboardEvent) => {
    if (event.key !== "Escape" || !open) return
    // Not the sheet's own Escape, which would deselect.
    event.preventDefault()
    close()
    // Back to Display, the box's first child, as the control in the box goes with it.
    box.current?.querySelector<HTMLButtonElement>(":scope > button")?.focus()
  }

  return (
    <div ref={box} className="relative" onKeyDown={onKeyDown}>
      <ToolbarButton
        icon={SlidersHorizontal}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-pressed={open}
        onClick={() => (open ? close() : setOpen(true))}
        disabled={!ready}
        title="The sheets' colours"
      >
        Display
      </ToolbarButton>
      {open && (
        <div
          ref={dialog}
          role="dialog"
          aria-modal="true"
          aria-label="Display"
          tabIndex={-1}
          data-testid="workbench-display"
          className="absolute top-full left-0 z-50 mt-1 flex w-[26rem] flex-col gap-3 rounded-lg p-3 text-xs shadow-2xl"
          style={{ background: "var(--bg-panel)", border: "1px solid var(--border)" }}
        >
          <EditorLabel as="div">Colours</EditorLabel>
          {THEME_COLOURS.map(({ key, label }) => (
            <ColourRow key={key} label={label} value={theme[key]} update={(spec, colour) => setThemeColour(spec, key, colour)} />
          ))}
        </div>
      )}
    </div>
  )
}

/**
 * One of the sheets' colours: its label above Automatic, the swatches and a colour of its
 * own. The sheet shows a colour of its own at each step of its pick, a gesture of the form
 * store's made from the form as the pick found it, so the whole pick is one undo step; a
 * swatch or Automatic is one at once.
 */
function ColourRow({
  label,
  value,
  update,
}: {
  label: string
  value: string | null
  update: (spec: FormSpec, colour: string | null) => FormSpec
}) {
  const change = useWorkbenchFormStore((s) => s.change)
  // The form as the pick found it, while a colour of its own is being picked.
  const picking = useRef<FormSpec | null>(null)
  const preview = (colour: string) => {
    const store = useWorkbenchFormStore.getState()
    if (picking.current === null) {
      if (store.form === null) return
      picking.current = store.form
      store.beginGesture(store.form, () => {
        picking.current = null
      })
    }
    store.setFormRaw(update(picking.current, colour))
  }
  const commit = (colour: string | null) => {
    const store = useWorkbenchFormStore.getState()
    if (picking.current === null) {
      change((spec) => update(spec, colour))
      return
    }
    store.setFormRaw(update(picking.current, colour))
    store.endGesture()
  }
  return (
    <div className="flex flex-col gap-1">
      <span style={{ color: "var(--text-muted)" }}>{label}</span>
      <ColourSwatches name={label} value={value} onCommit={commit} onPreview={preview} swatches={SHEET_SWATCHES} />
    </div>
  )
}
