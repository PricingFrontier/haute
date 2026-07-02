/**
 * Read-only config inspector for the side-by-side comparison view (S11).
 *
 * When a node is clicked in either comparison canvas, this panel takes the
 * sidepane slot (displacing the version-control panel) and shows that node's
 * REAL config editor read-only — the "primary focal point for what the pipeline
 * is doing". The editor is wrapped `inert` so it can't be interacted with. The
 * Historical/Current switcher lives in the header next to the close button (the
 * same place for every node); for an added/removed node the absent side is greyed
 * out rather than hidden, so nothing shifts vertically as you click around.
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
  const [view, setView] = useState<View>(inspect.current ? "current" : "historical")

  // The chosen view may be unavailable for this node (e.g. you were viewing
  // Historical, then clicked an added node) — fall back to whichever side exists.
  const available = (v: View) => (v === "current" ? !!inspect.current : !!inspect.historical)
  const effectiveView: View = available(view) ? view : inspect.current ? "current" : "historical"

  const facet = effectiveView === "current" ? inspect.current : inspect.historical
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
          <span className="text-[10px]" style={{ color: "var(--text-muted)" }}>
            read-only
          </span>
        </span>
      }
      actions={
        // Historical/Current switcher in the header — same place for every node;
        // the absent side is greyed out so the layout never jiggles.
        <div
          className="inline-flex rounded-md overflow-hidden shrink-0"
          style={{ border: "1px solid var(--border)" }}
        >
          {(["historical", "current"] as const).map((v) => {
            const isAvailable = available(v)
            const active = effectiveView === v
            return (
              <button
                key={v}
                data-testid={`comparison-inspector-view-${v}`}
                data-active={active || undefined}
                disabled={!isAvailable}
                onClick={() => isAvailable && setView(v)}
                title={
                  isAvailable
                    ? `Show the ${v} version's config`
                    : `Not present in the ${v} version`
                }
                className="px-2 py-0.5 text-[10px] font-semibold transition-colors disabled:opacity-40 disabled:cursor-default"
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
      }
    >
      {/* The real editor, made non-interactive. `inert` blocks focus/clicks for
          the whole subtree; ReadOnlyNodeConfig also passes no-op handlers. */}
      <div className="flex-1 min-h-0 overflow-y-auto" inert data-testid="comparison-inspector-config">
        <ReadOnlyNodeConfig nodeType={nodeType} config={config} nodeId={inspect.id} />
      </div>
    </PanelShell>
  )
}
