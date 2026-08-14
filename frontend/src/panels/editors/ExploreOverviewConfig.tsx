import type { OnUpdateConfig } from "./_shared"
import ExploreToggleCard from "./ExploreToggleCard"
import {
  OVERVIEW_CARD_DEFINITIONS,
  OVERVIEW_CONFIG_KEYS,
  isOverviewCardEnabled,
  type OverviewCardKey,
} from "../explore/overviewCardDefinitions"
import { readOverview } from "../explore/overviewConfig"

type ExploreOverviewConfigProps = {
  config: Record<string, unknown>
  onUpdate: OnUpdateConfig
}

export default function ExploreOverviewConfig({
  config,
  onUpdate,
}: ExploreOverviewConfigProps) {
  const overview = readOverview(config)
  const enabledByKey = Object.fromEntries(
    OVERVIEW_CARD_DEFINITIONS.map((definition) => [
      definition.key,
      isOverviewCardEnabled(overview, definition),
    ]),
  ) as Record<OverviewCardKey, boolean>

  // Drop the key on disable so the generated .py stays bare - the backend
  // parser drops an empty overview={} for the same reason. Unknown keys in the
  // raw overview block are preserved on every write to keep round-trip stable.
  const rawOverview = config.overview
  const baseOverview: Record<string, unknown> =
    rawOverview && typeof rawOverview === "object" && !Array.isArray(rawOverview)
      ? { ...(rawOverview as Record<string, unknown>) }
      : {}
  const toggleKey = (key: OverviewCardKey, currentlyEnabled: boolean) => {
    const next: Record<string, unknown> = { ...baseOverview }
    for (const configKey of OVERVIEW_CONFIG_KEYS) delete next[configKey]

    for (const definition of OVERVIEW_CARD_DEFINITIONS) {
      const enabled = definition.key === key ? !currentlyEnabled : enabledByKey[definition.key]
      if (enabled) next[definition.key] = true
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
        {OVERVIEW_CARD_DEFINITIONS.map((definition) => (
          <ExploreToggleCard
            key={definition.key}
            enabled={enabledByKey[definition.key]}
            label={definition.label}
            description={definition.description}
            onToggle={() => toggleKey(definition.key, enabledByKey[definition.key])}
          />
        ))}
      </div>
    </div>
  )
}
