/**
 * Shared feature browser sidebar for AvE and PDP tabs.
 *
 * Displays a searchable, scrollable list of features sorted by importance,
 * with small importance bars. Click to select a feature.
 */
import { useState, useMemo } from "react"
import { Search } from "lucide-react"
import { MODEL_COLORS } from "../../theme/colors"

export type FeatureItem = {
  feature: string
  importance: number
}

export interface FeatureBrowserProps {
  features: FeatureItem[]
  selected: string | null
  onSelect: (feature: string) => void
  width?: number
  search?: string
  onSearch?: (search: string) => void
}

export function FeatureBrowser({
  features,
  selected,
  onSelect,
  width,
  search: controlledSearch,
  onSearch,
}: FeatureBrowserProps) {
  const [localSearch, setLocalSearch] = useState("")
  const search = controlledSearch ?? localSearch
  const setSearch = onSearch ?? setLocalSearch

  const filtered = useMemo(() => {
    if (!search) return features
    const q = search.toLowerCase()
    return features.filter((f) => f.feature.toLowerCase().includes(q))
  }, [features, search])

  const maxImportance =
    features.length > 0
      ? features.map((f) => Math.abs(f.importance)).reduce((a, b) => Math.max(a, b), -Infinity)
      : 1

  return (
    <div className="validation-feature-browser" style={width === undefined ? undefined : { width }}>
      {/* Search */}
      <div className="mb-3">
        <div
          className="flex items-center gap-2 px-2 py-2 rounded"
          style={{ background: "var(--bg-input)", border: "1px solid var(--border)" }}
        >
          <Search size={14} aria-hidden="true" style={{ color: "var(--text-muted)" }} />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search features..."
            aria-label="Search features"
            className="bg-transparent border-none focus-ring text-[13px] min-w-0 w-full"
            style={{ color: "var(--text-primary)" }}
          />
        </div>
      </div>

      {/* Feature list */}
      <div className="validation-feature-list">
        {filtered.length === 0 && (
          <div className="px-2 py-3 text-xs text-center" style={{ color: "var(--text-muted)" }}>
            No features found
          </div>
        )}
        {filtered.map((f) => {
          const isSelected = f.feature === selected
          const barWidth = maxImportance > 0 ? (Math.abs(f.importance) / maxImportance) * 100 : 0
          return (
            <button
              key={f.feature}
              type="button"
              aria-pressed={isSelected}
              aria-label={f.feature}
              onClick={() => onSelect(f.feature)}
              className={`w-full text-left px-3 py-2.5 mb-1 rounded flex items-center gap-2 transition-colors focus-ring feature-browser-row${isSelected ? " feature-browser-row--selected" : ""}`}
            >
              <div className="flex-1 min-w-0">
                <div
                  className="text-[13px] break-words"
                  style={{ color: isSelected ? MODEL_COLORS.accent : "var(--text-secondary)" }}
                >
                  {f.feature}
                </div>
                <div
                  className="w-full h-1 rounded-full overflow-hidden mt-0.5"
                  style={{ background: "var(--chrome-hover)" }}
                >
                  <div
                    className="h-full rounded-full"
                    style={{ width: `${barWidth}%`, background: MODEL_COLORS.accent, opacity: 0.6 }}
                  />
                </div>
              </div>
            </button>
          )
        })}
      </div>
    </div>
  )
}
