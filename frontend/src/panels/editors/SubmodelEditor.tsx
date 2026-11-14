import { GripVertical, Package } from "lucide-react"
import { apiErrorMessage } from "../../api/errors"
import { EditorLabel } from "../../components/form"
import { Select } from "../../haute-ui"
import useGraphStore from "../../stores/useGraphStore"
import { isSubmodelDefinition, isSubmodelInstanceConfig } from "../../types/node"
import { withAlpha } from "../../utils/color"
import { moveSubmodelOutput, retargetSubmodelInput } from "../../utils/canonicalSubmodelBoundaryEditing"
import useListReorder from "../../hooks/useListReorder"
import useRefocusAfterMove, { altArrowMove } from "../../hooks/useRefocusAfterMove"
import { edgeInputName } from "../../utils/apiInputPorts"
import type { SimpleNode } from "./_shared"
import useToastStore from "../../stores/useToastStore"
import type { SchemaWarning } from "../../api/types"

interface SubmodelEditorProps {
  nodeId: string
  config: Record<string, unknown>
  accentColor: string
  readOnly?: boolean
}

interface PortBadgeProps {
  name: string
  accentColor: string
}

function PortBadge({ name, accentColor }: PortBadgeProps) {
  return (
    <span
      className="inline-flex items-center rounded px-2 py-0.5 font-mono text-[11px]"
      style={{ background: withAlpha(accentColor, 0.1), color: accentColor }}
    >
      {name}
    </span>
  )
}

/**
 * The owner's outputs in order: drag one, or Alt+Up and Alt+Down on it, to move it. The order
 * is the definition's, so every occurrence's output rows move with it, in one undoable edit.
 */
function OutputOrder({ definitionId, names, accentColor }: { definitionId: string; names: string[]; accentColor: string }) {
  const move = (from: number, to: number) => {
    const state = useGraphStore.getState()
    state.setNodesAndEdgesAndSubmodels(state.nodes, state.edges, moveSubmodelOutput(state.submodels, definitionId, from, to))
  }
  const dragFor = useListReorder(move)
  // An output moved by Alt+arrows takes the focus again in its new place.
  const refocus = useRefocusAfterMove<HTMLDivElement>()
  return (
    <div role="list" aria-label="Outputs" className="mt-1 flex flex-col gap-0.5">
      {names.map((name, place) => {
        const drag = dragFor(place)
        return (
          <div
            key={name}
            ref={refocus.register(name)}
            role="listitem"
            aria-label={name}
            aria-keyshortcuts="Alt+ArrowUp Alt+ArrowDown"
            tabIndex={0}
            draggable
            onDragStart={drag.onStart}
            onDragOver={drag.onOver}
            onDrop={drag.onDrop}
            onDragEnd={drag.onEnd}
            onKeyDown={(event) => {
              const to = altArrowMove(event, "vertical", place, names.length)
              if (to === null) return
              refocus.moved(name)
              move(place, to)
            }}
            data-drop-target={drag.target ? "true" : undefined}
            className="focus-ring flex cursor-grab items-center gap-1.5 rounded px-1 py-0.5 hover-bg active:cursor-grabbing"
            style={{ opacity: drag.dragging ? 0.5 : 1, boxShadow: drag.target ? "0 -2px 0 0 var(--accent)" : undefined }}
          >
            <GripVertical size={12} aria-hidden="true" className="shrink-0" style={{ color: "var(--text-muted)" }} />
            <PortBadge name={name} accentColor={accentColor} />
          </div>
        )
      })}
    </div>
  )
}

