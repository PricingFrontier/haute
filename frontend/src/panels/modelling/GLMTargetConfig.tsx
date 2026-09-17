import { useState } from "react"
import type { OnUpdateConfig } from "../editors"
import { ApiError } from "../../api/client"
import type { DispersionParam } from "../../api/types"
import { configField } from "../../utils/configField"
import { toggleButtonStyle } from "./styles"
import { FailoverHelp } from "./FailoverHelp"
import { OffsetFieldLabel } from "./OffsetFieldLabel"
import { GLM_FAMILY_LINKS, isGlmFamily, type GlmFamily } from "./glmFamilies"

type Column = { name: string; dtype: string }

/** Display order and labels; the families and their links come from GLM_FAMILY_LINKS. */
const FAMILY_LABELS: Record<GlmFamily, { label: string; hint: string; metrics: string[] }> = {
  poisson: { label: "Poisson", hint: "Claim frequency", metrics: ["gini", "poisson_deviance"] },
  gamma: { label: "Gamma", hint: "Claim severity", metrics: ["gini", "rmse"] },
  tweedie: { label: "Tweedie", hint: "Pure premium", metrics: ["gini", "tweedie_deviance"] },
  gaussian: { label: "Gaussian", hint: "Linear regression", metrics: ["gini", "rmse"] },
  binomial: { label: "Binomial", hint: "Binary outcomes", metrics: ["auc", "logloss"] },
  quasipoisson: { label: "Quasi-Poisson", hint: "Overdispersed counts", metrics: ["gini", "poisson_deviance"] },
  quasibinomial: { label: "Quasi-Binomial", hint: "Overdispersed binary outcomes", metrics: ["auc", "logloss"] },
  negbinomial: { label: "Neg. Binomial", hint: "Overdispersed counts (explicit theta)", metrics: ["gini", "poisson_deviance"] },
}
const FAMILIES = (Object.keys(GLM_FAMILY_LINKS) as GlmFamily[]).map((value) => ({ value, ...FAMILY_LABELS[value] }))

const TWEEDIE_HELP =
  "Tweedie interpolates between Poisson (power 1) and Gamma (power 2); the " +
  "variance power sets where. There is no sensible default — leaving it unset " +
  "would silently fit at power 1.5, so a choice is required. Estimate profiles " +
  "the likelihood over the power on the node's training data; the result is " +
  "filled in for you to accept or adjust. You can change it later; the value " +
  "is kept if you switch family and back."

const THETA_HELP =
  "Negative Binomial dispersion: variance = mean + mean²/theta (smaller theta " +
  "= more overdispersion). RustyStats does not estimate theta and refuses to " +
  "fit without it, so a choice is required. Estimate profiles the likelihood " +
  "over theta on the node's training data; the result is filled in for you to " +
  "accept or adjust."

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
  /** Run a profile-likelihood dispersion estimate on the node's training
   *  data and resolve with the value. The estimate is an explicit user
   *  action — the resolved value is filled into the config field for the
   *  user to accept or adjust, never applied silently. */
  onEstimateDispersion?: (param: DispersionParam) => Promise<number>
}

