/**
 * Shared feature browser sidebar for AvE and PDP tabs.
 *
 * Displays a searchable, scrollable list of features sorted by importance,
 * with small importance bars. Click to select a feature.
 */
import { useState, useMemo } from "react"
import { Search } from "lucide-react"

export type FeatureItem = {
  feature: string
  importance: number
}

interface FeatureBrowserProps {
  features: FeatureItem[]
  selected: string | null
  onSelect: (feature: string) => void
  width?: number
}

export function FeatureBrowser({ features, selected, onSelect, width = 180 }: FeatureBrowserProps) {
  const [search, setSearch] = useState("")

  const filtered = useMemo(() => {
    if (!search) return features
    const q = search.toLowerCase()
    return features.filter(f => f.feature.toLowerCase().includes(q))
  }, [features, search])

  const maxImportance = features.length > 0 ? features.map(f => Math.abs(f.importance)).reduce((a, b) => Math.max(a, b), -Infinity) : 1

  return (
    <div className="flex flex-col shrink-0" style={{ width, borderRight: "1px solid var(--border)" }}>
      {/* Search */}
      <div className="px-2 py-1.5 shrink-0" style={{ borderBottom: "1px solid var(--border)" }}>
        <div className="flex items-center gap-1.5 px-2 py-1 rounded" style={{ background: "var(--bg-input)", border: "1px solid var(--border)" }}>
          <Search size={11} style={{ color: "var(--text-muted)" }} />
          <input
            type="text"
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder="Search features..."
            className="bg-transparent border-none outline-none text-[11px] w-full"
            style={{ color: "var(--text-primary)" }}
          />
        </div>
      </div>

      {/* Feature list */}
      <div className="flex-1 overflow-y-auto">
        {filtered.length === 0 && (
          <div className="px-2 py-3 text-[11px] text-center" style={{ color: "var(--text-muted)" }}>
            No features found
          </div>
        )}
        {filtered.map(f => {
          const isSelected = f.feature === selected
          const barWidth = maxImportance > 0 ? (Math.abs(f.importance) / maxImportance) * 100 : 0
          return (
            <button
              key={f.feature}
              onClick={() => onSelect(f.feature)}
              className={`w-full text-left px-2 py-1 flex items-center gap-1.5 transition-colors ${isSelected ? "" : "hover:bg-[var(--chrome-hover)]"}`}
              style={{
                background: isSelected ? "var(--accent-soft)" : "transparent",
                borderLeft: isSelected ? "2px solid var(--accent)" : "2px solid transparent",
              }}
            >
              <div className="flex-1 min-w-0">
                <div
                  className="text-[11px] truncate"
                  style={{ color: isSelected ? "var(--accent)" : "var(--text-secondary)" }}
                >
                  {f.feature}
                </div>
                <div className="w-full h-1 rounded-full overflow-hidden mt-0.5" style={{ background: "var(--chrome-hover)" }}>
                  <div
                    className="h-full rounded-full"
                    style={{ width: `${barWidth}%`, background: isSelected ? "var(--accent)" : "#a855f7", opacity: 0.6 }}
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
