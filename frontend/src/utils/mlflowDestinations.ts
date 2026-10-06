/**
 * Pure helpers for the MLflow destinations inventory.
 *
 * The workspace offers up to three tracking destinations (Databricks, an
 * MLflow server, a local folder) and the backend reports what each one
 * resolves to. A node uses the local folder unless its config names a remote
 * (`mlflow_destination` is `databricks` or `server`; choosing Local folder
 * removes the key), so every surface needs the same small vocabulary: which
 * destination a node uses, what its connection light says, and whether
 * logging can proceed.
 *
 * These helpers are pure and import no store, so components, hooks and the
 * store's own selector can all share them. They never rewrite a node's stored
 * value: an unrecognised stored value is *read* as the local folder here and
 * left untouched in the config.
 */
import type { MlflowDestinationEntry, MlflowDestinationKey } from "../api/types"

/** Fixed display order: Databricks, MLflow server, Local folder. */
export const MLFLOW_DESTINATION_KEYS: readonly MlflowDestinationKey[] = [
  "databricks",
  "server",
  "local",
]

export const MLFLOW_DESTINATION_LABELS: Record<MlflowDestinationKey, string> = {
  databricks: "Databricks",
  server: "MLflow server",
  local: "Local folder",
}

/**
 * The inventory as display code sees it — `useMlflowDestinations()`'s return
 * shape, with the store's `"pending"` already mapped to `"loading"`.
 */
export interface MlflowInventoryState {
  status: "loading" | "ready" | "error"
  installed: boolean | null
  importable: boolean | null
  destinations: MlflowDestinationEntry[]
  detail: string
}

export function isMlflowDestinationKey(value: unknown): value is MlflowDestinationKey {
  return (
    typeof value === "string" &&
    (MLFLOW_DESTINATION_KEYS as readonly string[]).includes(value)
  )
}

/**
 * The destination a node actually uses: the remote it names, else the local
 * folder. Any other stored value reads as local — the config keeps it.
 */
export function effectiveMlflowDestination(nodeValue: unknown): MlflowDestinationKey {
  return nodeValue === "databricks" || nodeValue === "server" ? nodeValue : "local"
}

/**
 * The `mlflow_destination` config value for a chosen destination: the remote's
 * key, or `undefined` for the local folder so the key is removed and local has
 * exactly one stored representation (no key).
 */
export function mlflowDestinationConfigValue(
  key: MlflowDestinationKey,
): "databricks" | "server" | undefined {
  return key === "local" ? undefined : key
}

export function mlflowDestinationEntry(
  destinations: MlflowDestinationEntry[],
  key: string,
): MlflowDestinationEntry | undefined {
  return destinations.find((d) => d.key === key)
}

export type MlflowLight = "green" | "amber" | "grey" | "none" | "pending"

/**
 * Connection light for one destination. Local always works, so it carries no
 * light at all; a remote that is configured but whose probe failed is amber,
 * not grey — the configuration is there, the connection was not.
 */
export function mlflowLight(
  entry: MlflowDestinationEntry | undefined,
  status: "loading" | "ready" | "error",
): MlflowLight {
  if (entry && entry.key === "local") return "none"
  if (status === "loading") return "pending"
  if (!entry || !entry.configured) return "grey"
  if (entry.probed) return entry.ok ? "green" : "amber"
  return "pending"
}

export interface MlflowLogAvailability {
  available: boolean
  key: MlflowDestinationKey
  label: string
  destination: string
  reason: string
}

/**
 * Whether a node can log to MLflow, and what to say when it cannot.
 *
 * A failed probe does NOT make logging unavailable: the probe is a snapshot
 * and the log request may well succeed, so the amber light carries that
 * warning instead of disabling the action. Only a missing package, a failed
 * inventory fetch, or an unconfigured effective destination block logging.
 */
export function mlflowLogAvailability(
  state: MlflowInventoryState,
  nodeValue: unknown,
): MlflowLogAvailability {
  const key = effectiveMlflowDestination(nodeValue)
  const label = MLFLOW_DESTINATION_LABELS[key]
  const entry = mlflowDestinationEntry(state.destinations, key)

  if (state.status === "loading") {
    return { available: false, key, label, destination: "", reason: "Checking MLflow…" }
  }
  if (state.status === "error" || state.installed === false || state.importable === false) {
    // Package missing/unimportable, or the inventory never arrived: the
    // store's detail is the only reason anybody has.
    return { available: false, key, label, destination: "", reason: state.detail }
  }
  if (!entry) {
    // The inventory lists every destination, so a missing entry means the
    // response itself was incomplete; its detail is the only reason there is.
    return { available: false, key, label, destination: "", reason: state.detail }
  }
  if (!entry.configured) {
    return { available: false, key, label, destination: entry.destination, reason: entry.detail }
  }
  return { available: true, key, label, destination: entry.destination, reason: "" }
}

/** The experiment path used when a node leaves the field blank. */
export function defaultExperimentName(nodeLabel: string, key: MlflowDestinationKey): string {
  return key === "databricks" ? `/Shared/haute/${nodeLabel}` : nodeLabel
}
