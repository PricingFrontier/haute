import type { IoCapabilityGroup, IoFieldCapability, IoFormatCapability } from "../../api/types"

/** Which side of the provider/format capabilities a Data Input or Data Output reads. */
export type IoDirection = "input" | "output"

export const PROVIDER_KEY: Record<IoDirection, "inputType" | "outputType"> = {
  input: "inputType",
  output: "outputType",
}

export const DIRECTION_LABEL: Record<IoDirection, string> = {
  input: "Data Input",
  output: "Data Output",
}

export function hasNonEmptyString(value: unknown): boolean {
  return typeof value === "string" && value.trim().length > 0
}

function providerFields(group: IoCapabilityGroup, direction: IoDirection): IoFieldCapability[] {
  return direction === "input" ? group.input_fields : group.output_fields
}

/** The format a new branch starts on: the one asked for, else the group's first for this side. */
function selectedFormat(
  direction: IoDirection,
  group: IoCapabilityGroup,
  requested?: IoFormatCapability,
): IoFormatCapability | undefined {
  if (requested?.[direction]) return requested
  return group.formats.find((format) => format[direction] !== null)
}

/**
 * The config a provider or format change starts from. It keeps only the
 * provider-independent `commonKeys`, then the new provider's required fields
 * (or, when `preserveProviderFields`, the fields already filled in).
 */
export function ioBranchConfig({
  direction,
  config,
  group,
  commonKeys,
  requestedFormat,
  preserveProviderFields = false,
}: {
  direction: IoDirection
  config: Record<string, unknown>
  group: IoCapabilityGroup
  commonKeys: readonly string[]
  requestedFormat?: IoFormatCapability
  preserveProviderFields?: boolean
}): Record<string, unknown> {
  const format = selectedFormat(direction, group, requestedFormat)
  const capability = format?.[direction]
  const fields = Object.fromEntries(
    providerFields(group, direction).flatMap((field) => {
      if (preserveProviderFields && config[field.name] !== undefined) {
        return [[field.name, config[field.name]]]
      }
      // An input's record fields start as an empty list; everything else is text.
      const initial = direction === "input" && field.kind === "records" ? [] : ""
      return field.required ? [[field.name, initial]] : []
    }),
  )
  return {
    ...Object.fromEntries(
      commonKeys.flatMap((key) => (config[key] === undefined ? [] : [[key, config[key]]])),
    ),
    [PROVIDER_KEY[direction]]: group.name,
    ...(format ? { format: format.name } : {}),
    ...(capability?.modes[0] ? { mode: capability.modes[0] } : {}),
    arguments: {},
    ...fields,
  }
}

/**
 * Whether the provider's own fields are filled in. A database needs exactly
 * one of a connection name or a URI, plus its query (input) or table (output).
 */
export function ioProviderFieldsReady(
  direction: IoDirection,
  group: IoCapabilityGroup,
  config: Record<string, unknown>,
): boolean {
  if (group.name === "database") {
    const hasConnection = hasNonEmptyString(config.connection)
    const hasUri = hasNonEmptyString(config.uri)
    const target = direction === "input" ? config.query : config.table
    return hasConnection !== hasUri && hasNonEmptyString(target)
  }
  return providerFields(group, direction)
    .filter((field) => field.required)
    .every((field) =>
      direction === "input" && field.kind === "records"
        ? Array.isArray(config[field.name])
        : hasNonEmptyString(config[field.name]),
    )
}
