import type { OnUpdateConfig } from "./_shared"
import { NODE_GROUP_COLORS } from "../../theme/colors"
import { withAlpha } from "../../utils/color"
import { readOverview, type OverviewConfig } from "../explore/overviewConfig"

type ExploreOverviewConfigProps = {
  config: Record<string, unknown>
  onUpdate: OnUpdateConfig
}

type OverviewCardToggleProps = {
  enabled: boolean
  toggleKey: keyof OverviewConfig
  label: string
  description: string
  onToggle: () => void
}

/**
 * Full-box-highlight toggle for an Overview pane card.
 *
 * Renders the entire card row as the checkbox target: explore-pink border and
 * accent-soft background when enabled, neutral input background and border when
 * disabled.
 * Shared by every entry in the Overview cards list.
 */
function OverviewCardToggle({
  enabled,
  toggleKey,
  label,
  description,
  onToggle,
}: OverviewCardToggleProps) {
  const labelId = `explore-overview-toggle-${toggleKey}-label`
  const descriptionId = `explore-overview-toggle-${toggleKey}-description`

  return (
    <button
      type="button"
      role="checkbox"
      aria-checked={enabled}
      aria-labelledby={labelId}
      aria-describedby={descriptionId}
      onClick={onToggle}
      className="focus-ring text-left flex items-center px-3 py-2 rounded-lg cursor-pointer select-none transition-colors hover:brightness-105"
      style={{
        background: enabled ? "var(--accent-soft)" : "var(--bg-input)",
        border: `1px solid ${enabled ? NODE_GROUP_COLORS.explore : "var(--border)"}`,
        ["--focus-ring-border" as string]: withAlpha(NODE_GROUP_COLORS.explore, 0.3),
        ["--focus-ring-shadow" as string]: withAlpha(NODE_GROUP_COLORS.explore, 0.1),
      }}
    >
      <span className="flex-1 min-w-0 flex flex-col gap-0.5">
        <span
          id={labelId}
          className="text-xs font-semibold"
          style={{ color: enabled ? NODE_GROUP_COLORS.explore : "var(--text-primary)" }}
        >
          {label}
        </span>
        <span
          id={descriptionId}
          className="text-[11px] leading-relaxed"
          style={{ color: "var(--text-muted)" }}
        >
          {description}
        </span>
      </span>
    </button>
  )
}

export default function ExploreOverviewConfig({
  config,
  onUpdate,
}: ExploreOverviewConfigProps) {
  const overview = readOverview(config)
  const datasetHeaderEnabled = overview.dataset_header ?? false
  const schemaEnabled = overview.schema ?? false

  // Drop the key on disable so the generated .py stays bare - the backend
  // parser drops an empty overview={} for the same reason. Unknown keys in the
  // raw overview block are preserved on every write to keep round-trip stable.
  const rawOverview = config.overview
  const baseOverview: Record<string, unknown> =
    rawOverview && typeof rawOverview === "object" && !Array.isArray(rawOverview)
      ? { ...(rawOverview as Record<string, unknown>) }
      : {}
  const toggleKey = (key: keyof OverviewConfig, currentlyEnabled: boolean) => {
    const next: Record<string, unknown> = { ...baseOverview }
    if (currentlyEnabled) {
      delete next[key]
    } else {
      next[key] = true
    }
    onUpdate("overview", next)
  }

  return (
    <div
      data-testid="explore-overview-config"
      className="px-4 py-3 flex flex-col gap-3"
    >
      <div
        className="text-[11px] font-bold uppercase tracking-[0.08em]"
        style={{ color: "var(--text-secondary)" }}
      >
        Cards
      </div>

      <div className="flex flex-col gap-2">
        <OverviewCardToggle
          enabled={datasetHeaderEnabled}
          toggleKey="dataset_header"
          label="Dataset header"
          description="Shows row count, columns, source, and last cached time in the Overview pane below."
          onToggle={() => toggleKey("dataset_header", datasetHeaderEnabled)}
        />
        <OverviewCardToggle
          enabled={schemaEnabled}
          toggleKey="schema"
          label="Schema table"
          description="Lists every column with dtype, null %, distinct count, and an example value."
          onToggle={() => toggleKey("schema", schemaEnabled)}
        />
      </div>
    </div>
  )
}
