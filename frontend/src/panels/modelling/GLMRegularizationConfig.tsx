import { useState } from "react"
import { ChevronDown, ChevronRight } from "lucide-react"
import type { OnUpdateConfig } from "../editors"
import { configField } from "../../utils/configField"
import { toggleButtonStyle } from "./styles"

const REGULARIZATION_TYPES = [
  { value: "", label: "None" },
  { value: "ridge", label: "Ridge" },
  { value: "lasso", label: "Lasso" },
  { value: "elastic_net", label: "Elastic Net" },
] as const

export type GLMRegularizationConfigProps = {
  config: Record<string, unknown>
  onUpdate: OnUpdateConfig
}

export function GLMRegularizationConfig({ config, onUpdate }: GLMRegularizationConfigProps) {
  const [open, setOpen] = useState(false)
  const regularization = configField(config, "regularization", "")
  const alpha = configField(config, "alpha", 0)
  const l1Ratio = configField(config, "l1_ratio", 0.5)
  const isActive = !!regularization

  return (
    <div>
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center gap-1 text-[11px] font-bold uppercase tracking-[0.08em]"
        style={{ color: "var(--text-muted)" }}
      >
        {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        Regularization
        {isActive && (
          <span className="font-normal text-[10px] px-1.5 py-0.5 rounded-full"
            style={{ background: "rgba(168,85,247,.15)", color: "#a855f7" }}>
            {regularization}
          </span>
        )}
      </button>

      {open && (
        <div className="mt-1.5 space-y-2">
          {/* Type toggle */}
          <div className="flex flex-wrap gap-1.5">
            {REGULARIZATION_TYPES.map(r => (
              <button
                key={r.value}
                onClick={() => onUpdate("regularization", r.value || null)}
                className="px-2.5 py-1 rounded-md text-xs font-mono transition-colors"
                style={toggleButtonStyle(regularization === r.value)}
              >
                {r.label}
              </button>
            ))}
          </div>

          {isActive && (
            <>
              {/* Alpha */}
              <div>
                <label className="text-xs" style={{ color: "var(--text-secondary)" }}>
                  Alpha ({alpha === 0 ? "Auto via CV" : "Manual"})
                </label>
                <input
                  type="number"
                  value={alpha}
                  onChange={(e) => onUpdate("alpha", parseFloat(e.target.value) || 0)}
                  className="w-full mt-0.5 px-2.5 py-1.5 rounded-lg text-xs font-mono"
                  style={{ background: "var(--bg-input)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
                  min={0}
                  step={0.01}
                  placeholder="0 = auto (CV)"
                />
              </div>

              {/* L1 ratio (Elastic Net only) */}
              {regularization === "elastic_net" && (
                <div>
                  <label className="text-[11px]" style={{ color: "var(--text-muted)" }}>
                    L1 ratio (0=Ridge, 1=Lasso)
                  </label>
                  <input
                    type="range" min={0} max={1} step={0.05}
                    value={l1Ratio}
                    onChange={(e) => onUpdate("l1_ratio", parseFloat(e.target.value))}
                    className="w-full mt-0.5"
                  />
                  <div className="text-[11px] font-mono text-right" style={{ color: "var(--text-muted)" }}>
                    {l1Ratio.toFixed(2)}
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  )
}
