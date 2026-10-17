/** A colour of the sheets', or of a component's, chosen with the swatches and shown as it is picked (specs/workbench). */
import { useRef } from "react"
import type { FormSpec } from "../../api/types"
import { ColourSwatches } from "../../components/form"
import useWorkbenchFormStore from "../../stores/useWorkbenchFormStore"
import { NEUTRAL_SWATCHES, PIVOT_CHART_COLORS } from "../../theme/colors"

/** The swatches each colour offers: white, light grey, dark grey and black, then Haute's hues. */
const SHEET_SWATCHES: readonly string[] = [...NEUTRAL_SWATCHES, ...PIVOT_CHART_COLORS.defaultSeries]

/**
 * One colour: its label above Automatic, the swatches and a colour of its own, or, `beside`,
 * in the label column of the panel's rows. The sheet shows a colour of its own at each step
 * of its pick, a gesture of the form store's made from the form as the pick found it, so
 * the whole pick is one undo step; a pick an undo, a redo or another edit finishes first is
 * not committed again as the picker is left; a swatch or Automatic is one at once. `name` says what
 * it colours in each control's label, its label while not given.
 */
export function ColourRow({
  label,
  name = label,
  value,
  update,
  beside = false,
}: {
  label: string
  name?: string
  value: string | null
  update: (spec: FormSpec, colour: string | null) => FormSpec
  beside?: boolean
}) {
  const change = useWorkbenchFormStore((s) => s.change)
  // The form as the pick found it, while a colour of its own is being picked.
  const picking = useRef<FormSpec | null>(null)
  // A pick another edit, an undo or a redo finished first: what it held is in the history
  // already, so leaving the picker commits nothing over what came after.
  const stopped = useRef(false)
  const preview = (colour: string) => {
    const store = useWorkbenchFormStore.getState()
    if (picking.current === null) {
      if (store.form === null) return
      picking.current = store.form
      stopped.current = false
      store.beginGesture(store.form, () => {
        if (picking.current !== null) stopped.current = true
        picking.current = null
      })
    }
    store.setFormRaw(update(picking.current, colour))
  }
  const commit = (colour: string | null) => {
    const store = useWorkbenchFormStore.getState()
    const before = picking.current
    if (before === null) {
      if (stopped.current) stopped.current = false
      else change((spec) => update(spec, colour))
      return
    }
    // Ended here, not stopped by another edit.
    picking.current = null
    store.setFormRaw(update(before, colour))
    store.endGesture()
  }
  return (
    <div className={beside ? "flex items-start gap-2" : "flex flex-col gap-1"}>
      <span className={beside ? "w-14 shrink-0 pt-0.5" : undefined} style={{ color: "var(--text-muted)" }}>
        {label}
      </span>
      <ColourSwatches name={name} value={value} onCommit={commit} onPreview={preview} swatches={SHEET_SWATCHES} />
    </div>
  )
}
