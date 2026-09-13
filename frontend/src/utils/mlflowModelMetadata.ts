import type { ModelVersion } from "../hooks/useMlflowBrowser"

export type ModelTask = "regression" | "classification"

/**
 * The loaded version a stored choice resolves to: the version an alias targets,
 * the newest for "latest", else the exact version.
 */
export function resolveLoadedVersion(
  versions: ModelVersion[],
  version: string,
  alias = "",
): ModelVersion | undefined {
  if (alias) return versions.find((v) => v.aliases?.includes(alias))
  // The versions route lists newest first.
  return version === "latest" ? versions[0] : versions.find((v) => v.version === version)
}

/** The picker value for a registered source: `alias:<name>` for an alias, else the version. */
export function registeredSelectionValue(version: string, alias: string): string {
  return alias ? `alias:${alias}` : version
}

/** The config update a picker value stands for; an alias and a version exclude each other. */
export function registeredSelectionUpdate(value: string): { version?: string; alias?: string } {
  return value.startsWith("alias:")
    ? { alias: value.slice("alias:".length), version: undefined }
    : { version: value, alias: undefined }
}

/**
 * The task a training run recorded in its params. Haute candidate runs always
 * record one; a model logged elsewhere may not, and then the node's explicit
 * task is the only source.
 */
export function recordedModelTask(params: Record<string, string> | undefined): ModelTask | null {
  const task = params?.task
  return task === "regression" || task === "classification" ? task : null
}
