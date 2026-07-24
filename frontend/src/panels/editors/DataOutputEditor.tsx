import { useRef, useState } from "react"
import { writeOutput } from "../../api/client"
import type {
  IoCapabilityGroup,
  IoFormatCapability,
  WriteOutputResponse,
} from "../../api/types"
import { EditorLabel } from "../../components/form"
import useSettingsStore from "../../stores/useSettingsStore"
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
  const [result, setResult] = useState<WriteOutputResponse | null>(null)
  const [writing, setWriting] = useState(false)
  const currentIdentity = useRef("")

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
  currentIdentity.current = identity

  const write = async () => {
    if (!ready) return
    const requestIdentity = identity
    setWriting(true)
    setResult(null)
    try {
      const response = await writeOutput({
        graph: buildGraph(allNodes, edges, submodels, preamble),
        nodeId,
        source: useSettingsStore.getState().activeSource,
        streamingChunkSize,
      })
      if (currentIdentity.current === requestIdentity) setResult(response)
    } catch (caught) {
      if (currentIdentity.current === requestIdentity) {
        setResult({
          status: "error",
          message:
            caught instanceof Error ? caught.message : "Output write failed.",
        })
      }
    } finally {
      setWriting(false)
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

      <button
        type="button"
        disabled={!ready || writing}
        onClick={() => void write()}
        className="px-3 py-1.5 rounded-lg text-xs font-medium disabled:opacity-50"
        style={{ background: "var(--accent)", color: "white" }}
      >
        {writing ? "Writing..." : "Write"}
      </button>

      {result && (
        <p
          role="status"
          className="text-xs"
          style={{
            color:
              result.status === "ok"
                ? "var(--success)"
                : "var(--danger-text)",
          }}
        >
          {result.message ?? result.status}
          {result.row_count !== undefined
            ? ` | ${result.row_count} rows`
            : ""}
          {result.path ? ` | ${result.path}` : ""}
        </p>
      )}
    </div>
  )
}
