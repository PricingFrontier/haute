import type { ReactNode } from "react"
import { NODE_TYPE_META, SINGLETON_TYPES } from "../utils/nodeTypes"
import type { NodeTypeValue } from "../utils/nodeTypes"

/**
 * Type-description tooltip card (tooltips-descriptions design §3.3).
 *
 * Renders TYPE-level metadata from NODE_TYPE_META only — it never reads
 * `node.data`. Per-node-INSTANCE descriptions are interop item 6; when they
 * land they go in the `footer` slot below the divider. The contract:
 * everything above the divider is type-static and haute-controlled,
 * everything below is instance/user content.
 */

/**
 * Constraint-note copy per `maxInputs` value. Derived from the numeric
 * value, not a hardcoded type list — a new maxInputs value with no entry
 * here fails the meta gate in nodeTypes.test.ts rather than silently
 * rendering no note.
 */
const MAX_INPUTS_NOTES: Record<number, string> = {
  1: "Single input.",
  2: "Two inputs: base + join.",
}

export default function NodeTypeTooltip({
  type,
  singletonBlocked = false,
  footer,
}: {
  type: NodeTypeValue
  singletonBlocked?: boolean
  /** Forward-compat slot for instance content (interop item 6). Unused in this build. */
  footer?: ReactNode
}) {
  const meta = NODE_TYPE_META[type]
  if (!meta) return null
  const Icon = meta.icon
  const description = meta.description.trim()
  const maxInputsNote = meta.maxInputs !== undefined ? MAX_INPUTS_NOTES[meta.maxInputs] : undefined
  const isSingleton = SINGLETON_TYPES.has(type)

  return (
    <div data-node-type={type} className="flex flex-col gap-1">
      <div className="flex items-center gap-1.5">
        <Icon size={14} style={{ color: meta.color }} className="shrink-0" />
        <span
          data-testid="node-type-tooltip-name"
          className="text-[12px] font-semibold"
          style={{ color: "var(--text-primary)" }}
        >
          {meta.name}
        </span>
        <span
          className="ml-auto pl-2 text-[9px] font-bold uppercase tracking-[0.08em] shrink-0"
          style={{ color: meta.color }}
        >
          {meta.label}
        </span>
      </div>
      {description !== "" && (
        <p
          data-testid="node-type-tooltip-description"
          className="m-0 text-[11px] leading-snug"
          style={{ color: "var(--text-secondary)" }}
        >
          {meta.description}
        </p>
      )}
      {isSingleton && (
        <p
          data-testid="node-type-tooltip-singleton-note"
          className="m-0 text-[10px]"
          style={{ color: singletonBlocked ? "var(--warning-strong)" : "var(--text-muted)" }}
        >
          {singletonBlocked ? "Already in this pipeline — only one allowed." : "Only one per pipeline."}
        </p>
      )}
      {maxInputsNote !== undefined && (
        <p
          data-testid="node-type-tooltip-maxinputs-note"
          className="m-0 text-[10px]"
          style={{ color: "var(--text-muted)" }}
        >
          {maxInputsNote}
        </p>
      )}
      {footer !== undefined && footer !== null && (
        <div
          data-testid="node-type-tooltip-footer"
          className="mt-0.5 pt-1"
          style={{ borderTop: "1px solid var(--border)" }}
        >
          {footer}
        </div>
      )}
    </div>
  )
}
