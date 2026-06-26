import { useState } from "react"
import { ChevronDown, ChevronRight } from "lucide-react"
import ModalShell from "./ModalShell"
import { withAlpha } from "../utils/color"
import type { InheritGroup } from "../panels/editors/apiInputInherit"

/**
 * The one shared dialog behind inherit, inherit-attributes, and the key-pick step
 * of cascade. It shows candidate keys grouped into collapsible per-level sections
 * — each candidate a checkbox with its name, source path, and type — and returns
 * the selected paths on confirm. Keys already present on the target (by path) are
 * shown ticked and disabled. The dialog is presentational: the caller supplies
 * the groups and decides what "confirm" does (add to a frame, cascade, …).
 *
 * `ModalShell` already provides the backdrop, focus trap, and close-on-Escape /
 * close-on-click-outside, so this component only owns the collapse and selection
 * state.
 */
export interface KeyPickerModalProps {
  /** Dialog title, e.g. "Inherit keys" / "Cascade keys". */
  title: string
  /** The frame the keys are being added to / from, named in the subtitle. */
  targetLabel: string
  accentColor: string
  groups: InheritGroup[]
  /** Paths already on the target — rendered checked + disabled. */
  existingPaths: ReadonlySet<string>
  /** Builds the primary-button label from the live selection count. */
  confirmLabel?: (count: number) => string
  onConfirm: (paths: string[]) => void
  onClose: () => void
}

export default function KeyPickerModal({
  title,
  targetLabel,
  accentColor,
  groups,
  existingPaths,
  confirmLabel = (n) => `Add ${n}`,
  onConfirm,
  onClose,
}: KeyPickerModalProps) {
  // Default expanded — collapse is opt-in to tame deep cases.
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set())
  const [selected, setSelected] = useState<Set<string>>(new Set())

  const toggleCollapse = (ancestorPath: string) =>
    setCollapsed((prev) => {
      const next = new Set(prev)
      if (next.has(ancestorPath)) next.delete(ancestorPath)
      else next.add(ancestorPath)
      return next
    })

  const toggleSelect = (path: string) =>
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(path)) next.delete(path)
      else next.add(path)
      return next
    })

  const count = selected.size
  const hasCandidates = groups.some((g) => g.candidates.length > 0)

  return (
    <ModalShell ariaLabel={title} onClose={onClose} width="w-[480px]" testId="key-picker-modal">
      {/* Header */}
      <div className="px-4 py-3" style={{ borderBottom: "1px solid var(--border)" }}>
        <h2 className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>
          {title}
        </h2>
        <p className="text-[11px] mt-0.5 font-mono" style={{ color: "var(--text-muted)" }}>
          {targetLabel}
        </p>
      </div>

      {/* Body — collapsible groups of candidate keys */}
      <div className="max-h-[400px] overflow-y-auto px-2 py-2 space-y-1">
        {!hasCandidates && (
          <div className="text-xs italic px-2 py-3" style={{ color: "var(--text-muted)" }}>
            No keys available to add here.
          </div>
        )}
        {groups.map((group) => {
          const isCollapsed = collapsed.has(group.ancestorPath)
          return (
            <div key={group.ancestorPath} data-testid={`key-picker-group-${group.ancestorPath}`}>
              <button
                type="button"
                onClick={() => toggleCollapse(group.ancestorPath)}
                className="w-full flex items-center gap-1 px-1.5 py-1 rounded text-[11px] font-semibold"
                style={{ color: "var(--text-secondary)" }}
              >
                {isCollapsed ? (
                  <ChevronRight size={12} style={{ color: "var(--text-muted)" }} />
                ) : (
                  <ChevronDown size={12} style={{ color: "var(--text-muted)" }} />
                )}
                <span>{group.ancestorLabel}</span>
                <span className="font-mono font-normal" style={{ color: "var(--text-muted)" }}>
                  {group.ancestorPath}
                </span>
              </button>
              {!isCollapsed && (
                <div className="pl-4 space-y-0.5">
                  {group.candidates.map((cand) => {
                    const present = existingPaths.has(cand.path)
                    const checked = present || selected.has(cand.path)
                    return (
                      <label
                        key={cand.path}
                        data-testid={`key-picker-candidate-${cand.path}`}
                        className="flex items-center gap-2 px-1.5 py-1 rounded text-[11px] cursor-pointer"
                        style={present ? { opacity: 0.5 } : { background: withAlpha(accentColor, 0.04) }}
                      >
                        <input
                          type="checkbox"
                          checked={checked}
                          disabled={present}
                          onChange={() => toggleSelect(cand.path)}
                        />
                        <span className="font-mono" style={{ color: "var(--text-primary)" }}>
                          {cand.name}
                        </span>
                        <span
                          className="font-mono flex-1 min-w-0 truncate"
                          style={{ color: "var(--text-muted)" }}
                        >
                          {cand.path}
                        </span>
                        <span className="shrink-0" style={{ color: "var(--text-muted)" }}>
                          {cand.type}
                        </span>
                      </label>
                    )
                  })}
                </div>
              )}
            </div>
          )
        })}
      </div>

      {/* Footer */}
      <div
        className="px-4 py-3 flex gap-2 justify-end"
        style={{ borderTop: "1px solid var(--border)" }}
      >
        <button
          type="button"
          data-testid="key-picker-cancel"
          onClick={onClose}
          className="text-xs font-semibold px-3 py-1.5 rounded"
          style={{ color: "var(--text-secondary)" }}
        >
          Cancel
        </button>
        <button
          type="button"
          data-testid="key-picker-confirm"
          disabled={count === 0}
          onClick={() => onConfirm([...selected])}
          className="text-xs font-semibold px-3 py-1.5 rounded disabled:opacity-50 disabled:cursor-not-allowed"
          style={{ background: accentColor, color: "var(--text-on-accent)" }}
        >
          {confirmLabel(count)}
        </button>
      </div>
    </ModalShell>
  )
}