export function GLMTargetConfig({ config, onUpdate, columns, onEstimateDispersion }: GLMTargetConfigProps) {
  const [estimating, setEstimating] = useState<DispersionParam | null>(null)
  const [estimateError, setEstimateError] = useState<string | null>(null)
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
  const links: readonly string[] = isGlmFamily(family) ? GLM_FAMILY_LINKS[family] : []
  const canonicalLink = links[0]
  const linkUnavailable = link !== "" && !links.includes(link)
  const theta = config.theta

  const handleEstimate = async (param: DispersionParam) => {
    if (!onEstimateDispersion || estimating) return
    setEstimating(param)
    setEstimateError(null)
    try {
      const value = await onEstimateDispersion(param)
      // Filled in, not applied silently: the value lands in the visible,
      // editable field and the user keeps the final say.
      onUpdate(param, value)
    } catch (e) {
      // Prefer the backend's actionable detail ("GLM config has no factors…")
      // over the generic "HTTP 400" message.
      const detail = e instanceof ApiError ? e.detail : undefined
      setEstimateError(detail || (e instanceof Error ? e.message : String(e)))
    } finally {
      setEstimating(null)
    }
  }

  const estimateButton = (param: DispersionParam) =>
    onEstimateDispersion && (
      <button
        onClick={() => handleEstimate(param)}
        disabled={estimating !== null}
        className="px-2.5 py-1.5 rounded-lg text-xs font-medium whitespace-nowrap"
        style={{ background: "var(--bg-input)", border: "1px solid var(--border)", color: "var(--text-primary)", opacity: estimating ? 0.6 : 1 }}
      >
        {estimating === param ? "Estimating…" : "Estimate from data"}
      </button>
    )

  return (
    <div>
      <p className="text-[10px] mb-1" aria-label="Selected algorithm">Algorithm <strong>Rustystats</strong></p>
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
                    onUpdate({
                      family: f.value,
                      link: "",  // reset to canonical
                      metrics: f.metrics,
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
          {family !== "" && !isGlmFamily(family) && (
            <p role="alert" className="mt-1 text-[11px]" style={{ color: "var(--danger)" }}>
              The saved family {family} is not supported; choose one above.
            </p>
          )}
        </div>

        {/* Link function: the links RustyStats supports for the family. */}
        {links.length > 0 && (
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
              {links.slice(1).map(l => (
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
            {linkUnavailable && (
              <p role="alert" className="mt-1 text-[11px]" style={{ color: "var(--danger)" }}>
                The saved link {link} is not available for {FAMILY_LABELS[family as GlmFamily].label}; choose one above.
              </p>
            )}
          </div>
        )}

        {/* Tweedie variance power — gated: no silent 1.5 failover. */}
        {family === "tweedie" && (
          <div>
            <label className="flex items-center gap-1 text-[11px]" style={{ color: "var(--text-muted)" }}>
              Variance power (1.0=Poisson, 2.0=Gamma)
              <FailoverHelp label={TWEEDIE_HELP} />
            </label>
            {config.var_power === undefined || config.var_power === null ? (
              <div className="mt-1 flex gap-1.5">
                <button
                  onClick={() => onUpdate("var_power", 1.5)}
                  className="flex-1 px-2.5 py-1.5 rounded-lg text-xs font-medium"
                  style={{ background: "var(--warning-soft-subtle)", border: "1px solid var(--warning-border)", color: "var(--warning)" }}
                >
                  Set variance power (required for Tweedie)
                </button>
                {estimateButton("var_power")}
              </div>
            ) : (
              <>
                <input
                  type="range" min={1.0} max={2.0} step={0.05}
                  value={configField(config, "var_power", 1.5)}
                  onChange={(e) => onUpdate("var_power", parseFloat(e.target.value))}
                  className="w-full mt-0.5"
                />
                <div className="mt-0.5 flex items-center justify-between gap-1.5">
                  {estimateButton("var_power")}
                  <div className="text-[11px] font-mono text-right" style={{ color: "var(--text-muted)" }}>
                    {configField(config, "var_power", 1.5).toFixed(2)}
                  </div>
                </div>
              </>
            )}
            {estimateError && estimating === null && (
              <div className="mt-1 text-[11px]" style={{ color: "var(--warning)" }}>
                Estimation failed: {estimateError}
              </div>
            )}
          </div>
        )}

        {/* Negative Binomial dispersion — gated: RustyStats refuses to fit
            without it. Starts empty; the user types a value or triggers an
            explicit estimate from the training data. */}
        {family === "negbinomial" && (
          <div>
            <label className="flex items-center gap-1 text-[11px]" style={{ color: "var(--text-muted)" }}>
              Dispersion theta (required for Neg. Binomial)
              <FailoverHelp label={THETA_HELP} />
            </label>
            <div className="mt-1 flex gap-1.5">
              <input
                type="number" min={0} step="any"
                value={typeof theta === "number" ? theta : ""}
                placeholder="e.g. 1.5"
                onChange={(e) => {
                  const parsed = parseFloat(e.target.value)
                  onUpdate("theta", Number.isFinite(parsed) ? parsed : null)
                }}
                className="flex-1 min-w-0 px-2.5 py-1.5 rounded-lg text-xs font-mono"
                style={
                  typeof theta === "number" && theta > 0
                    ? { background: "var(--bg-input)", border: "1px solid var(--border)", color: "var(--text-primary)" }
                    : { background: "var(--warning-soft-subtle)", border: "1px solid var(--warning-border)", color: "var(--text-primary)" }
                }
              />
              {estimateButton("theta")}
            </div>
            {typeof theta === "number" && !(theta > 0) && (
              <p role="alert" className="mt-1 text-[11px]" style={{ color: "var(--danger)" }}>
                Theta must be greater than 0.
              </p>
            )}
            {estimateError && estimating === null && (
              <div className="mt-1 text-[11px]" style={{ color: "var(--warning)" }}>
                Estimation failed: {estimateError}
              </div>
            )}
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
