import { useState } from "react"

import type { ColumnInfo, HauteNodeData } from "../types/node"
import { columnFingerprint } from "../utils/columnFingerprint"

type Config = Record<string, unknown>

/** The config a Polars pane writes: its code, its steps and the caches materialised from them. */
const POLARS_PANE_KEYS: ReadonlySet<string> = new Set(["code", "steps", "_steps_error", "_steps_discarded"])
const NO_CONFIG: Config = {}

type KeptColumns = { source: string; config: Config; fingerprint: string; columns: ColumnInfo[] }

/**
 * The node's own columns as its last preview under the active source recorded
 * them, before its own selection and renames: what the Polars panes offer
 * after the node's input columns.
 *
 * Every config edit clears the recorded columns until the node is previewed
 * again, which would take them away at the first pause in typing, so the
 * columns are kept while only the pane's own config changes. Any other
 * setting, or a switch of the active source, drops them until the node is
 * previewed again. Equal recorded content keeps one identity, so a refreshed
 * preview does not reconfigure the code box's completion.
 */
export function useRecordedNodeColumns(data: HauteNodeData, activeSource: string): ColumnInfo[] | undefined {
  const config = data.config ?? NO_CONFIG
  const [kept, setKept] = useState<KeptColumns | null>(null)
  const keptApplies = kept !== null && kept.source === activeSource && sameSettings(kept.config, config)
  const recorded = data._columnsSource === activeSource ? data._availableColumns : undefined
  if (recorded === undefined) {
    // Going back to the kept source or settings does not preview the node again.
    if (kept !== null && !keptApplies) setKept(null)
    return keptApplies ? kept.columns : undefined
  }
  const fingerprint = columnFingerprint(recorded)
  if (keptApplies && kept.fingerprint === fingerprint) return kept.columns
  setKept({ source: activeSource, config, fingerprint, columns: recorded })
  return recorded
}

/** Whether two configs hold the same values, by identity, outside the pane's own keys. */
function sameSettings(kept: Config, current: Config): boolean {
  if (kept === current) return true
  const keys = settingKeys(kept)
  return keys.length === settingKeys(current).length
    && keys.every((key) => Object.hasOwn(current, key) && Object.is(kept[key], current[key]))
}

function settingKeys(config: Config): string[] {
  return Object.keys(config).filter((key) => !POLARS_PANE_KEYS.has(key))
}
