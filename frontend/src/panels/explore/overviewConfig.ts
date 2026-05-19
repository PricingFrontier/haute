export type OverviewConfig = {
  dataset_header?: boolean
  schema?: boolean
}

function readBool(raw: Record<string, unknown>, key: string): boolean | undefined {
  const value = raw[key]
  return typeof value === "boolean" ? value : undefined
}

export function readOverview(config: Record<string, unknown>): OverviewConfig {
  const raw = config.overview
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return {}
  const rec = raw as Record<string, unknown>
  const result: OverviewConfig = {}
  const datasetHeader = readBool(rec, "dataset_header")
  if (datasetHeader !== undefined) result.dataset_header = datasetHeader
  const schema = readBool(rec, "schema")
  if (schema !== undefined) result.schema = schema
  return result
}
