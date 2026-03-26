import { useState, useMemo } from "react"
import { ChevronDown, ChevronRight, X } from "lucide-react"
import type { OnUpdateConfig } from "../editors"

type Column = { name: string; dtype: string }

const DEFAULT_PARAMS: Record<string, unknown> = {
  iterations: 1000,
  learning_rate: 0.05,
  depth: 6,
  l2_leaf_reg: 3,
  early_stopping_rounds: 50,
}

export type FeatureAndAlgorithmConfigProps = {
  onUpdate: OnUpdateConfig
  columns: Column[]
  target: string
  weight: string
  exclude: string[]
  params: Record<string, unknown>
  featureCount: number
  featuresOpen: boolean
  toggleSection: (section: string) => void
}

export function FeatureAndAlgorithmConfig({
  onUpdate,
  columns,
  target,
  weight,
  exclude,
  params,
  featureCount,
  featuresOpen,
  toggleSection,
}: FeatureAndAlgorithmConfigProps) {
  // GPU toggle lives outside the JSON editor — stripped from display, merged on commit
  const isGpu = (params.task_type as string) === "GPU"
  const { task_type: _taskType, ...displayParams } = params; void _taskType
  const effectiveParams = Object.keys(displayParams).length > 0 ? displayParams : DEFAULT_PARAMS
  const [draft, setDraft] = useState<string>(JSON.stringify(effectiveParams, null, 2))
  const [parseError, setParseError] = useState<string | null>(null)

  // Keep draft in sync when params change externally (e.g. config reload)
  const serialised = JSON.stringify(effectiveParams, null, 2)
  const [lastSynced, setLastSynced] = useState(serialised)
  if (serialised !== lastSynced) {
    setDraft(serialised)
    setLastSynced(serialised)
    setParseError(null)
  }

  const commitParams = (text: string) => {
    try {
      const parsed = JSON.parse(text)
      if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
        setParseError("Must be a JSON object")
        return
      }
      setParseError(null)
      // Merge task_type back in if GPU is enabled
      onUpdate("params", isGpu ? { ...parsed, task_type: "GPU" } : parsed)
    } catch (e) {
      setParseError((e as Error).message)
    }
  }

  const handleGpuToggle = (checked: boolean) => {
    try {
      const current = JSON.parse(draft)
      onUpdate("params", checked ? { ...current, task_type: "GPU" } : { ...current })
    } catch (e) {
      console.warn("GPU toggle: draft has invalid JSON, falling back to last-known params", e)
      onUpdate("params", checked ? { ...displayParams, task_type: "GPU" } : { ...displayParams })
    }
  }

  // Stale excludes: entries in the exclude list that don't match any upstream column
  const upstreamNames = useMemo(() => new Set(columns.map(c => c.name)), [columns])
  const staleExcludes = useMemo(
    () => exclude.filter(e => !upstreamNames.has(e)),
    [exclude, upstreamNames],
  )

  const removeExclude = (name: string) => {
    onUpdate("exclude", exclude.filter(e => e !== name))
  }

  return (
    <>
      {/* Feature Selection */}
      <div>
        <button
          onClick={() => toggleSection("modelling.features")}
          className="flex items-center gap-1 text-[11px] font-bold uppercase tracking-[0.08em]"
          style={{ color: "var(--text-muted)" }}
        >
          {featuresOpen ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
          Features {columns.length > 0 && <span className="font-normal">({featureCount} of {columns.length}{exclude.length > 0 ? `, ${exclude.length} excluded` : ""})</span>}
        </button>
        {featuresOpen && (
          <div className="mt-1.5 space-y-1">
            <div className="flex items-center justify-between">
              <span className="text-[10px]" style={{ color: "var(--text-muted)" }}>Toggle columns to include or exclude from training</span>
              <div className="flex gap-1">
                <button
                  onClick={() => onUpdate("exclude", [])}
                  className="px-1.5 py-0.5 rounded text-[10px] font-mono"
                  style={{ background: "rgba(34,197,94,.1)", color: "#22c55e", border: "1px solid rgba(34,197,94,.2)" }}
                >
                  Select all
                </button>
                <button
                  onClick={() => {
                    const allFeatures = columns
                      .filter(c => c.name !== target && c.name !== weight)
                      .map(c => c.name)
                    onUpdate("exclude", allFeatures)
                  }}
                  className="px-1.5 py-0.5 rounded text-[10px] font-mono"
                  style={{ background: "rgba(239,68,68,.1)", color: "#ef4444", border: "1px solid rgba(239,68,68,.2)" }}
                >
                  Deselect all
                </button>
              </div>
            </div>
            {/* Combined list: upstream columns + stale excludes, sorted together */}
            {(() => {
              const featureCols = columns
                .filter(c => c.name !== target && c.name !== weight)
                .map(c => c.name)
              const staleSet = new Set(staleExcludes)
              const allNames = [...new Set([...featureCols, ...staleExcludes])].sort()

              return allNames.map(name => {
                const isStale = staleSet.has(name)
                const excluded = exclude.includes(name)

                if (isStale) {
                  // Stale entry: orange name + X button
                  return (
                    <div key={name} className="flex items-center gap-2">
                      <span className="text-[11px] font-mono flex-1 truncate" style={{ color: "#f59e0b" }}>{name}</span>
                      <span className="text-[10px] italic" style={{ color: "var(--text-muted)" }}>not found</span>
                      <button
                        onClick={() => removeExclude(name)}
                        className="p-0.5 rounded transition-colors shrink-0"
                        style={{ color: "var(--text-muted)" }}
                        onMouseEnter={(e) => { e.currentTarget.style.color = "#ef4444" }}
                        onMouseLeave={(e) => { e.currentTarget.style.color = "var(--text-muted)" }}
                        title={`Remove "${name}" from exclude list`}
                      >
                        <X size={12} />
                      </button>
                    </div>
                  )
                }

                // Normal upstream column: include/exclude buttons
                return (
                  <div key={name} className="flex items-center gap-2">
                    <span className="text-[11px] font-mono flex-1 truncate" style={{ color: "var(--text-secondary)" }}>{name}</span>
                    {([false, true] as const).map(isExclude => (
                      <button
                        key={isExclude ? "exclude" : "include"}
                        onClick={() => {
                          if (isExclude && !excluded) {
                            onUpdate("exclude", [...exclude, name])
                          } else if (!isExclude && excluded) {
                            removeExclude(name)
                          }
                        }}
                        className="px-1.5 py-0.5 rounded text-[10px] font-mono"
                        style={{
                          background: (isExclude ? excluded : !excluded)
                            ? (isExclude ? "rgba(239,68,68,.15)" : "rgba(34,197,94,.15)")
                            : "var(--chrome-hover)",
                          color: (isExclude ? excluded : !excluded)
                            ? (isExclude ? "#ef4444" : "#22c55e")
                            : "var(--text-muted)",
                          border: `1px solid ${(isExclude ? excluded : !excluded)
                            ? (isExclude ? "rgba(239,68,68,.3)" : "rgba(34,197,94,.3)")
                            : "transparent"}`,
                        }}
                      >
                        {isExclude ? "Exclude" : "Include"}
                      </button>
                    ))}
                  </div>
                )
              })
            })()}

            {/* Remove all stale button */}
            {staleExcludes.length > 0 && (
              <div className="mt-1.5 flex justify-end">
                <button
                  onClick={() => onUpdate("exclude", exclude.filter(e => upstreamNames.has(e)))}
                  className="px-1.5 py-0.5 rounded text-[10px] font-mono"
                  style={{ background: "rgba(245,158,11,.1)", color: "#f59e0b", border: "1px solid rgba(245,158,11,.2)" }}
                >
                  Remove all stale ({staleExcludes.length})
                </button>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Hyperparameters (JSON) */}
      <div>
        <label className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: "var(--text-muted)" }}>
          Hyperparameters
        </label>
        <p className="text-[10px] mt-0.5" style={{ color: "var(--text-muted)" }}>
          Any valid CatBoost parameter. Saved on blur.
        </p>
        <textarea
          value={draft}
          onChange={(e) => {
            setDraft(e.target.value)
            // Clear error while typing
            if (parseError) {
              try { JSON.parse(e.target.value); setParseError(null) } catch { /* still invalid */ }
            }
          }}
          onBlur={() => commitParams(draft)}
          spellCheck={false}
          rows={Math.min(20, Math.max(6, draft.split("\n").length + 1))}
          className="w-full mt-1.5 px-2.5 py-2 rounded-lg text-xs font-mono"
          style={{
            background: "var(--bg-input)",
            border: `1px solid ${parseError ? "#ef4444" : "var(--border)"}`,
            color: "var(--text-primary)",
            resize: "vertical",
          }}
        />
        {parseError && (
          <p className="text-[10px] mt-0.5" style={{ color: "#ef4444" }}>
            {parseError}
          </p>
        )}
        <label className="flex items-center gap-2 mt-2 cursor-pointer select-none">
          <input
            type="checkbox"
            checked={isGpu}
            onChange={(e) => handleGpuToggle(e.target.checked)}
            className="accent-purple-500"
          />
          <span className="text-[11px]" style={{ color: "var(--text-primary)" }}>
            GPU training
          </span>
          <span className="text-[10px]" style={{ color: "var(--text-muted)" }}>
            (CUDA)
          </span>
        </label>
      </div>
    </>
  )
}
