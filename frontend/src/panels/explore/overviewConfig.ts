import {
  OVERVIEW_CONFIG_KEYS,
  type OverviewConfig,
  type OverviewConfigKey,
} from "./overviewCardDefinitions"

export type { OverviewConfig } from "./overviewCardDefinitions"

function readBool(raw: Record<string, unknown>, key: OverviewConfigKey): boolean | undefined {
  const value = raw[key]
  return typeof value === "boolean" ? value : undefined
}

export function readOverview(config: Record<string, unknown>): OverviewConfig {
  const raw = config.overview
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return {}
  const rec = raw as Record<string, unknown>
  const result: OverviewConfig = {}
  for (const key of OVERVIEW_CONFIG_KEYS) {
    const value = readBool(rec, key)
    if (value !== undefined) result[key] = value
  }
  return result
}
