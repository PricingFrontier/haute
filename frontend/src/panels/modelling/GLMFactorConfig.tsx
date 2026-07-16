import { useState, useMemo, useCallback, useEffect, useRef } from "react"
import { ChevronDown, ChevronRight, Plus, Trash2 } from "lucide-react"
import type { OnUpdateConfig } from "../editors"
import { configField, safeParseFloat } from "../../utils/configField"
import { FailoverHelp } from "./FailoverHelp"

const ALL_FACTORS_HELP =
  "With no factors configured, the GLM would silently auto-build one term per " +
  "column (linear for numeric, categorical for strings) — an all-features " +
  "model you never chose. Tick 'All features' to opt into that explicitly, or " +
  "add factors below. Your individual factor setup is kept either way."

type Column = { name: string; dtype: string }

/** RustyStats term type options */
const TERM_TYPES = [
  { value: "linear", label: "Linear" },
  { value: "categorical", label: "Categorical" },
  { value: "bs", label: "B-spline" },
  { value: "ns", label: "Nat. spline" },
  { value: "target_encoding", label: "Target enc." },
  { value: "expression", label: "Expression" },
] as const

type TermSpec = {
  type: string
  df?: number
  k?: number
  degree?: number
  monotonicity?: string
  prior_weight?: number
  expr?: string
  levels?: string[]
}

type InteractionSpec = {
  factors: string[]
  include_main: boolean
}

export type GLMFactorConfigProps = {
  config: Record<string, unknown>
  onUpdate: OnUpdateConfig
  columns: Column[]
  target: string
  weight: string
  exclude: string[]
}

const STRING_TYPES = new Set(["Utf8", "String", "Categorical", "str", "string", "object", "Enum"])

function defaultTermType(col: Column): string {
  return STRING_TYPES.has(col.dtype) ? "categorical" : "linear"
}

/** Properties that are relevant to each term type. Anything not listed is stripped on type change. */
const TYPE_RELEVANT_PROPS: Record<string, Set<string>> = {
  linear: new Set(["monotonicity"]),
  categorical: new Set(["levels"]),
  bs: new Set(["df", "degree", "monotonicity"]),
  ns: new Set(["df", "monotonicity"]),
  target_encoding: new Set(["prior_weight", "k"]),
  expression: new Set(["expr"]),
}

/** Build a clean TermSpec for the new type, preserving only relevant properties. */
function cleanTermForType(oldSpec: TermSpec, newType: string): TermSpec {
  const relevant = TYPE_RELEVANT_PROPS[newType] ?? new Set()
  const cleaned: TermSpec = { type: newType }
  for (const [key, val] of Object.entries(oldSpec)) {
    if (key !== "type" && relevant.has(key)) {
      ;(cleaned as Record<string, unknown>)[key] = val
    }
  }
  return cleaned
}

type FactorMode = "builder" | "json"

