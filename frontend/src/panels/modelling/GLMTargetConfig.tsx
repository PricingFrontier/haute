import type { OnUpdateConfig } from "../editors"
import { configField } from "../../utils/configField"
import { toggleButtonStyle } from "./styles"
import { OffsetFieldLabel } from "./OffsetFieldLabel"

type Column = { name: string; dtype: string }

const FAMILIES = [
  { value: "poisson", label: "Poisson", hint: "Claim frequency" },
  { value: "gamma", label: "Gamma", hint: "Claim severity" },
  { value: "tweedie", label: "Tweedie", hint: "Pure premium" },
  { value: "gaussian", label: "Gaussian", hint: "Linear regression" },
  { value: "binomial", label: "Binomial", hint: "Binary outcomes" },
  { value: "quasipoisson", label: "Quasi-Poisson", hint: "Overdispersed counts" },
  { value: "negbinomial", label: "Neg. Binomial", hint: "Overdispersed counts" },
] as const

const CANONICAL_LINKS: Record<string, string> = {
  poisson: "log",
  gamma: "log",
  tweedie: "log",
  gaussian: "identity",
  binomial: "logit",
  quasipoisson: "log",
  quasibinomial: "logit",
  negbinomial: "log",
}

const LINK_FUNCTIONS = ["log", "identity", "logit", "inverse", "sqrt", "cloglog", "probit"]

const GLM_METRICS = [
  { value: "gini", label: "Gini" },
  { value: "rmse", label: "RMSE" },
  { value: "mae", label: "MAE" },
  { value: "poisson_deviance", label: "Poisson Dev." },
  { value: "tweedie_deviance", label: "Tweedie Dev." },
  { value: "r2", label: "R²" },
  { value: "auc", label: "AUC" },
  { value: "logloss", label: "Logloss" },
]

export type GLMTargetConfigProps = {
  config: Record<string, unknown>
  onUpdate: OnUpdateConfig
  columns: Column[]
}

