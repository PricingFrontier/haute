import { writeOutput, ApiError } from "../../api/client"
import type {
  IoCapabilityGroup,
  IoFormatCapability,
} from "../../api/types"
import { EditorLabel } from "../../components/form"
import useSettingsStore from "../../stores/useSettingsStore"
import useOutputWriteStore from "../../stores/useOutputWriteStore"
import { buildGraph } from "../../utils/buildGraph"
import { useGraph } from "../useGraph"
import IoFormatEditor from "./_IoFormatEditor"
import { useIoCapabilities } from "./_ioFormats"
import { INPUT_STYLE } from "./_shared"
import type { OnReplaceConfig, OnUpdateConfig } from "./_shared"

const OUTPUT_COMMON_KEYS = [
  "instanceOf",
  "inputMapping",
  "selected_columns",
  "column_renames",
  "categorical_levels",
  "contract",
] as const

function retainedCommonConfig(
  config: Record<string, unknown>,
): Record<string, unknown> {
  return Object.fromEntries(
    OUTPUT_COMMON_KEYS.flatMap((key) =>
      config[key] === undefined ? [] : [[key, config[key]]],
    ),
  )
}

function selectedOutputFormat(
  group: IoCapabilityGroup,
  requested?: IoFormatCapability,
): IoFormatCapability | undefined {
  if (requested?.output) return requested
  return group.formats.find((format) => format.output !== null)
}

function outputBranchConfig(
  config: Record<string, unknown>,
  group: IoCapabilityGroup,
  requestedFormat?: IoFormatCapability,
  preserveProviderFields = false,
): Record<string, unknown> {
  const format = selectedOutputFormat(group, requestedFormat)
  const capability = format?.output
  const fields = Object.fromEntries(
    group.output_fields.flatMap((field) => {
      if (preserveProviderFields && config[field.name] !== undefined) {
        return [[field.name, config[field.name]]]
      }
      return field.required ? [[field.name, ""]] : []
    }),
  )
  return {
    ...retainedCommonConfig(config),
    outputType: group.name,
    ...(format ? { format: format.name } : {}),
    ...(capability?.modes[0] ? { mode: capability.modes[0] } : {}),
    arguments: {},
    ...fields,
  }
}

function hasNonEmptyString(value: unknown): boolean {
  return typeof value === "string" && value.trim().length > 0
}

function isPlainRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}

function providerFieldsReady(
  group: IoCapabilityGroup,
  config: Record<string, unknown>,
): boolean {
  if (group.name === "database") {
    const hasConnection = hasNonEmptyString(config.connection)
    const hasUri = hasNonEmptyString(config.uri)
    return hasConnection !== hasUri && hasNonEmptyString(config.table)
  }
  return group.output_fields
    .filter((field) => field.required)
    .every((field) => hasNonEmptyString(config[field.name]))
}

function outputConfigReady(
  group: IoCapabilityGroup,
  format: IoFormatCapability | undefined,
  config: Record<string, unknown>,
): boolean {
  const capability = format?.output
  if (!capability || capability.engines_missing.length > 0) return false
  const configuredMode =
    typeof config.mode === "string" ? config.mode : ""
  if (
    configuredMode !== "" &&
    !capability.modes.includes(configuredMode as "sink" | "write")
  ) {
    return false
  }
  if (capability.modes.length === 0 || !providerFieldsReady(group, config)) {
    return false
  }
  if (
    config.arguments !== undefined &&
    !isPlainRecord(config.arguments)
  ) {
    return false
  }

  const knownKeys = new Set<string>([
    ...OUTPUT_COMMON_KEYS,
    "outputType",
    "format",
    "mode",
    "arguments",
    ...group.output_fields.map((field) => field.name),
  ])
  return Object.keys(config).every(
    (key) => config[key] === undefined || knownKeys.has(key),
  )
}

function destinationPreview(
  group: IoCapabilityGroup | undefined,
  format: IoFormatCapability | undefined,
  config: Record<string, unknown>,
): { destination: string | null; suffixMismatch: boolean } {
  if (group?.name === "database") {
    const table = typeof config.table === "string" ? config.table.trim() : ""
    return { destination: table || null, suffixMismatch: false }
  }
  if (group?.name !== "file") return { destination: null, suffixMismatch: false }
  const path = typeof config.path === "string" ? config.path.trim() : ""
  if (!path) return { destination: null, suffixMismatch: false }
  const fileName = path.split(/[\\/]/).at(-1) ?? path
  const lastDot = fileName.lastIndexOf(".")
  const suffix =
    lastDot > 0 && lastDot < fileName.length - 1
      ? fileName.slice(lastDot)
      : ""
  const extensions = format?.extensions ?? []
  const resolvedName = suffix ? path : `${path}${extensions[0] ?? ""}`
  return {
    destination: path.includes("/") || path.includes("\\")
      ? resolvedName
      : `outputs/${resolvedName}`,
    suffixMismatch:
      suffix.length > 0 && !extensions.some((extension) => extension.toLowerCase() === suffix.toLowerCase()),
  }
}

