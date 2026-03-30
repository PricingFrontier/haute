import { useState } from "react"
import { withAlpha } from "../../../utils/color"

interface CategoricalValuePickerProps {
  availableValues: { value: string; count: number }[]
  existingValues: string[]
  onAddValue: (value: string) => void
  accentColor: string
}

export function CategoricalValuePicker({
  availableValues,
  existingValues,
  onAddValue,
  accentColor,
}: CategoricalValuePickerProps) {
  const [filter, setFilter] = useState("")

  if (availableValues.length === 0) {
    return (
      <div className="py-2 text-center text-[11px]" style={{ color: "var(--text-muted)" }}>
        Connect data to see values
      </div>
    )
  }

  const showSearch = availableValues.length > 10
  const existingSet = new Set(existingValues)

  const filtered = filter
    ? availableValues.filter((v) => v.value.toLowerCase().includes(filter.toLowerCase()))
    : availableValues

  return (
    <div className="space-y-1.5">
      <div className="text-[11px] font-medium" style={{ color: "var(--text-muted)" }}>
        Available values
      </div>

      {showSearch && (
        <input
          type="text"
          aria-label="Filter available values"
          placeholder="Filter values..."
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          className="w-full px-2 py-1 rounded text-[11px] font-mono focus:outline-none"
          style={{
            background: "var(--bg-surface)",
            border: "1px solid var(--border)",
            color: "var(--text-primary)",
          }}
        />
      )}

      <div
        className="flex flex-wrap gap-1"
        style={{ maxHeight: 120, overflowY: "auto" }}
      >
        {filtered.map((v) => {
          const isUsed = existingSet.has(v.value)
          return (
            <button
              key={v.value}
              disabled={isUsed}
              onClick={() => {
                if (!isUsed) onAddValue(v.value)
              }}
              className="px-2 py-0.5 rounded-full text-[11px] font-mono transition-colors"
              style={{
                background: isUsed ? withAlpha(accentColor, 0.08) : withAlpha(accentColor, 0.12),
                border: `1px solid ${isUsed ? withAlpha(accentColor, 0.2) : withAlpha(accentColor, 0.35)}`,
                color: isUsed ? accentColor : accentColor,
                opacity: isUsed ? 0.5 : 1,
                cursor: isUsed ? "default" : "pointer",
              }}
            >
              {v.value} ({v.count})
            </button>
          )
        })}
      </div>
    </div>
  )
}
