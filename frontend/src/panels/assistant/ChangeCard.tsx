import { AlertTriangle, GitCommitHorizontal } from "lucide-react"

import type { AssistantChangeNode, AssistantChangeRecord } from "../../api/assistant"

const CHANGE_LABELS: Record<AssistantChangeNode["change"], string> = {
  added: "Added",
  changed: "Changed",
  removed: "Removed",
  renamed: "Renamed",
}

const CHANGE_COLORS: Record<AssistantChangeNode["change"], string> = {
  added: "var(--success)",
  changed: "var(--accent)",
  removed: "var(--danger-text)",
  renamed: "var(--accent)",
}

/** The length of the abbreviated commit id the card shows. */
const SHORT_SHA = 7

function words(kind: string): string {
  return kind.replaceAll("_", " ")
}

function stepLine(node: AssistantChangeNode): string | null {
  if (node.steps === null) return null
  const kinds = node.steps.length === 0 ? "no steps" : node.steps.map(words).join(", ")
  const changed = node.steps_changed > 0
    ? ` (${node.steps_changed} ${node.steps_changed === 1 ? "step" : "steps"} changed)`
    : ""
  return `Steps: ${kinds}${changed}`
}

function NodeChip({ node }: { node: AssistantChangeNode }) {
  const steps = stepLine(node)
  return (
    <li
      data-testid="assistant-change-node"
      data-change={node.change}
      className="rounded px-2 py-1"
      style={{ background: "var(--bg-input)", border: "1px solid var(--border)" }}
    >
      <div className="flex flex-wrap items-baseline gap-x-1.5">
        <span className="font-medium" style={{ color: CHANGE_COLORS[node.change] }}>
          {CHANGE_LABELS[node.change]}
        </span>{" "}
        <span className="font-mono" style={{ color: "var(--text-primary)" }}>{node.id}</span>{" "}
        <span style={{ color: "var(--text-muted)" }}>{node.type}</span>
        {node.renamed_from !== null && (
          <>
            {" "}
            <span style={{ color: "var(--text-muted)" }}>
              from <span className="font-mono">{node.renamed_from}</span>
            </span>
          </>
        )}
      </div>
      {node.fields.length > 0 && (
        <div style={{ color: "var(--text-secondary)" }}>Changed: {node.fields.join(", ")}</div>
      )}
      {steps !== null && <div style={{ color: "var(--text-secondary)" }}>{steps}</div>}
    </li>
  )
}

/**
 * The change card of one saved plan: what the assistant said it did, then what
 * the save actually changed. It renders only what the backend's value-free
 * record holds, so it never shows a configuration value or step code.
 */
export default function ChangeCard({ change }: { change: AssistantChangeRecord }) {
  const { nodes, edges_added: added, edges_removed: removed } = change.changes
  return (
    <section
      data-testid="assistant-change-card"
      aria-label="Saved changes"
      className="rounded-md px-2.5 py-2 text-[11px] space-y-1.5"
      style={{ background: "var(--bg-elevated)", border: "1px solid var(--border)" }}
    >
      <div className="font-medium" style={{ color: "var(--text-primary)" }}>{change.summary}</div>
      {change.assumptions.length > 0 && (
        <div style={{ color: "var(--text-secondary)" }}>
          <div>Assumed:</div>
          <ul className="list-disc pl-4">
            {change.assumptions.map((assumption, index) => (
              <li key={index}>{assumption}</li>
            ))}
          </ul>
        </div>
      )}
      {nodes.length > 0 && (
        <ul className="space-y-1">
          {nodes.map((node) => (
            <NodeChip key={`${node.change}-${node.id}`} node={node} />
          ))}
        </ul>
      )}
      {(added.length > 0 || removed.length > 0) && (
        <ul className="font-mono" style={{ color: "var(--text-secondary)" }}>
          {added.map((edge) => (
            <li key={`added-${edge.source}-${edge.target}`}>
              <span className="font-sans" style={{ color: "var(--success)" }}>Connected </span>
              {edge.source} → {edge.target}
            </li>
          ))}
          {removed.map((edge) => (
            <li key={`removed-${edge.source}-${edge.target}`}>
              <span className="font-sans" style={{ color: "var(--danger-text)" }}>Disconnected </span>
              {edge.source} → {edge.target}
            </li>
          ))}
        </ul>
      )}
      {change.changes.preamble_changed && (
        <div style={{ color: "var(--text-secondary)" }}>The pipeline preamble changed.</div>
      )}
      {change.changes.truncated && (
        <div style={{ color: "var(--text-muted)" }}>
          More changes were saved than this card lists.
        </div>
      )}
      {change.warnings.map((warning, index) => (
        <div
          key={index}
          data-testid="assistant-change-warning"
          className="flex items-start gap-1.5"
          style={{ color: "var(--warning-strong)" }}
        >
          <AlertTriangle size={12} aria-hidden="true" className="mt-px shrink-0" />
          <span className="break-words">{warning}</span>
        </div>
      ))}
      <div className="flex items-center gap-1.5" style={{ color: "var(--text-muted)" }}>
        <GitCommitHorizontal size={12} aria-hidden="true" />
        {change.git_sha === null
          ? <span>Not saved to Git</span>
          : <span>Commit <span className="font-mono">{change.git_sha.slice(0, SHORT_SHA)}</span></span>}
      </div>
    </section>
  )
}