export function GLMFactorConfig({
  config,
  onUpdate,
  columns,
  target,
  weight,
  exclude,
}: GLMFactorConfigProps) {
  const terms = configField<Record<string, TermSpec>>(config, "terms", {})
  const interactions = configField<InteractionSpec[]>(config, "interactions", [])
  const offset = configField(config, "offset", "")
  // "All features" is an explicit opt-in to auto-terms (one linear/categorical
  // term per column). It gates the otherwise-silent empty-terms failover: with
  // it off and no terms, the model would auto-build over every column with no
  // signal. When on, the individual builder greys out but keeps its state, so
  // unticking restores exactly what was configured before.
  const allFactors = configField(config, "all_factors", false)

  const [factorsOpen, setFactorsOpen] = useState(true)
  const [mode, setMode] = useState<FactorMode>("builder")

  // All eligible columns (not target/weight/offset/exclude)
  const eligibleColumns = useMemo(
    () => columns.filter(c => c.name !== target && c.name !== weight && c.name !== offset && !exclude.includes(c.name)),
    [columns, target, weight, offset, exclude],
  )

  // Columns already added as factors
  const addedFactors = useMemo(() => {
    return Object.keys(terms)
      .map(name => {
        const col = eligibleColumns.find(c => c.name === name)
        return col ? { col, spec: terms[name] } : null
      })
      .filter((r): r is { col: Column; spec: TermSpec } => r !== null)
  }, [terms, eligibleColumns])

  // Columns available to add (eligible but not yet in terms)
  const availableColumns = useMemo(
    () => eligibleColumns.filter(c => !(c.name in terms)),
    [eligibleColumns, terms],
  )

  // JSON textarea — only reflects what's in terms
  const termsJson = useMemo(() => JSON.stringify(terms, null, 2), [terms])

  const [jsonDraft, setJsonDraft] = useState(termsJson)
  const [jsonError, setJsonError] = useState<string | null>(null)

  // Sync draft when terms change externally (e.g. builder edits while on JSON tab)
  const lastSyncedRef = useRef(termsJson)
  useEffect(() => {
    if (termsJson !== lastSyncedRef.current) {
      setJsonDraft(termsJson)
      lastSyncedRef.current = termsJson
      setJsonError(null)
    }
  }, [termsJson])

  // ── Add / remove factors ──

  const addFactor = useCallback((name: string) => {
    const col = eligibleColumns.find(c => c.name === name)
    if (!col) return
    onUpdate("terms", { ...terms, [name]: { type: defaultTermType(col) } })
  }, [terms, eligibleColumns, onUpdate])

  const removeFactor = useCallback((name: string) => {
    const { [name]: _removed, ...rest } = terms; void _removed
    onUpdate("terms", rest)
  }, [terms, onUpdate])

  // ── Update term spec ──

  const updateTerm = useCallback((name: string, spec: TermSpec) => {
    onUpdate("terms", { ...terms, [name]: spec })
  }, [terms, onUpdate])

  const updateTermField = useCallback((name: string, field: string, value: unknown) => {
    const current = terms[name] || { type: "linear" }
    const updated = { ...current, [field]: value }
    if (value === undefined || value === null || value === "") {
      delete (updated as Record<string, unknown>)[field]
    }
    onUpdate("terms", { ...terms, [name]: updated })
  }, [terms, onUpdate])

  // ── JSON commit ──

  const commitJson = useCallback((text: string) => {
    try {
      const parsed = JSON.parse(text)
      if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
        setJsonError("Must be a JSON object")
        return
      }
      setJsonError(null)
      onUpdate("terms", parsed)
    } catch (e) {
      setJsonError((e as Error).message)
    }
  }, [onUpdate])

  // ── Interactions ──

  const addInteraction = useCallback(() => {
    onUpdate("interactions", [...interactions, { factors: ["", ""], include_main: true }])
  }, [interactions, onUpdate])

  const removeInteraction = useCallback((idx: number) => {
    onUpdate("interactions", interactions.filter((_, i) => i !== idx))
  }, [interactions, onUpdate])

  const updateInteraction = useCallback((idx: number, field: string, value: unknown) => {
    const updated = interactions.map((x, i) => i === idx ? { ...x, [field]: value } : x)
    onUpdate("interactions", updated)
  }, [interactions, onUpdate])

  const updateInteractionFactor = useCallback((idx: number, factorIdx: number, value: string) => {
    const updated = interactions.map((x, i) => {
      if (i !== idx) return x
      const factors = [...x.factors]
      factors[factorIdx] = value
      return { ...x, factors }
    })
    onUpdate("interactions", updated)
  }, [interactions, onUpdate])

  // Interaction dropdowns only show added factors
  const includedFeatures = addedFactors.map(r => r.col.name)

  return (
    <>
      {/* Factors header + mode toggle */}
      <div>
        <div className="flex items-center justify-between">
          <button
            onClick={() => setFactorsOpen(!factorsOpen)}
            className="flex items-center gap-1 text-[11px] font-bold uppercase tracking-[0.08em]"
            style={{ color: "var(--text-muted)" }}
          >
            {factorsOpen ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
            Factors
            <span className="font-normal">({addedFactors.length})</span>
          </button>

          {factorsOpen && (
            <div className="flex gap-0.5">
              <button
                onClick={() => setMode("builder")}
                className="px-2 py-0.5 rounded text-[10px] font-medium"
                style={{
                  background: mode === "builder" ? "var(--accent-soft)" : "var(--chrome-hover)",
                  color: mode === "builder" ? "var(--accent)" : "var(--text-muted)",
                }}
              >
                Builder
              </button>
              <button
                onClick={() => setMode("json")}
                className="px-2 py-0.5 rounded text-[10px] font-medium"
                style={{
                  background: mode === "json" ? "var(--accent-soft)" : "var(--chrome-hover)",
                  color: mode === "json" ? "var(--accent)" : "var(--text-muted)",
                }}
              >
                JSON
              </button>
            </div>
          )}
        </div>

        {/* All-features gate — explicit opt-in to auto-terms. */}
        {factorsOpen && (
          <label className="flex items-center gap-1.5 mt-1.5 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={allFactors}
              onChange={(e) => onUpdate("all_factors", e.target.checked || null)}
              className="accent-purple-500"
            />
            <span className="text-[11px]" style={{ color: "var(--text-primary)" }}>
              All features
            </span>
            <span className="text-[10px]" style={{ color: "var(--text-muted)" }}>
              (one term per column)
            </span>
            <FailoverHelp label={ALL_FACTORS_HELP} />
          </label>
        )}

        {factorsOpen && allFactors && (
          <div className="mt-1 text-[10px]" style={{ color: "var(--text-muted)" }}>
            Using every eligible column. Untick to edit the factor list below (kept).
          </div>
        )}

        {factorsOpen && mode === "builder" && (
          <div
            className="mt-1.5 space-y-1.5"
            style={allFactors ? { opacity: 0.4, pointerEvents: "none" } : undefined}
          >
            {/* Added factors */}
            {addedFactors.length > 0 ? (
              <div className="space-y-1">
                {addedFactors.map(({ col, spec }) => (
                  <div key={col.name} className="flex items-center gap-1.5">
                    {/* Remove button */}
                    <button
                      onClick={() => removeFactor(col.name)}
                      className="p-0.5 rounded transition-colors shrink-0"
                      style={{ color: "var(--text-muted)" }}
                      title={`Remove ${col.name}`}
                    >
                      <Trash2 size={11} />
                    </button>

                    {/* Feature name */}
                    <span
                      className="text-[11px] font-mono truncate"
                      style={{ color: "var(--text-secondary)", minWidth: "80px", flex: "1 1 0" }}
                      title={col.name}
                    >
                      {col.name}
                    </span>

                    {/* Type dropdown */}
                    <select
                      value={spec.type}
                      onChange={(e) => updateTerm(col.name, cleanTermForType(spec, e.target.value))}
                      className="px-1.5 py-0.5 rounded text-[10px] font-mono"
                      style={{
                        background: "var(--bg-input)",
                        border: "1px solid var(--border)",
                        color: "var(--text-primary)",
                        minWidth: "85px",
                      }}
                    >
                      {TERM_TYPES.map(t => (
                        <option key={t.value} value={t.value}>{t.label}</option>
                      ))}
                    </select>

                    {/* Type-specific params */}
                    {(spec.type === "bs" || spec.type === "ns") && (
                      <input
                        type="number"
                        value={spec.df ?? ""}
                        onChange={(e) => updateTermField(col.name, "df", e.target.value ? parseInt(e.target.value) : undefined)}
                        placeholder="df"
                        className="w-10 px-1 py-0.5 rounded text-[10px] font-mono text-center"
                        style={{ background: "var(--bg-input)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
                        min={2}
                        max={20}
                        title="Degrees of freedom"
                      />
                    )}

                    {spec.type === "target_encoding" && (
                      <input
                        type="number"
                        value={spec.prior_weight ?? 1.0}
                        onChange={(e) => updateTermField(col.name, "prior_weight", safeParseFloat(e.target.value, 1.0))}
                        className="w-10 px-1 py-0.5 rounded text-[10px] font-mono text-center"
                        style={{ background: "var(--bg-input)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
                        step={0.5}
                        min={0}
                        title="Prior weight"
                      />
                    )}

                    {/* Monotonicity toggle */}
                    {(spec.type === "linear" || spec.type === "bs" || spec.type === "ns") && (
                      <button
                        onClick={() => {
                          const cycle = { undefined: "increasing", increasing: "decreasing", decreasing: undefined } as Record<string, string | undefined>
                          const next = cycle[spec.monotonicity ?? "undefined"]
                          updateTermField(col.name, "monotonicity", next)
                        }}
                        className="px-1 py-0.5 rounded text-[10px] font-mono"
                        style={{
                          background: spec.monotonicity ? "var(--model-accent-soft)" : "var(--chrome-hover)",
                          color: spec.monotonicity ? "var(--chart-above)" : "var(--text-muted)",
                          border: `1px solid ${spec.monotonicity ? "var(--model-accent-border)" : "transparent"}`,
                          minWidth: "20px",
                        }}
                        title={
                          spec.monotonicity === "increasing" ? "Increasing (click: decreasing)"
                            : spec.monotonicity === "decreasing" ? "Decreasing (click: none)"
                            : "No constraint (click: increasing)"
                        }
                      >
                        {spec.monotonicity === "increasing" ? "↑" : spec.monotonicity === "decreasing" ? "↓" : "─"}
                      </button>
                    )}
                  </div>
                ))}
              </div>
            ) : (
              <div className="text-[10px] py-1" style={{ color: "var(--text-muted)" }}>
                No factors added yet. Add columns below to include them in the model.
              </div>
            )}

            {/* Add factor dropdown */}
            {availableColumns.length > 0 && (
              <div className="pt-1" style={{ borderTop: "1px solid var(--border)" }}>
                <select
                  value=""
                  onChange={(e) => { if (e.target.value) addFactor(e.target.value) }}
                  className="w-full px-1.5 py-1 rounded text-[10px] font-mono"
                  style={{ background: "var(--bg-input)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
                >
                  <option value="">Add factor...</option>
                  {availableColumns.map(c => (
                    <option key={c.name} value={c.name}>{c.name} ({c.dtype})</option>
                  ))}
                </select>
              </div>
            )}
          </div>
        )}

        {factorsOpen && mode === "json" && (
          <div
            className="mt-1.5"
            style={allFactors ? { opacity: 0.4, pointerEvents: "none" } : undefined}
          >
            <p className="text-[10px] mb-1" style={{ color: "var(--text-muted)" }}>
              RustyStats terms dict. Paste from Atelier or edit directly. Saved on blur.
            </p>
            <textarea
              value={jsonDraft}
              onChange={(e) => {
                setJsonDraft(e.target.value)
                if (jsonError) {
                  try { JSON.parse(e.target.value); setJsonError(null) } catch { /* still invalid */ }
                }
              }}
              onBlur={() => commitJson(jsonDraft)}
              spellCheck={false}
              rows={Math.min(20, Math.max(6, jsonDraft.split("\n").length + 1))}
              className="w-full px-2.5 py-2 rounded-lg text-xs font-mono"
              style={{
                background: "var(--bg-input)",
                border: `1px solid ${jsonError ? "var(--danger)" : "var(--border)"}`,
                color: "var(--text-primary)",
                resize: "vertical",
              }}
            />
            {jsonError && (
              <p className="text-[10px] mt-0.5" style={{ color: "var(--danger)" }}>
                {jsonError}
              </p>
            )}
          </div>
        )}
      </div>

      {/* Interactions */}
      <div>
        <div className="flex items-center justify-between">
          <label className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: "var(--text-muted)" }}>
            Interactions
          </label>
          <button
            onClick={addInteraction}
            className="flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium transition-colors"
            style={{ color: "var(--chart-above)" }}
          >
            <Plus size={10} /> Add
          </button>
        </div>
        {interactions.length > 0 && (
          <div className="mt-1.5 space-y-1.5">
            {interactions.map((interaction, idx) => (
              <div key={idx} className="flex items-center gap-1.5">
                <select
                  value={interaction.factors[0] || ""}
                  onChange={(e) => updateInteractionFactor(idx, 0, e.target.value)}
                  className="flex-1 px-1.5 py-1 rounded text-[10px] font-mono"
                  style={{ background: "var(--bg-input)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
                >
                  <option value="">Select...</option>
                  {includedFeatures.map(f => <option key={f} value={f}>{f}</option>)}
                </select>
                <span className="text-[10px]" style={{ color: "var(--text-muted)" }}>x</span>
                <select
                  value={interaction.factors[1] || ""}
                  onChange={(e) => updateInteractionFactor(idx, 1, e.target.value)}
                  className="flex-1 px-1.5 py-1 rounded text-[10px] font-mono"
                  style={{ background: "var(--bg-input)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
                >
                  <option value="">Select...</option>
                  {includedFeatures.filter(f => f !== interaction.factors[0]).map(f => (
                    <option key={f} value={f}>{f}</option>
                  ))}
                </select>
                <label className="flex items-center gap-1 text-[10px] whitespace-nowrap" style={{ color: "var(--text-muted)" }}>
                  <input
                    type="checkbox"
                    checked={interaction.include_main}
                    onChange={(e) => updateInteraction(idx, "include_main", e.target.checked)}
                    className="accent-purple-500"
                  />
                  main
                </label>
                <button
                  onClick={() => removeInteraction(idx)}
                  className="p-0.5 rounded transition-colors"
                  style={{ color: "var(--text-muted)" }}
                  title="Remove interaction"
                >
                  <Trash2 size={12} />
                </button>
              </div>
            ))}
          </div>
        )}
      </div>
    </>
  )
}
