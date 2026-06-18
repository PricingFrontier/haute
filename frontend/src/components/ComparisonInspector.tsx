/**
 * Read-only config inspector for the side-by-side comparison view (S11).
 *
 * When a node is clicked in either comparison canvas, this panel takes the
 * sidepane slot (displacing the version-control panel) and shows that node's
 * REAL config editor read-only — the "primary focal point for what the pipeline
 * is doing". The editor is wrapped `inert` so it can't be interacted with; a
 * pipeline selector at the top says (and, for a node present on both sides, lets
 * you switch) which version's config you're looking at.
 */
import { useState } from "react"
import { GitCompareArrows } from "lucide-react"

import PanelShell from "../panels/PanelShell"
import { nodeTypeLabels } from "../utils/nodeTypes"
import ReadOnlyNodeConfig from "./ReadOnlyNodeConfig"
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

type View = "current" | "historical"

interface ComparisonInspectorProps {
  inspect: ComparisonInspect
  onClose: () => void
}

export default function ComparisonInspector({ inspect, onClose }: ComparisonInspectorProps) {
  const meta = STATUS_META[inspect.status]
  const bothSides = !!inspect.current && !!inspect.historical
  const [view, setView] = useState<View>(inspect.current ? "current" : "historical")

  const facet =
    (view === "current" ? inspect.current : inspect.historical) ??
    inspect.current ??
    inspect.historical
  const label = facet?.label ?? inspect.id
  const nodeType = facet?.nodeType ?? ""
  const typeLabel = nodeTypeLabels[nodeType] ?? nodeType
  const config = (facet?.config ?? {}) as Record<string, unknown>

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
      {/* Which version's config is shown. A node on both sides gets a switcher;
          an added/removed node shows the single available version as a label. */}
      <div
        className="shrink-0 flex items-center gap-2 px-3 py-2"
        style={{ borderBottom: "1px solid var(--border)" }}
      >
        {bothSides ? (
          <div
            className="inline-flex rounded-md overflow-hidden"
            style={{ border: "1px solid var(--border)" }}
          >
            {(["historical", "current"] as const).map((v) => {
              const active = view === v
              return (
                <button
                  key={v}
                  data-testid={`comparison-inspector-view-${v}`}
                  data-active={active || undefined}
                  onClick={() => setView(v)}
                  className="px-2.5 py-1 text-[11px] font-medium transition-colors"
                  style={
                    active
                      ? { background: "var(--accent-soft)", color: "var(--accent)" }
                      : { background: "transparent", color: "var(--text-secondary)" }
                  }
                >
                  {v === "historical" ? "Historical" : "Current"}
                </button>
              )
            })}
          </div>
        ) : (
          <span
            data-testid="comparison-inspector-only"
            className="text-[11px] font-medium px-1"
            style={{ color: "var(--text-secondary)" }}
          >
            {inspect.current ? "Current pipeline" : "Historical version"}
          </span>
        )}
        <span className="text-[11px] ml-auto" style={{ color: "var(--text-muted)" }}>
          read-only
        </span>
      </div>

      {/* The real editor, made non-interactive. `inert` blocks focus/clicks for
          the whole subtree; ReadOnlyNodeConfig also passes no-op handlers. */}
      <div className="flex-1 min-h-0 overflow-y-auto" inert data-testid="comparison-inspector-config">
        <ReadOnlyNodeConfig nodeType={nodeType} config={config} nodeId={inspect.id} />
      </div>
    </PanelShell>
  )
}
