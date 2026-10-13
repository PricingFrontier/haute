import { useState } from "react"
import { COLOUR_WHEEL_GRADIENT, PIVOT_CHART_COLORS } from "../../theme/colors"

/**
 * A colour chosen as Explore's charts choose one: Automatic (null), one of the swatches
 * (Haute's chart hues unless the caller gives its own), or a colour of its own from the
 * picker behind a colour wheel's rainbow square, outlined while the colour is one no swatch
 * holds. `name` says what the colour is for, in each control's label. Given `onPreview`, each
 * step of a colour being picked is handed up as it is made, so what it colours can show it,
 * and the pick is committed as the picker is left.
 */
export default function ColourSwatches({
  name,
  value,
  onCommit,
  onPreview,
  swatches = PIVOT_CHART_COLORS.defaultSeries,
}: {
  name: string
  value: string | null
  onCommit: (value: string | null) => void
  onPreview?: (value: string) => void
  swatches?: readonly string[]
}) {
  const swatch = "h-5 w-5 rounded border"
  return (
    <div className="flex flex-wrap items-center gap-1">
      <button
        type="button"
        aria-label={`Automatic colour for ${name}`}
        aria-pressed={value === null}
        onClick={() => onCommit(null)}
        className="rounded px-1.5 py-0.5 text-[10px] font-semibold"
        style={{
          border: `1px solid ${value === null ? "var(--text-secondary)" : "var(--border)"}`,
          color: "var(--text-secondary)",
        }}
      >
        Automatic
      </button>
      {swatches.map((hex) => (
        <button
          key={hex}
          type="button"
          aria-label={`Colour ${hex} for ${name}`}
          aria-pressed={value === hex}
          onClick={() => onCommit(hex)}
          className={swatch}
          style={{
            background: hex,
            borderColor: value === hex ? "var(--text-primary)" : "var(--border)",
          }}
        />
      ))}
      {/* Remounted as a committed value lands, dropping a stale draft; while each step is
          previewed the value moves with the pick, and a remount would close the picker. */}
      <CustomColourInput
        key={onPreview === undefined ? (value ?? "automatic") : "previewed"}
        name={name}
        value={value}
        own={value !== null && !swatches.includes(value)}
        onCommit={onCommit}
        onPreview={onPreview}
      />
    </div>
  )
}

function CustomColourInput({
  name,
  value,
  own,
  onCommit,
  onPreview,
}: {
  name: string
  value: string | null
  /** The colour is one no swatch holds: the square is outlined as a chosen swatch is. */
  own: boolean
  onCommit: (value: string) => void
  onPreview?: (value: string) => void
}) {
  // The native picker fires an input event per drag tick; committing each
  // tick would persist one edit (and one undo step) per tick. Track a local
  // draft, hand each tick up to be previewed when asked to, and commit once on
  // blur, like the other committed controls.
  const [draft, setDraft] = useState<string | null>(null)
  // The picker itself, unseen over a colour wheel's rainbow: a press on the square opens it.
  return (
    <span
      className="relative inline-flex h-5 w-7 rounded border focus-within:ring-1 focus-within:ring-[var(--accent)]"
      style={{ background: COLOUR_WHEEL_GRADIENT, borderColor: own ? "var(--text-primary)" : "var(--border)" }}
    >
      <input
        type="color"
        aria-label={`Custom colour for ${name}`}
        className="absolute inset-0 h-full w-full cursor-pointer opacity-0"
        value={draft ?? value ?? PIVOT_CHART_COLORS.defaultSeries[0]}
        onChange={(event) => {
          const picked = event.target.value.toUpperCase()
          setDraft(picked)
          onPreview?.(picked)
        }}
        onBlur={() => {
          // A previewed pick already shows, so it is committed even as the value it now holds.
          if (draft !== null && (onPreview !== undefined || draft !== value)) onCommit(draft)
          setDraft(null)
        }}
      />
    </span>
  )
}