export function GLMTargetConfig({ config, onUpdate, columns }: GLMTargetConfigProps) {
  const target = configField(config, "target", "")
  const weight = configField(config, "weight", "")
  // No display default: an unselected family must LOOK unselected — the
  // backend requires an explicit family (unset would have silently trained
  // gaussian), so faking a "poisson" selection here would show a chosen
  // family the config doesn't actually have.
  const family = configField(config, "family", "")
  const link = configField(config, "link", "")
  const intercept = configField(config, "intercept", true)
  const metrics = configField<string[]>(config, "metrics", ["gini", "poisson_deviance"])
  const canonicalLink = CANONICAL_LINKS[family] || "log"

  return (
    <div>
      <label className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: "var(--text-muted)" }}>
        Target & Weight
      </label>
      <div className="mt-1.5 space-y-2">
        {/* Target */}
        <div>
          <label className="text-xs" style={{ color: "var(--text-secondary)" }}>Target column</label>
          <select
            value={target}
            onChange={(e) => onUpdate("target", e.target.value)}
            className="w-full mt-0.5 px-2.5 py-1.5 rounded-lg text-xs font-mono"
            style={{ background: "var(--bg-input)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
          >
            <option value="">Select target...</option>
            {columns.map(c => <option key={c.name} value={c.name}>{c.name} ({c.dtype})</option>)}
          </select>
        </div>

        {/* Weight */}
        <div>
          <label className="text-xs" style={{ color: "var(--text-secondary)" }}>Weight column (optional)</label>
          <select
            value={weight}
            onChange={(e) => onUpdate("weight", e.target.value)}
            className="w-full mt-0.5 px-2.5 py-1.5 rounded-lg text-xs font-mono"
            style={{ background: "var(--bg-input)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
          >
            <option value="">None</option>
            {columns.map(c => <option key={c.name} value={c.name}>{c.name}</option>)}
          </select>
        </div>

        {/* Offset */}
        <div>
          <OffsetFieldLabel />
          <select
            value={configField(config, "offset", "")}
            onChange={(e) => onUpdate("offset", e.target.value || null)}
            className="w-full mt-0.5 px-2.5 py-1.5 rounded-lg text-xs font-mono"
            style={{ background: "var(--bg-input)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
          >
            <option value="">None</option>
            {columns.map(c => <option key={c.name} value={c.name}>{c.name}</option>)}
          </select>
        </div>

        {/* Family */}
        <div>
          <label className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: "var(--text-muted)" }}>
            Family
          </label>
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {FAMILIES.map(f => {
              const selected = family === f.value
              return (
                <button
                  key={f.value}
                  onClick={() => {
                    const defaults: Record<string, string[]> = {
                      poisson: ["gini", "poisson_deviance"],
                      gamma: ["gini", "rmse"],
                      tweedie: ["gini", "tweedie_deviance"],
                      gaussian: ["gini", "rmse"],
                      binomial: ["auc", "logloss"],
                      quasipoisson: ["gini", "poisson_deviance"],
                      negbinomial: ["gini", "poisson_deviance"],
                    }
                    onUpdate({
                      family: f.value,
                      link: "",  // reset to canonical
                      metrics: defaults[f.value] || ["gini", "rmse"],
                    })
                  }}
                  className="px-2.5 py-1 rounded-md text-xs font-mono transition-colors"
                  style={toggleButtonStyle(selected)}
                  title={f.hint}
                >
                  {f.label}
                </button>
              )
            })}
          </div>
        </div>

        {/* Link function */}
        <div>
          <label className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: "var(--text-muted)" }}>
            Link Function
          </label>
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            <button
              onClick={() => onUpdate("link", "")}
              className="px-2.5 py-1 rounded-md text-xs font-mono transition-colors"
              style={toggleButtonStyle(!link)}
            >
              auto ({canonicalLink})
            </button>
            {LINK_FUNCTIONS.filter(l => l !== canonicalLink).map(l => (
              <button
                key={l}
                onClick={() => onUpdate("link", l)}
                className="px-2.5 py-1 rounded-md text-xs font-mono transition-colors"
                style={toggleButtonStyle(link === l)}
              >
                {l}
              </button>
            ))}
          </div>
        </div>

        {/* Tweedie variance power */}
        {family === "tweedie" && (
          <div>
            <label className="text-[11px]" style={{ color: "var(--text-muted)" }}>
              Variance power (1.0=Poisson, 2.0=Gamma)
            </label>
            <input
              type="range" min={1.0} max={2.0} step={0.05}
              value={configField(config, "var_power", 1.5)}
              onChange={(e) => onUpdate("var_power", parseFloat(e.target.value))}
              className="w-full mt-0.5"
            />
            <div className="text-[11px] font-mono text-right" style={{ color: "var(--text-muted)" }}>
              {configField(config, "var_power", 1.5).toFixed(2)}
            </div>
          </div>
        )}

        {/* Intercept */}
        <label className="flex items-center gap-2 cursor-pointer select-none">
          <input
            type="checkbox"
            checked={intercept}
            onChange={(e) => onUpdate("intercept", e.target.checked)}
            className="accent-purple-500"
          />
          <span className="text-[11px]" style={{ color: "var(--text-primary)" }}>Intercept</span>
        </label>

        {/* Metrics */}
        <div>
          <label className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: "var(--text-muted)" }}>
            Metrics
          </label>
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {GLM_METRICS.map(m => {
              const selected = metrics.includes(m.value)
              return (
                <button
                  key={m.value}
                  onClick={() => {
                    const newMetrics = selected
                      ? metrics.filter(x => x !== m.value)
                      : [...metrics, m.value]
                    onUpdate("metrics", newMetrics)
                  }}
                  className="px-2.5 py-1 rounded-md text-xs font-mono transition-colors"
                  style={toggleButtonStyle(selected)}
                >
                  {m.label}
                </button>
              )
            })}
          </div>
        </div>
      </div>
    </div>
  )
}
