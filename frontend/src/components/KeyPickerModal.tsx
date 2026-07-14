import { useState } from "react"
import { createPortal } from "react-dom"
import { ChevronDown, ChevronRight } from "lucide-react"
import ModalShell from "./ModalShell"
import { withAlpha } from "../utils/color"
import type { InheritGroup } from "../panels/editors/apiInputInherit"
import type { ColumnType } from "../panels/editors/apiInputSchema"

const MANUAL_TYPES: ColumnType[] = ["int", "float", "str", "bool", "date"]

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
  /** The subset of `existingPaths` already marked as keys — the hand-entry
   * already-exists notice names the key status when it applies. */
  existingKeyPaths?: ReadonlySet<string>
  /** Builds the primary-button label from the live selection count. */
  confirmLabel?: (count: number) => string
  onConfirm: (paths: string[]) => void
  onClose: () => void
  /** When set (inherit-attributes mode), renders the enter-a-field-by-hand
   * section: a path plus a REQUIRED type — a new entry is not complete without
   * both. The path is validated against the target frame; an invalid one shows
   * its error and cannot be added. A path already on the frame (per
   * `existingPaths`) greys the type out — the existing column keeps its name
   * and type — and Add promotes + confirms it instead of duplicating; `type`
   * is passed as null in that case. Adding is immediate (independent of the
   * checkbox selection). */
  manualEntry?: {
    validatePath: (path: string) => string | null
    onAdd: (path: string, type: ColumnType | null) => void
  }
}

export default function KeyPickerModal({
  title,
  targetLabel,
  accentColor,
  groups,
  existingPaths,
  existingKeyPaths,
  confirmLabel = (n) => `Add ${n}`,
  onConfirm,
  onClose,
  manualEntry,
}: KeyPickerModalProps) {
  // Default expanded — collapse is opt-in to tame deep cases.
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set())
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [manualPath, setManualPath] = useState("")
  // No default type — the entry is incomplete until BOTH path and type are set.
  const [manualType, setManualType] = useState<ColumnType | "">("")

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

  // Portal to document.body: this dialog mounts INSIDE the node side pane, and
  // ModalShell's fixed inset-0 overlay is contained by any transformed ancestor
  // — rendering the backdrop over just the pane with the dialog box clipped
  // (the observed sidebar grey-out freeze). At body level, fixed means the
  // viewport again for every mount point.
  return createPortal(
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

      {/* Enter a field by hand (inherit-attributes mode only) */}
      {manualEntry && (() => {
        const trimmed = manualPath.trim()
        const exists = trimmed !== "" && existingPaths.has(trimmed)
        const pathError = trimmed && !exists ? manualEntry.validatePath(trimmed) : null
        // An existing path is addable without a type — its column keeps the
        // name and type it already has; Add promotes + confirms it.
        const addable =
          trimmed !== "" && pathError === null && (exists || manualType !== "")
        return (
          <div
            className="px-4 py-2.5 space-y-1"
            style={{ borderTop: "1px solid var(--border)" }}
            data-testid="key-picker-manual"
          >
            <div className="text-[11px] font-semibold" style={{ color: "var(--text-secondary)" }}>
              Enter a field by hand
            </div>
            <div className="flex items-center gap-2">
              <input
                data-testid="key-picker-manual-path"
                type="text"
                placeholder="$[:].orders[:].currency"
                value={manualPath}
                onChange={(e) => setManualPath(e.target.value)}
                aria-invalid={pathError !== null ? true : undefined}
                className="flex-1 min-w-0 text-[11px] font-mono px-1.5 py-1 rounded"
                style={{
                  background: "var(--bg-input)",
                  border: `1px solid ${pathError !== null ? "var(--danger-border-strong)" : "var(--border)"}`,
                  color: "var(--text-primary)",
                }}
              />
              <select
                data-testid="key-picker-manual-type"
                value={manualType}
                onChange={(e) => setManualType(e.target.value as ColumnType | "")}
                disabled={exists}
                title={exists ? "Field already exists — its name and type are kept" : undefined}
                className="text-[11px] px-1 py-1 rounded disabled:opacity-50 disabled:cursor-not-allowed"
                style={{
                  background: "var(--bg-input)",
                  border: "1px solid var(--border)",
                  // A missing type is the incomplete half of the entry — read as such.
                  color: manualType === "" ? "var(--text-muted)" : "var(--text-primary)",
                }}
              >
                <option value="">type…</option>
                {MANUAL_TYPES.map((t) => (
                  <option key={t} value={t}>
                    {t}
                  </option>
                ))}
              </select>
              <button
                type="button"
                data-testid="key-picker-manual-add"
                disabled={!addable}
                onClick={() => {
                  manualEntry.onAdd(trimmed, exists ? null : (manualType as ColumnType))
                  setManualPath("")
                  setManualType("")
                }}
                className="text-[11px] font-semibold px-2 py-1 rounded disabled:opacity-50 disabled:cursor-not-allowed"
                style={{ background: accentColor, color: "var(--text-on-accent)" }}
              >
                Add
              </button>
              <button
                type="button"
                data-testid="key-picker-manual-add-close"
                disabled={!addable}
                onClick={() => {
                  manualEntry.onAdd(trimmed, exists ? null : (manualType as ColumnType))
                  onClose()
                }}
                title="Add this field and close the dialog"
                className="text-[11px] font-semibold px-2 py-1 rounded disabled:opacity-50 disabled:cursor-not-allowed"
                style={{ color: accentColor, border: `1px solid ${accentColor}` }}
              >
                Add & close
              </button>
            </div>
            {pathError !== null && (
              <div
                data-testid="key-picker-manual-error"
                className="px-1.5 py-0.5 rounded text-[10px] leading-snug"
                style={{ background: "var(--danger-soft)", color: "var(--danger-text)" }}
              >
                {pathError}
              </div>
            )}
            {exists && (
              <div
                data-testid="key-picker-manual-exists"
                className="px-1.5 py-0.5 rounded text-[10px] leading-snug"
                style={{ background: "var(--accent-soft)", color: "var(--accent)" }}
              >
                {existingKeyPaths?.has(trimmed)
                  ? "This field is already added as a key on this frame — Add re-confirms it and keeps its name and type."
                  : "Field already exists on this frame — Add makes it a key (moved into the keys at the top), keeping its name and type."}
              </div>
            )}
          </div>
        )
      })()}

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
    </ModalShell>,
    document.body,
  )
}