export default function DataOutputEditor({
  config,
  onUpdate,
  onReplaceConfig,
  accentColor,
  nodeId,
}: {
  config: Record<string, unknown>
  onUpdate: OnUpdateConfig
  onReplaceConfig: OnReplaceConfig
  accentColor: string
  nodeId: string
}) {
  const { capabilities, error } = useIoCapabilities()
  const { allNodes, edges, submodels, preamble } = useGraph()
  const streamingChunkSize = useSettingsStore(
    (state) => state.streamingChunkSize,
  )
  const writeState = useOutputWriteStore((state) => state.writes[nodeId])
  const beginWrite = useOutputWriteStore((state) => state.begin)
  const completeWrite = useOutputWriteStore((state) => state.complete)

  const groups = (capabilities?.groups ?? []).filter(
    (group) => group.output_available,
  )
  const group = groups.find(
    (candidate) => candidate.name === config.outputType,
  )
  const format = group?.formats.find(
    (candidate) => candidate.name === config.format,
  )
  const ready =
    group !== undefined && outputConfigReady(group, format, config)
  const identity = `${nodeId}:${JSON.stringify(config)}`
  const isWriting = writeState?.phase === "writing"
  const visibleState = writeState?.configIdentity === identity ? writeState : undefined
  const destination = destinationPreview(group, format, config)

  const write = async (overwrite: boolean) => {
    if (!ready || isWriting) return
    const requestIdentity = identity
    const requestId = beginWrite(nodeId, requestIdentity)
    if (requestId === null) return
    try {
      const response = await writeOutput({
        graph: buildGraph(allNodes, edges, submodels, preamble),
        nodeId,
        source: useSettingsStore.getState().activeSource,
        streamingChunkSize,
        overwrite,
      })
      completeWrite(nodeId, requestId, requestIdentity, {
        phase: "success",
        result: response,
      })
    } catch (caught) {
      const message =
        caught instanceof ApiError && caught.detail
          ? caught.detail
          : caught instanceof Error
            ? caught.message
            : "Output write failed."
      completeWrite(nodeId, requestId, requestIdentity, {
        phase: caught instanceof ApiError && caught.status === 409 ? "confirm_overwrite" : "error",
        message,
      })
    }
  }

  return (
    <div className="px-4 py-3 space-y-3">
      {error && (
        <p style={{ color: "var(--danger-text)" }}>
          Could not load IO capabilities: {error}
        </p>
      )}

      {config.outputType !== undefined && !group && capabilities && (
        <section
          aria-label="Configuration errors"
          className="rounded-lg p-2 text-[11px]"
          style={{
            background: "var(--danger-soft)",
            border: "1px solid var(--danger-border)",
            color: "var(--danger-text)",
          }}
        >
          Unknown Data Output provider {JSON.stringify(config.outputType)}.
        </section>
      )}

      <div>
        <EditorLabel>Provider</EditorLabel>
        <select
          aria-label="Provider"
          value={group?.name ?? ""}
          onChange={(event) => {
            const next = groups.find(
              (candidate) => candidate.name === event.target.value,
            )
            if (next) onReplaceConfig(outputBranchConfig(config, next))
          }}
          className="mt-1 w-full px-2.5 py-1.5 text-xs rounded-lg"
          style={INPUT_STYLE}
        >
          <option value="">Select a provider...</option>
          {groups.map((candidate) => (
            <option key={candidate.name} value={candidate.name}>
              {candidate.label}
            </option>
          ))}
        </select>
      </div>

      {group && (
        <IoFormatEditor
          group={group}
          direction="output"
          config={config}
          onUpdate={onUpdate}
          onSelectFormat={(next) =>
            onReplaceConfig(outputBranchConfig(config, group, next, true))
          }
          accentColor={accentColor}
        />
      )}

      {destination.destination && (
        <p className="text-xs" style={{ color: "var(--text-muted)" }}>
          Destination: {destination.destination}
        </p>
      )}
      {destination.suffixMismatch && (
        <p role="alert" className="text-xs" style={{ color: "var(--danger-text)" }}>
          The destination extension does not match the selected format.
        </p>
      )}

      <button
        type="button"
        disabled={!ready || isWriting}
        onClick={() => void write(false)}
        className="px-3 py-1.5 rounded-lg text-xs font-medium disabled:opacity-50"
        style={{ background: "var(--accent)", color: "white" }}
      >
        {isWriting ? "Writing..." : "Write"}
      </button>

      {visibleState?.phase === "writing" && (
        <p role="status" className="text-xs" style={{ color: "var(--text-muted)" }}>
          Writing output…
        </p>
      )}

      {visibleState?.phase === "confirm_overwrite" && (
        <div className="space-y-1">
          <p role="alert" className="text-xs" style={{ color: "var(--danger-text)" }}>
            {visibleState.message}
          </p>
          <button
            type="button"
            onClick={() => void write(true)}
            className="px-3 py-1.5 rounded-lg text-xs font-medium"
            style={{ background: "var(--danger)", color: "white" }}
          >
            Replace existing file
          </button>
        </div>
      )}

      {visibleState && (visibleState.phase === "success" || visibleState.phase === "error") && (
        <p
          role={visibleState.phase === "error" ? "alert" : "status"}
          className="text-xs"
          style={{
            color:
              visibleState.phase === "success"
                ? "var(--success)"
                : "var(--danger-text)",
          }}
        >
          {visibleState.phase === "error"
            ? visibleState.message
            : visibleState.result?.message ?? visibleState.result?.status}
          {visibleState.result?.row_count !== undefined
            ? ` | ${visibleState.result.row_count} rows`
            : ""}
          {visibleState.result?.path ? ` | ${visibleState.result.path}` : ""}
        </p>
      )}
    </div>
  )
}