export default function SubmodelEditor({ nodeId, config, accentColor, readOnly = false }: SubmodelEditorProps) {
  const nodes = useGraphStore((state) => state.nodes)
  const edges = useGraphStore((state) => state.edges)
  const definition = useGraphStore((state) =>
    isSubmodelInstanceConfig(config) ? state.submodels[config.definition_id] : undefined,
  )
  const incoming = edges.filter((edge) => edge.target === nodeId)
  const warnings = (nodes.find((node) => node.id === nodeId)?.data._schemaWarnings as SchemaWarning[] | undefined) ?? []
  const warningsByInput = new Map<string, SchemaWarning[]>()
  if (config.instance_of) {
    for (const warning of warnings) {
      if (warning.status !== "missing" && warning.status !== "unavailable") continue
      if (!warning.input) throw new Error(`Submodel warning for ${nodeId} has no public input`)
      const group = warningsByInput.get(warning.input) ?? []
      group.push(warning)
      warningsByInput.set(warning.input, group)
    }
  }
  const retarget = (port: string, edgeId: string) => {
    if (readOnly) return
    try {
      const state = useGraphStore.getState()
      const next = retargetSubmodelInput(state, nodeId, port, edgeId || null)
      state.setNodesAndEdgesAndSubmodels(next.nodes, next.edges, next.submodels)
    } catch (error: unknown) {
      useToastStore.getState().addToast("error", apiErrorMessage(error))
    }
  }
  if (!isSubmodelInstanceConfig(config) || !isSubmodelDefinition(definition, config.definition_id)) {
    return (
      <div role="alert" className="px-4 py-3 text-xs" style={{ color: "var(--danger)" }}>
        Submodel occurrence or definition is invalid.
      </div>
    )
  }

  return (
    <div className="space-y-3 px-4 py-3">
      <div
        className="flex items-center gap-2 rounded-lg px-2.5 py-2"
        style={{
          background: withAlpha(accentColor, 0.08),
          border: `1px solid ${withAlpha(accentColor, 0.2)}`,
        }}
      >
        <Package size={14} style={{ color: accentColor }} />
        <span className="text-xs font-medium" style={{ color: accentColor }}>Submodel</span>
        <span className="ml-auto font-mono text-[11px]" style={{ color: "var(--text-muted)" }}>
          {definition.graph.nodes.length} nodes
        </span>
      </div>

      <div>
        <EditorLabel>File</EditorLabel>
        <div
          className="mt-1 rounded-lg px-2.5 py-1.5 font-mono text-xs"
          style={{
            background: "var(--bg-input)",
            border: "1px solid var(--border)",
            color: "var(--text-secondary)",
          }}
        >
          {definition.file}
        </div>
      </div>

      {definition.inputPorts.length > 0 && (
        <div>
          <EditorLabel>Inputs</EditorLabel>
          <div className="mt-1 space-y-2">
            {definition.inputPorts.map((port) => (
              <label key={port.name} className="flex items-center justify-between gap-2 text-xs">
                <span>{port.name}</span>
                <Select
                  aria-label={port.name}
                  disabled={readOnly}
                  value={incoming.find((edge) => edge.targetHandle === `in__${port.name}`)?.id ?? ""}
                  onChange={(event) => retarget(port.name, event.target.value)}
                >
                  <option value="">not connected</option>
                  {incoming.map((edge) => {
                    const source = nodes.find((node) => node.id === edge.source)
                    if (!source) throw new Error(`Connection ${edge.id} has no source ${edge.source}`)
                    return <option key={edge.id} value={edge.id}>{edgeInputName(edge, source as unknown as SimpleNode)}</option>
                  })}
                </Select>
              </label>
            ))}
          </div>
        </div>
      )}

      {warningsByInput.size > 0 && (
        <section aria-label="Missing columns">
          <EditorLabel>Missing columns</EditorLabel>
          <dl className="mt-1 space-y-2 text-xs" style={{ color: "var(--warning-strong)" }}>
            {[...warningsByInput].map(([input, inputWarnings]) => (
              <div key={input}>
                <dt className="font-medium">{input}</dt>
                {inputWarnings.map((warning) => (
                  <dd key={warning.column ?? "unavailable"} className="mt-1 font-mono">
                    {warning.status === "unavailable" ? warning.detail : warning.column}
                  </dd>
                ))}
              </div>
            ))}
          </dl>
        </section>
      )}

      {definition.outputPorts.length > 0 && (
        <div>
          <EditorLabel>Outputs</EditorLabel>
          {!readOnly && !config.instance_of && definition.outputPorts.length > 1 ? (
            <OutputOrder definitionId={config.definition_id} names={definition.outputPorts.map((port) => port.name)} accentColor={accentColor} />
          ) : (
            <div className="mt-1 flex flex-wrap gap-1.5">
              {definition.outputPorts.map((port) => (
                <PortBadge key={port.name} {...port} accentColor={accentColor} />
              ))}
            </div>
          )}
        </div>
      )}

    </div>
  )
}
