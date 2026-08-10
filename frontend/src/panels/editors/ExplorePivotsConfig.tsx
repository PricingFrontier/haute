import { useState } from "react"
import { ArrowLeft, Plus, SlidersHorizontal, Table2 } from "lucide-react"

import { NODE_GROUP_COLORS } from "../../theme/colors"
import { withAlpha } from "../../utils/color"
import {
  explorePivotLabel,
  nextExplorePivotId,
  parseExplorePivots,
} from "../explore/pivotConfig"
import type { OnUpdateConfig } from "./_shared"

type ExplorePivotsConfigProps = {
  config: Record<string, unknown>
  onUpdate: OnUpdateConfig
}

export default function ExplorePivotsConfig({ config, onUpdate }: ExplorePivotsConfigProps) {
  const [configuredPivotId, setConfiguredPivotId] = useState<string | null>(null)
  const parsed = parseExplorePivots(config)

  if (!parsed.ok) {
    return (
      <div data-testid="explore-pivots-config" className="px-4 py-3">
        <div
          role="alert"
          className="rounded-lg px-3 py-2 text-xs leading-relaxed"
          style={{
            color: "var(--danger)",
            background: "var(--danger-soft)",
            border: "1px solid var(--danger-border)",
          }}
        >
          {parsed.error}
        </div>
      </div>
    )
  }

  const { pivots } = parsed
  const configuredIndex = configuredPivotId
    ? pivots.findIndex((pivot) => pivot.id === configuredPivotId)
    : -1

  if (configuredIndex >= 0) {
    const label = explorePivotLabel(configuredIndex)
    return (
      <div data-testid="explore-pivots-config" className="px-4 py-3 flex flex-col gap-4">
        <button
          type="button"
          onClick={() => setConfiguredPivotId(null)}
          className="focus-ring self-start inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs font-semibold hover:bg-[var(--bg-hover)]"
          style={{
            color: "var(--text-secondary)",
            ["--focus-ring-border" as string]: withAlpha(NODE_GROUP_COLORS.explore, 0.3),
            ["--focus-ring-shadow" as string]: withAlpha(NODE_GROUP_COLORS.explore, 0.1),
          }}
        >
          <ArrowLeft size={13} aria-hidden="true" />
          Back to pivots
        </button>

        <div>
          <h3 className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>
            Configure {label}
          </h3>
          <p className="mt-1 text-[11px] leading-relaxed" style={{ color: "var(--text-muted)" }}>
            Pivot settings will be added here.
          </p>
        </div>

        <div
          className="flex min-h-28 flex-col items-center justify-center gap-2 rounded-lg px-4 py-6 text-center"
          style={{ background: "var(--bg-input)", border: "1px dashed var(--border)" }}
        >
          <SlidersHorizontal size={20} aria-hidden="true" style={{ color: NODE_GROUP_COLORS.explore }} />
          <span className="text-[11px]" style={{ color: "var(--text-muted)" }}>
            Rows, columns, values, and aggregation controls are coming next.
          </span>
        </div>
      </div>
    )
  }

  const addPivot = () => {
    onUpdate("pivots", [...pivots, { id: nextExplorePivotId(pivots) }])
  }

  return (
    <div data-testid="explore-pivots-config" className="px-4 py-3 flex flex-col gap-3">
      <div className="flex items-center justify-between gap-3">
        <div>
          <div
            className="text-[11px] font-bold uppercase tracking-[0.08em]"
            style={{ color: "var(--text-secondary)" }}
          >
            Pivots
          </div>
          <div className="mt-0.5 text-[10px]" style={{ color: "var(--text-muted)" }}>
            Add pivot layouts and configure their fields.
          </div>
        </div>
        <button
          type="button"
          onClick={addPivot}
          className="focus-ring inline-flex shrink-0 items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs font-semibold hover:brightness-105"
          style={{
            color: "var(--text-on-accent)",
            background: NODE_GROUP_COLORS.explore,
            ["--focus-ring-border" as string]: withAlpha(NODE_GROUP_COLORS.explore, 0.35),
            ["--focus-ring-shadow" as string]: withAlpha(NODE_GROUP_COLORS.explore, 0.15),
          }}
        >
          <Plus size={13} aria-hidden="true" />
          Add Pivot
        </button>
      </div>

      {pivots.length === 0 ? (
        <div
          className="rounded-lg px-3 py-5 text-center text-xs"
          style={{ color: "var(--text-muted)", background: "var(--bg-input)", border: "1px dashed var(--border)" }}
        >
          No pivots yet. Add one to start defining a pivot layout.
        </div>
      ) : (
        <div className="flex flex-col gap-2">
          {pivots.map((pivot, index) => {
            const label = explorePivotLabel(index)
            return (
              <div
                key={pivot.id}
                role="group"
                aria-label={label}
                className="flex items-center overflow-hidden rounded-lg"
                style={{ background: "var(--bg-input)", border: "1px solid var(--border)" }}
              >
                <div className="flex min-w-0 flex-1 items-center gap-2.5 px-3 py-2">
                  <span
                    className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md"
                    style={{
                      color: NODE_GROUP_COLORS.explore,
                      background: withAlpha(NODE_GROUP_COLORS.explore, 0.12),
                    }}
                  >
                    <Table2 size={15} aria-hidden="true" />
                  </span>
                  <span className="min-w-0 flex-1">
                    <span
                      className="block truncate text-xs font-semibold"
                      style={{ color: "var(--text-primary)" }}
                    >
                      {label}
                    </span>
                    <span className="mt-0.5 block text-[10px]" style={{ color: "var(--text-muted)" }}>
                      Ready to configure
                    </span>
                  </span>
                </div>
                <button
                  type="button"
                  aria-label={`Configure ${label}`}
                  onClick={() => setConfiguredPivotId(pivot.id)}
                  className="focus-ring m-1.5 inline-flex shrink-0 items-center gap-1 rounded-md px-2 py-1 text-[11px] font-semibold hover:bg-[var(--bg-hover)]"
                  style={{
                    color: "var(--text-secondary)",
                    border: "1px solid var(--border)",
                    ["--focus-ring-border" as string]: withAlpha(NODE_GROUP_COLORS.explore, 0.3),
                    ["--focus-ring-shadow" as string]: withAlpha(NODE_GROUP_COLORS.explore, 0.1),
                  }}
                >
                  <SlidersHorizontal size={11} aria-hidden="true" />
                  Configure
                </button>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
