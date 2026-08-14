import { Plus, SlidersHorizontal, Trash2 } from "lucide-react"
import type { ReactNode } from "react"

import { NODE_GROUP_COLORS } from "../../theme/colors"
import ExploreToggleCard from "./ExploreToggleCard"

export function ExploreConfigCardListHeader({
  title,
  description,
  addLabel,
  onAdd,
}: {
  title: string
  description: string
  addLabel: string
  onAdd: () => void
}) {
  return (
    <div className="flex items-center justify-between gap-3">
      <div>
        <div
          className="text-[11px] font-bold uppercase tracking-[0.08em]"
          style={{ color: "var(--text-secondary)" }}
        >
          {title}
        </div>
        <div className="mt-0.5 text-[10px]" style={{ color: "var(--text-muted)" }}>
          {description}
        </div>
      </div>
      <button
        type="button"
        onClick={onAdd}
        className="focus-ring inline-flex shrink-0 items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs font-semibold"
        style={{ color: "var(--text-on-accent)", background: NODE_GROUP_COLORS.explore }}
      >
        <Plus size={13} aria-hidden="true" />
        {addLabel}
      </button>
    </div>
  )
}

export function ExploreConfigCardEmptyState({ children }: { children: ReactNode }) {
  return (
    <div
      className="rounded-lg px-3 py-5 text-center text-xs"
      style={{
        color: "var(--text-muted)",
        background: "var(--bg-input)",
        border: "1px dashed var(--border)",
      }}
    >
      {children}
    </div>
  )
}

export function ExploreConfigCard({
  name,
  enabled,
  detail,
  onEnabledChange,
  onConfigure,
  onDelete,
  deleteDisabled = false,
  deleteTitle,
}: {
  name: string
  enabled: boolean
  detail?: ReactNode
  onEnabledChange: (enabled: boolean) => void
  onConfigure: () => void
  onDelete: () => void
  deleteDisabled?: boolean
  deleteTitle?: string
}) {
  return (
    <ExploreToggleCard
      enabled={enabled}
      label={name}
      description={detail}
      onToggle={() => onEnabledChange(!enabled)}
      actions={
        <>
          <button
            type="button"
            aria-label={`Delete ${name}`}
            title={deleteTitle ?? `Delete ${name}`}
            disabled={deleteDisabled}
            onClick={onDelete}
            className="focus-ring inline-flex shrink-0 items-center rounded p-1.5 disabled:cursor-not-allowed disabled:opacity-35"
            style={{ color: "var(--danger)" }}
          >
            <Trash2 size={12} aria-hidden="true" />
          </button>
          <button
            type="button"
            aria-label={`Configure ${name}`}
            onClick={onConfigure}
            className="focus-ring m-1.5 inline-flex shrink-0 items-center gap-1 rounded-md px-2 py-1 text-[11px] font-semibold"
            style={{
              color: "var(--text-secondary)",
              border: "1px solid var(--border)",
            }}
          >
            <SlidersHorizontal size={11} aria-hidden="true" />
            Configure
          </button>
        </>
      }
    />
  )
}
