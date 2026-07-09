import { useState } from "react"
import { ChevronDown, ChevronRight } from "lucide-react"

/**
 * The frames table at the top of the API Input editor: one row per frame
 * (label, path, emit, column count), the surface the cascade and
 * inherit/inherit-attributes operations work from. Modelled on the OUTPUT
 * editor's collapsible "Frames (N)" table; starts collapsed, and the parent
 * hides it entirely while there are no frames yet.
 *
 * Presentational: the editor supplies one {@link FramesTableRow} of facts per
 * frame and owns what the three entry points do. A frame whose path is invalid
 * (blank or failing the grammar) still gets its row — a persisted entry must
 * surface (render-gate) — shown greyed with the failure named, and its
 * inherit/cascade entry points disabled, since a frame with no position in the
 * JSON tree can take part in neither direction.
 */
export interface FramesTableRow {
  label: string
  path: string
  emit: boolean
  columnCount: number
  /** Columns on this frame that are structurally incomplete or fail path
   * validation — surfaced in the count so the row never disagrees with what
   * opening the frame shows. */
  invalidColumnCount: number
  /** The failure keeping this frame out of inherit/cascade (blank or
   * ungrammatical path), or null when the frame is sound. */
  pathError: string | null
  /** Whether any inventory key sits at a shallower level on this frame's
   * branch — a top-level frame simply shows no inherit affordance. */
  canInherit: boolean
}

export default function FramesTable({
  rows,
  accentColor,
  disabled = false,
  disabledReason,
  onCascade,
  onInherit,
  onAddKeys,
}: {
  rows: FramesTableRow[]
  accentColor: string
  /** True while the replace-tables confirmation gate is open — all three
   * entry points are disabled rather than mutating tables mid-decision. */
  disabled?: boolean
  disabledReason?: string
  onCascade: () => void
  onInherit: (frameIdx: number) => void
  onAddKeys: (frameIdx: number) => void
}) {
  const [expanded, setExpanded] = useState(false)

  return (
    <div
      data-testid="api-input-frames-table"
      className="rounded-md"
      style={{ border: "1px solid var(--border)", background: "var(--bg-elevated)" }}
    >
      <div className="flex items-center justify-between gap-2 px-2 py-2">
        <button
          type="button"
          data-testid="api-input-frames-toggle"
          onClick={() => setExpanded((open) => !open)}
          className="flex items-center gap-1 flex-1 min-w-0 text-left"
          title={expanded ? "Collapse frames" : "Show frames"}
        >
          {expanded ? (
            <ChevronDown size={14} style={{ color: "var(--text-muted)" }} className="shrink-0" />
          ) : (
            <ChevronRight size={14} style={{ color: "var(--text-muted)" }} className="shrink-0" />
          )}
          <span className="text-[11px] font-semibold" style={{ color: "var(--text-muted)" }}>
            Frames ({rows.length})
          </span>
        </button>
        <button
          type="button"
          data-testid="api-input-cascade-btn"
          onClick={onCascade}
          disabled={disabled}
          title={disabled ? disabledReason : "Push keys into every deeper frame on their branch"}
          className="text-[11px] font-semibold px-2 py-0.5 rounded disabled:opacity-50 disabled:cursor-not-allowed"
          style={{ color: accentColor, border: `1px solid ${accentColor}` }}
        >
          Cascade keys
        </button>
      </div>

      {expanded && (
        <div
          data-testid="api-input-frames-rows"
          className="px-2 pb-2 pt-1.5 space-y-1"
          style={{ borderTop: "1px solid var(--border)" }}
        >
          {rows.map((row, fi) => (
            <div
              key={fi}
              data-testid={`api-input-frames-row-${fi}`}
              className="rounded p-1.5 space-y-0.5"
              style={{
                border: "1px solid var(--border)",
                background: "var(--bg-input)",
                opacity: row.pathError !== null ? 0.6 : 1,
              }}
            >
              <div className="flex items-center gap-2 text-[11px]">
                <span
                  className="font-mono font-semibold truncate"
                  style={{ color: "var(--text-primary)" }}
                >
                  {row.label || "(unnamed)"}
                </span>
                <span
                  className="font-mono flex-1 min-w-0 truncate"
                  style={{
                    color: row.pathError !== null ? "var(--danger-text)" : "var(--text-muted)",
                  }}
                >
                  {row.path || "(no path)"}
                </span>
                <span className="shrink-0" style={{ color: "var(--text-muted)" }}>
                  {row.emit ? "emit" : "—"}
                </span>
                <span
                  className="shrink-0"
                  data-testid={`api-input-frames-row-${fi}-count`}
                  style={{
                    color:
                      row.invalidColumnCount > 0 ? "var(--danger-text)" : "var(--text-muted)",
                  }}
                >
                  {row.columnCount} col{row.columnCount === 1 ? "" : "s"}
                  {row.invalidColumnCount > 0 ? `, ${row.invalidColumnCount} invalid` : ""}
                </span>
                <span className="shrink-0 flex items-center gap-1">
                  {row.canInherit && (
                    <button
                      type="button"
                      data-testid={`api-input-frames-row-${fi}-inherit`}
                      onClick={() => onInherit(fi)}
                      disabled={disabled || row.pathError !== null}
                      title={
                        disabled
                          ? disabledReason
                          : "Pull a key from a shallower level onto this frame"
                      }
                      className="text-[10px] font-semibold px-1.5 py-0.5 rounded disabled:opacity-50 disabled:cursor-not-allowed"
                      style={{ color: accentColor, border: `1px solid ${accentColor}` }}
                    >
                      Inherit
                    </button>
                  )}
                  <button
                    type="button"
                    data-testid={`api-input-frames-row-${fi}-add-keys`}
                    onClick={() => onAddKeys(fi)}
                    disabled={disabled || row.pathError !== null}
                    title={
                      disabled
                        ? disabledReason
                        : row.pathError !== null
                          ? `This frame's path is invalid, so keys cannot be placed on it: ${row.pathError}`
                          : "Add keys from the inventory, or enter a field by hand"
                    }
                    className="text-[10px] font-semibold px-1.5 py-0.5 rounded disabled:opacity-50 disabled:cursor-not-allowed"
                    style={{ color: "var(--text-secondary)", border: "1px solid var(--border)" }}
                  >
                    Add keys
                  </button>
                </span>
              </div>
              {row.pathError !== null && (
                <div
                  data-testid={`api-input-frames-row-${fi}-path-error`}
                  className="px-1.5 py-0.5 rounded text-[10px] leading-snug"
                  style={{ background: "var(--danger-soft)", color: "var(--danger-text)" }}
                >
                  {row.pathError}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
