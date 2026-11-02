import { canonicalJson } from "./canonicalJson"

/**
 * A node config's identity for result staleness and remembered training jobs.
 * Editor state travels beside the config (`HauteNodeData.editor` and the node
 * data's own fields), so the whole config is the node's identity, with the
 * value semantics of persisted config (see `canonicalJson`). Remembered
 * training handles keep it, so its output must not change.
 */
export function hashConfig(config: Record<string, unknown>): string {
  return canonicalJson(config)
}
