/**
 * Shared browser sidebar for the per-feature diagnostic tabs (AvE, PDP and
 * the optimiser's Rates tab).
 *
 * Displays a searchable, scrollable list of items in the caller's ranking,
 * with small bars for the ranking measure, which `rankedBy` names so a
 * ranking that is not importance never reads as one. Click to select.
 */
import { useState, useMemo } from "react"
import { Search } from "lucide-react"
import { MODEL_COLORS } from "../../theme/colors"

export type FeatureItem = {
  feature: string
  /** The ranking measure the bars draw; `rankedBy` names it when it is not importance. */
  importance: number
}

export type FeatureRanking = {
  /** The measure's name, shown above the list ("Rate spread"). */
  label: string
  /** One line on what the measure means. */
  description: string
}

export interface FeatureBrowserProps {
  features: FeatureItem[]
  selected: string | null
  onSelect: (feature: string) => void
  width?: number
  search?: string
  onSearch?: (search: string) => void
  /** What the list holds, singular ("feature", "factor"). */
  itemNoun?: string
  rankedBy?: FeatureRanking
}

export function FeatureBrowser({
  features,
  selected,
  onSelect,
  width,
  search: controlledSearch,
  onSearch,
  itemNoun = "feature",
  rankedBy,
}: FeatureBrowserProps) {
  const plural = `${itemNoun}s`
  const Plural = `${plural[0].toUpperCase()}${plural.slice(1)}`
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
            placeholder={`Search ${plural}...`}
            aria-label={`Search ${plural}`}
            className="bg-transparent border-none focus-ring text-[13px] min-w-0 w-full"
            style={{ color: "var(--text-primary)" }}
          />
        </div>
      </div>

      {rankedBy && (
        <div className="mb-2 px-1">
          <div className="text-[11px] font-semibold uppercase tracking-[0.06em]" style={{ color: "var(--text-muted)" }}>
            {rankedBy.label}
          </div>
          <p className="m-0 text-[11px]" style={{ color: "var(--text-muted)" }}>
            {rankedBy.description}
          </p>
        </div>
      )}

      {/* Item list */}
      <div
        className="validation-feature-list"
        role="group"
        aria-label={rankedBy ? `${Plural} ranked by ${rankedBy.label.toLowerCase()}` : Plural}
      >
        {filtered.length === 0 && (
          <div className="px-2 py-3 text-xs text-center" style={{ color: "var(--text-muted)" }}>
            No {plural} found
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
