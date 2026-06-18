/**
 * Read-only config inspector for the side-by-side comparison view (S11).
 *
 * When a node is clicked in either comparison canvas, this panel takes the
 * sidepane slot (displacing the version-control panel) and shows that node's
 * configuration read-only — the "primary focal point for what the pipeline is
 * doing". For a changed node it shows the historical value alongside the current
 * one, key by key, so the actual edit is legible.
 */
import { GitCompareArrows } from "lucide-react"

import PanelShell from "../panels/PanelShell"
import { formatValueCompact } from "../utils/formatValue"
import { nodeTypeLabels } from "../utils/nodeTypes"
import type { ComparisonInspect } from "./ComparisonView"

const STATUS_META: Record<
  ComparisonInspect["status"],
  { label: string; color: string; soft: string }
> = {
  added: { label: "Added", color: "var(--diff-added)", soft: "var(--success-soft)" },
  removed: { label: "Removed", color: "var(--diff-removed)", soft: "var(--danger-soft)" },
  changed: { label: "Changed", color: "var(--diff-changed)", soft: "var(--warning-soft)" },
  unchanged: { label: "Unchanged", color: "var(--text-muted)", soft: "var(--bg-hover)" },
}

/** Render a config value as a readable string (scalars compact, objects JSON). */
function renderValue(v: unknown): string {
  if (v === undefined) return "—"
  if (v === null) return "null"
  if (typeof v === "object") return JSON.stringify(v)
  return formatValueCompact(v)
}

function asRecord(config: unknown): Record<string, unknown> {
  return config && typeof config === "object" && !Array.isArray(config)
    ? (config as Record<string, unknown>)
    : {}
}

interface ComparisonInspectorProps {
  inspect: ComparisonInspect
  onClose: () => void
}

export default function ComparisonInspector({ inspect, onClose }: ComparisonInspectorProps) {
  const meta = STATUS_META[inspect.status]
  const facet = inspect.current ?? inspect.historical
  const label = facet?.label ?? inspect.id
  const nodeType = facet?.nodeType ?? ""
  const typeLabel = nodeTypeLabels[nodeType] ?? nodeType

  const currentConfig = asRecord(inspect.current?.config)
  const historicalConfig = asRecord(inspect.historical?.config)
  const keys = Array.from(
    new Set([...Object.keys(historicalConfig), ...Object.keys(currentConfig)]),
  ).sort()

  return (
    <PanelShell
      testId="comparison-inspector"
      title={label}
      onClose={onClose}
      maxWidth={520}
      icon={<GitCompareArrows size={14} style={{ color: "var(--accent)" }} />}
      subtitle={
        <span className="flex items-center gap-2">
          {typeLabel && (
            <span
              className="text-[10px] font-bold uppercase tracking-[0.08em]"
              style={{ color: "var(--text-muted)" }}
            >
              {typeLabel}
            </span>
          )}
          <span
            data-testid="comparison-inspector-status"
            className="text-[10px] font-semibold px-1.5 py-0.5 rounded"
            style={{ color: meta.color, background: meta.soft }}
          >
            {meta.label}
          </span>
        </span>
      }
    >
      <div className="flex-1 min-h-0 overflow-y-auto px-3 py-2">
        <div className="flex items-center gap-1.5 mb-2 px-1">
          <span
            className="text-[11px] font-medium uppercase tracking-wider"
            style={{ color: "var(--text-muted)" }}
          >
            Configuration
          </span>
          <span className="text-[11px]" style={{ color: "var(--text-muted)" }}>
            (read-only)
          </span>
        </div>

        {keys.length === 0 ? (
          <p data-testid="comparison-inspector-empty" className="text-[12px] px-1" style={{ color: "var(--text-muted)" }}>
            No configuration on this node.
          </p>
        ) : (
          <div className="flex flex-col gap-1.5">
            {keys.map((key) => {
              const oldV = historicalConfig[key]
              const newV = currentConfig[key]
              const onlyOld = !(key in currentConfig)
              const onlyNew = !(key in historicalConfig)
              const changed =
                !onlyOld && !onlyNew && JSON.stringify(oldV) !== JSON.stringify(newV)
              return (
                <div
                  key={key}
                  data-testid="comparison-inspector-row"
                  data-changed={changed || onlyOld || onlyNew || undefined}
                  className="rounded-md px-2 py-1.5"
                  style={{
                    background:
                      changed || onlyOld || onlyNew ? "var(--warning-soft)" : "var(--bg-hover)",
                  }}
                >
                  <div
                    className="text-[10px] font-mono mb-0.5"
                    style={{ color: "var(--text-muted)" }}
                  >
                    {key}
                  </div>
                  {/* For a genuine change, show old → new; otherwise the value. */}
                  {changed || onlyOld ? (
                    <div className="flex flex-col gap-0.5">
                      {!onlyNew && (
                        <span
                          className="text-[12px] font-mono break-all line-through"
                          style={{ color: "var(--text-muted)" }}
                        >
                          {renderValue(oldV)}
                        </span>
                      )}
                      {!onlyOld && (
                        <span
                          className="text-[12px] font-mono break-all"
                          style={{ color: "var(--text-primary)" }}
                        >
                          {renderValue(newV)}
                        </span>
                      )}
                    </div>
                  ) : (
                    <span
                      className="text-[12px] font-mono break-all"
                      style={{ color: "var(--text-primary)" }}
                    >
                      {renderValue(onlyNew ? newV : newV ?? oldV)}
                    </span>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </div>
    </PanelShell>
  )
}
