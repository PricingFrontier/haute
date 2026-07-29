import { configField, safeParseInt } from "../../utils/configField"
import { CHART_COLORS } from "../../theme/colors"

type Column = { name: string; dtype: string }

export type SplitAndMetricsConfigProps = {
  columns: Column[]
  split: Record<string, unknown>
  onSplitUpdate: (key: string, value: unknown) => void
}

const STRATEGIES = ["random", "temporal", "group"] as const

const inputStyle = {
  background: "var(--bg-input)",
  border: "1px solid var(--border)",
  color: "var(--text-primary)",
}

/** Split-only pane. Feature, tracking, and resource controls live elsewhere. */
export function SplitAndMetricsConfig({
  columns,
  split,
  onSplitUpdate,
}: SplitAndMetricsConfigProps) {
  const strategy = configField(split, "strategy", "random")
  const validationSize = configField(split, "validation_size", 0.2)
  const holdoutSize = configField(split, "holdout_size", 0)
  const trainSize = Math.max(0, 1 - validationSize - holdoutSize)

  return (
    <section aria-labelledby="split-strategy-heading">
      <h3
        id="split-strategy-heading"
        className="text-[11px] font-bold uppercase tracking-[0.08em]"
        style={{ color: "var(--text-muted)" }}
      >
        Split Strategy
      </h3>
      <div className="mt-1.5 space-y-2">
        <div className="flex gap-2">
          {STRATEGIES.map((value) => {
            const active = strategy === value
            return (
              <button
                type="button"
                key={value}
                aria-pressed={active}
                onClick={() => onSplitUpdate("strategy", value)}
                className="rounded-md px-3 py-1 text-xs font-medium transition-colors"
                style={{
                  background: active
                    ? "var(--accent-soft)"
                    : "var(--bg-input)",
                  color: active
                    ? "var(--accent)"
                    : "var(--text-secondary)",
                  border: `1px solid ${
                    active ? "var(--accent)" : "var(--border)"
                  }`,
                }}
              >
                {value}
              </button>
            )
          })}
        </div>

        {strategy === "random" && (
          <div className="space-y-2">
            <div className="grid grid-cols-3 gap-2">
              <label className="text-[11px]" style={{ color: "var(--text-muted)" }}>
                Validation
                <input
                  aria-label="Validation"
                  type="number"
                  step={0.05}
                  min={0}
                  max={0.5}
                  value={validationSize}
                  onChange={(event) =>
                    onSplitUpdate(
                      "validation_size",
                      Number.parseFloat(event.target.value) || 0,
                    )
                  }
                  className="mt-0.5 w-full rounded px-2 py-1 font-mono text-xs"
                  style={inputStyle}
                />
              </label>
              <label className="text-[11px]" style={{ color: "var(--text-muted)" }}>
                Holdout
                <input
                  aria-label="Holdout"
                  type="number"
                  step={0.05}
                  min={0}
                  max={0.5}
                  value={holdoutSize}
                  onChange={(event) =>
                    onSplitUpdate(
                      "holdout_size",
                      Number.parseFloat(event.target.value) || 0,
                    )
                  }
                  className="mt-0.5 w-full rounded px-2 py-1 font-mono text-xs"
                  style={inputStyle}
                />
              </label>
              <label className="text-[11px]" style={{ color: "var(--text-muted)" }}>
                Seed
                <input
                  aria-label="Seed"
                  type="number"
                  value={configField(split, "seed", 42)}
                  onChange={(event) =>
                    onSplitUpdate(
                      "seed",
                      safeParseInt(event.target.value, 42),
                    )
                  }
                  className="mt-0.5 w-full rounded px-2 py-1 font-mono text-xs"
                  style={inputStyle}
                />
              </label>
            </div>
            <div
              aria-label="Split allocation"
              className="flex h-1.5 gap-0.5 overflow-hidden rounded-full"
              style={{ background: "var(--chrome-hover)" }}
            >
              <div
                style={{
                  width: `${trainSize * 100}%`,
                  background: CHART_COLORS.train,
                }}
                title={`Train: ${(trainSize * 100).toFixed(0)}%`}
              />
              {validationSize > 0 && (
                <div
                  style={{
                    width: `${validationSize * 100}%`,
                    background: CHART_COLORS.lambdaChange,
                  }}
                  title={`Validation: ${(validationSize * 100).toFixed(0)}%`}
                />
              )}
              {holdoutSize > 0 && (
                <div
                  style={{
                    width: `${holdoutSize * 100}%`,
                    background: "var(--signif-high)",
                  }}
                  title={`Holdout: ${(holdoutSize * 100).toFixed(0)}%`}
                />
              )}
            </div>
          </div>
        )}

        {strategy === "temporal" && (
          <div className="space-y-2">
            <label className="block text-[11px]" style={{ color: "var(--text-muted)" }}>
              Date column
              <select
                aria-label="Date column"
                value={configField(split, "date_column", "")}
                onChange={(event) =>
                  onSplitUpdate("date_column", event.target.value)
                }
                className="mt-0.5 w-full rounded-lg px-2.5 py-1.5 font-mono text-xs"
                style={inputStyle}
              >
                <option value="">Select...</option>
                {columns.map((column) => (
                  <option key={column.name} value={column.name}>
                    {column.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="block text-[11px]" style={{ color: "var(--text-muted)" }}>
              Cutoff date
              <input
                aria-label="Cutoff date"
                type="date"
                value={configField(split, "cutoff_date", "")}
                onChange={(event) =>
                  onSplitUpdate("cutoff_date", event.target.value)
                }
                className="mt-0.5 w-full rounded-lg px-2.5 py-1.5 font-mono text-xs"
                style={inputStyle}
              />
            </label>
          </div>
        )}

        {strategy === "group" && (
          <div className="space-y-2">
            <label className="block text-[11px]" style={{ color: "var(--text-muted)" }}>
              Group column
              <select
                aria-label="Group column"
                value={configField(split, "group_column", "")}
                onChange={(event) =>
                  onSplitUpdate("group_column", event.target.value)
                }
                className="mt-0.5 w-full rounded-lg px-2.5 py-1.5 font-mono text-xs"
                style={inputStyle}
              >
                <option value="">Select...</option>
                {columns.map((column) => (
                  <option key={column.name} value={column.name}>
                    {column.name}
                  </option>
                ))}
              </select>
            </label>
            <div className="grid grid-cols-2 gap-2">
              <label className="text-[11px]" style={{ color: "var(--text-muted)" }}>
                Validation
                <input
                  aria-label="Validation"
                  type="number"
                  step={0.05}
                  min={0}
                  max={0.5}
                  value={validationSize}
                  onChange={(event) =>
                    onSplitUpdate(
                      "validation_size",
                      Number.parseFloat(event.target.value) || 0,
                    )
                  }
                  className="mt-0.5 w-full rounded px-2 py-1 font-mono text-xs"
                  style={inputStyle}
                />
              </label>
              <label className="text-[11px]" style={{ color: "var(--text-muted)" }}>
                Holdout
                <input
                  aria-label="Holdout"
                  type="number"
                  step={0.05}
                  min={0}
                  max={0.5}
                  value={holdoutSize}
                  onChange={(event) =>
                    onSplitUpdate(
                      "holdout_size",
                      Number.parseFloat(event.target.value) || 0,
                    )
                  }
                  className="mt-0.5 w-full rounded px-2 py-1 font-mono text-xs"
                  style={inputStyle}
                />
              </label>
            </div>
          </div>
        )}
      </div>
    </section>
  )
}
