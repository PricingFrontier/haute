import { useEffect, useMemo, useState } from "react"
import {
  resolveOutputDestination,
  writeOutput,
  ApiError,
} from "../../api/client"
import { apiErrorMessage } from "../../api/errors"
import type {
  IoCapabilityGroup,
  IoFormatCapability,
  OutputDestinationResponse,
} from "../../api/types"
import useSettingsStore from "../../stores/useSettingsStore"
import useOutputWriteStore from "../../stores/useOutputWriteStore"
import { buildGraph, graphForRequestIdentity } from "../../utils/buildGraph"
import { useGraph } from "../useGraph"
import IoFormatEditor from "./_IoFormatEditor"
import { ioBranchConfig, ioProviderFieldsReady } from "./_ioProvider"
import IoProviderPicker from "./_IoProviderPicker"
import { useIoCapabilities } from "./_ioFormats"
import type { OnReplaceConfig, OnUpdateConfig } from "./_shared"
import { isPlainObject } from "../../types/guards"

const OUTPUT_COMMON_KEYS = [
  "instanceOf",
  "inputMapping",
  "selected_columns",
  "column_renames",
  "categorical_levels",
  "contract",
] as const

function outputBranchConfig(
  config: Record<string, unknown>,
  group: IoCapabilityGroup,
  requestedFormat?: IoFormatCapability,
  preserveProviderFields = false,
): Record<string, unknown> {
  return ioBranchConfig({
    direction: "output",
    config,
    group,
    commonKeys: OUTPUT_COMMON_KEYS,
    requestedFormat,
    preserveProviderFields,
  })
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
  if (capability.modes.length === 0 || !ioProviderFieldsReady("output", group, config)) {
    return false
  }
  if (
    config.arguments !== undefined &&
    !isPlainObject(config.arguments)
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
  const activeSource = useSettingsStore((state) => state.activeSource)
  const writeState = useOutputWriteStore((state) => state.writes[nodeId])
  const beginWrite = useOutputWriteStore((state) => state.begin)
  const completeWrite = useOutputWriteStore((state) => state.complete)
  const clearWrite = useOutputWriteStore((state) => state.clear)

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
  const graph = useMemo(() => {
    const built = buildGraph(allNodes, edges, submodels, preamble)
    return {
      ...built,
      nodes: built.nodes.map((node) =>
        node.id === nodeId
          ? {
              ...node,
              data: {
                ...node.data,
                config,
              },
            }
          : node,
      ),
    }
  }, [allNodes, config, edges, nodeId, preamble, submodels])
  const identity = useMemo(
    () =>
      JSON.stringify({
        graph: graphForRequestIdentity(graph),
        nodeId,
        config,
        source: activeSource,
      }),
    [activeSource, config, graph, nodeId],
  )
  const isWriting = writeState?.phase === "writing"
  const visibleState =
    writeState?.phase === "writing" ||
    writeState?.requestIdentity === identity
      ? writeState
      : undefined
  const [destinationState, setDestinationState] = useState<
    | {
        identity: string
        response?: OutputDestinationResponse
        error?: string
      }
    | undefined
  >()
  const destination =
    ready && destinationState?.identity === identity
      ? destinationState
      : undefined

  useEffect(() => {
    if (
      writeState &&
      writeState.phase !== "writing" &&
      writeState.requestIdentity !== identity
    ) {
      clearWrite(nodeId, writeState.requestId)
    }
  }, [clearWrite, identity, nodeId, writeState])

  useEffect(
    () => () => {
      const active = useOutputWriteStore.getState().writes[nodeId]
      if (active?.phase !== "writing") {
        clearWrite(nodeId, active?.requestId ?? -1)
      }
    },
    [clearWrite, nodeId],
  )

  useEffect(() => {
    if (!ready) return
    const controller = new AbortController()
    void resolveOutputDestination({
      graph,
      nodeId,
      signal: controller.signal,
    }).then(
      (response) => {
        if (!controller.signal.aborted) {
          setDestinationState({ identity, response })
        }
      },
      (caught: unknown) => {
        if (controller.signal.aborted) return
        const message = apiErrorMessage(caught, "Could not resolve output destination.")
        setDestinationState({ identity, error: message })
      },
    )
    return () => controller.abort()
  }, [graph, identity, nodeId, ready])

  const write = async (overwrite: boolean) => {
    if (!ready || isWriting) return
    if (overwrite && visibleState?.phase !== "confirm_overwrite") return
    const requestIdentity = identity
    const requestId = beginWrite(nodeId, requestIdentity)
    if (requestId === null) return
    try {
      const response = await writeOutput({
        graph,
        nodeId,
        source: activeSource,
        overwrite,
      })
      completeWrite(nodeId, requestId, requestIdentity, {
        phase: "success",
        result: response,
      })
    } catch (caught) {
      const message = apiErrorMessage(caught, "Output write failed.")
      completeWrite(nodeId, requestId, requestIdentity, {
        phase: caught instanceof ApiError && caught.status === 409 ? "confirm_overwrite" : "error",
        message,
      })
    }
  }

  return (
    <div className="px-4 py-3 space-y-3">
      <IoProviderPicker
        direction="output"
        config={config}
        groups={groups}
        group={group}
        capabilitiesLoaded={capabilities !== null}
        error={error}
        accentColor={accentColor}
        onSelect={(next) => onReplaceConfig(outputBranchConfig(config, next))}
      />

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

      {destination?.response?.path && (
        <p className="text-xs" style={{ color: "var(--text-muted)" }}>
          Destination: {destination.response.path}
        </p>
      )}
      {destination?.response?.suffix_mismatch && (
        <p role="alert" className="text-xs" style={{ color: "var(--danger-text)" }}>
          The destination extension does not match the selected format.
        </p>
      )}
      {destination?.error && (
        <p role="alert" className="text-xs" style={{ color: "var(--danger-text)" }}>
          Could not resolve destination: {destination.error}
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
