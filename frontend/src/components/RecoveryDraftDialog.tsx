import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { ApiError } from "../api/client"
import { loadPipeline } from "../api/client"
import {
  applyRecoveryDraft,
  createRecoveryDraft,
  discardRecoveryDraft,
  editRecoveryDraft,
  listRecoveryDrafts,
  previewRecoveryDraft,
  restorePreviewRecoveryDraft,
  restoreRecoveryDraft,
} from "../api/recoveryDrafts"
import type { PipelineEditorDocument } from "../types/pipelineDocument"
import {
  parsePipelineEditorDocument,
  type RecoveryGraph,
  type RecoveryNode,
} from "../types/pipelineDocument"
import type { JsonValue, RecoveryDraft, RecoveryDraftPreview } from "../types/recoveryDraft"
import { NODE_TYPE_META } from "../utils/nodeTypes"
import type { NodeTypeValue } from "../utils/nodeTypes"
import type { ExplorePane, ModellingPane } from "../stores/useUIStore"
import useGraphStore from "../stores/useGraphStore"
import { NodeConfigEditor } from "../panels/NodeConfigEditor"
import { DraftEditingContext } from "../panels/DraftEditingContext"
import { GraphProvider } from "../panels/GraphContext"
import { ErrorBoundary } from "./ErrorBoundary"
import type {
  InputSource,
  OnReplaceConfig,
  OnUpdateConfig,
  SimpleEdge,
  SimpleNode,
} from "../panels/editors"
import ModalShell from "./ModalShell"

export type RecoveryDraftTarget = { sourceFile: string; recoveryId: string }

type Props = {
  sourceFile: string
  sourceRevision: string
  target: RecoveryDraftTarget
  onClose: () => void
  onApplied: (document: PipelineEditorDocument) => void
}

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.detail ?? error.message
  return error instanceof Error ? error.message : String(error)
}

function configsFor(draft: RecoveryDraft): Record<string, Record<string, JsonValue>> {
  return Object.fromEntries(draft.nodes.map((node) => [node.key, node.config]))
}

function operationId(): string {
  return `recovery_${crypto.randomUUID().replaceAll("-", "")}`
}

type DraftEditorProps = {
  nodes: RecoveryDraft["nodes"]
  node: RecoveryDraft["nodes"][number] | undefined
  configs: Record<string, Record<string, JsonValue>>
  selectedKey: string | null
  onSelect: (key: string) => void
  onChange: (key: string, next: Record<string, JsonValue>) => void
  disabled: boolean
}

/** Adapts the ordinary editor switch to an in-memory draft, never a graph node. */
function DraftNodeEditor({
  nodes,
  node,
  configs,
  selectedKey,
  onSelect,
  onChange,
  disabled,
}: DraftEditorProps) {
  const [explorePane, setExplorePane] = useState<ExplorePane>("code")
  const [modellingPane, setModellingPane] = useState<ModellingPane>("target")
  const inputSources = useMemo<InputSource[]>(
    () =>
      (node?.input_names ?? []).map((name, index) => ({
        sourceNodeId: `draft-input-${index}`,
        name,
        sourceLabel: name,
        edgeId: `draft-input-${index}`,
        frameUnresolved: false,
      })),
    [node?.input_names],
  )
  if (!node) return null
  return (
    <DraftNodeEditorContent
      nodes={nodes}
      node={node}
      configs={configs}
      selectedKey={selectedKey}
      onSelect={onSelect}
      onChange={onChange}
      disabled={disabled}
      inputSources={inputSources}
      explorePane={explorePane}
      setExplorePane={setExplorePane}
      modellingPane={modellingPane}
      setModellingPane={setModellingPane}
    />
  )
}

type DraftNodeEditorContentProps = Omit<DraftEditorProps, "node"> & {
  node: RecoveryDraft["nodes"][number]
  inputSources: InputSource[]
  explorePane: ExplorePane
  setExplorePane: (pane: ExplorePane) => void
  modellingPane: ModellingPane
  setModellingPane: (pane: ModellingPane) => void
}

function DraftNodeEditorContent({
  nodes,
  node,
  configs,
  selectedKey,
  onSelect,
  onChange,
  disabled,
  inputSources,
  explorePane,
  setExplorePane,
  modellingPane,
  setModellingPane,
}: DraftNodeEditorContentProps) {
  const config = configs[node.key] ?? node.config
  const knownType = node.node_type !== null && Object.hasOwn(NODE_TYPE_META, node.node_type)
  const update: OnUpdateConfig = (keyOrUpdates, value) => {
    if (disabled || !node.editable) return { ok: false, error: "This draft node is read-only." }
    onChange(
      node.key,
      (typeof keyOrUpdates === "string"
        ? { ...config, [keyOrUpdates]: value }
        : { ...config, ...keyOrUpdates }) as Record<string, JsonValue>,
    )
    return { ok: true }
  }
  const replace: OnReplaceConfig = (next) => {
    if (disabled || !node.editable) return { ok: false, error: "This draft node is read-only." }
    onChange(node.key, next as Record<string, JsonValue>)
    return { ok: true }
  }
  const syntheticNode = useMemo<SimpleNode>(
    () => ({
      id: `recovery:${node.key}`,
      data: {
        label: node.label,
        description: "Recovery draft",
        nodeType: node.node_type ?? "unknown",
        config,
      },
    }),
    [config, node.key, node.label, node.node_type],
  )
  // Ordinary editors use the graph context to resolve input frames and edge
  // roles. Give them only the recorded draft inputs: this keeps those controls
  // useful without admitting the live canvas into a recovery edit.
  const draftGraph = useMemo(() => {
    const inputNodes: SimpleNode[] = inputSources.map((input) => ({
      id: input.sourceNodeId,
      data: {
        label: input.name,
        description: "Recovery draft input",
        nodeType: "polars",
        config: {},
        _defaultInputName: input.name,
      },
    }))
    const edges: SimpleEdge[] = inputSources.map((input, index) => ({
      id: input.edgeId,
      source: input.sourceNodeId,
      target: syntheticNode.id,
      data: { _inputName: input.name },
      targetHandle:
        node.node_type === "edgeJoin" ? (index === 0 ? "base" : "join") : undefined,
    }))
    return { allNodes: [...inputNodes, syntheticNode], edges }
  }, [inputSources, node.node_type, syntheticNode])
  return (
    <>
      <div className="mb-3 flex flex-wrap gap-2" aria-label="Draft nodes">
        {nodes.map((item) => (
          <button
            key={item.key}
            type="button"
            onClick={() => onSelect(item.key)}
            aria-pressed={selectedKey === item.key}
          >
            {item.label}
          </button>
        ))}
      </div>
      {!knownType && (
        <p style={{ color: "var(--warning)" }}>
          This node type is not installed. Its settings are retained as evidence and cannot be
          guessed.
        </p>
      )}
      {knownType && node.node_type === "submodel" && (
        <SubmodelDraftEditor
          node={node}
          config={config}
          onChange={(next) => onChange(node.key, next)}
          disabled={disabled || !node.editable}
        />
      )}
      {knownType && node.node_type !== "submodel" && (
        <fieldset
          aria-label="Draft editor"
          disabled={disabled || !node.editable}
          className={disabled || !node.editable ? "opacity-60" : undefined}
        >
          {node.node_type === "explore" && (
            <PaneButtons
              values={["code", "overview", "pivots", "charts", "export"] as ExplorePane[]}
              value={explorePane}
              onChange={setExplorePane}
            />
          )}
          {node.node_type === "modelling" && (
            <PaneButtons
              values={["target", "features", "params", "split", "train"] as ModellingPane[]}
              value={modellingPane}
              onChange={setModellingPane}
            />
          )}
          <ErrorBoundary
            key={node.key}
            name="recovery-draft-editor"
            fallback={
              <p role="alert">
                The normal editor failed. Use Advanced draft JSON to repair this proposal.
              </p>
            }
          >
            <DraftEditingContext.Provider value={true}>
              <GraphProvider allNodes={draftGraph.allNodes} edges={draftGraph.edges} submodels={{}} preamble="">
                <NodeConfigEditor
                  nodeType={node.node_type as NodeTypeValue}
                  config={config}
                  configWithNodeId={{ ...config, _nodeId: syntheticNode.id }}
                  node={syntheticNode}
                  onUpdateConfig={update}
                  onReplaceConfig={replace}
                  inputSources={inputSources}
                  upstreamColumns={[]}
                  pivotColumns={[]}
                  activeExplorePane={explorePane}
                  activeModellingPane={modellingPane}
                  onShowPivots={() => setExplorePane("pivots")}
                  loadPivotFilterMembers={async () => {
                    throw new Error("Draft recovery never loads preview data.")
                  }}
                  exploreConfigHash={null}
                  reservedApiInputFrameLabels={new Set()}
                  accentColor={NODE_TYPE_META[node.node_type as NodeTypeValue].color}
                />
              </GraphProvider>
            </DraftEditingContext.Provider>
          </ErrorBoundary>
        </fieldset>
      )}
    </>
  )
}

function PaneButtons<T extends string>({
  values,
  value,
  onChange,
}: {
  values: T[]
  value: T
  onChange: (value: T) => void
}) {
  return (
    <div className="mb-2 flex gap-1" role="tablist">
      {values.map((pane) => (
        <button
          key={pane}
          type="button"
          role="tab"
          aria-selected={value === pane}
          onClick={() => onChange(pane)}
        >
          {pane}
        </button>
      ))}
    </div>
  )
}

function flattenRecoveryNodes(
  nodes: RecoveryNode[],
  submodels: Record<string, { graph: RecoveryGraph }> | null,
): RecoveryNode[] {
  return [
    ...nodes,
    ...Object.values(submodels ?? {}).flatMap((submodel) =>
      flattenRecoveryNodes(submodel.graph.nodes, submodel.graph.submodels),
    ),
  ]
}

function GroupInventory({
  nodes,
  initial,
  disabled,
  reason,
  onCreate,
}: {
  nodes: RecoveryNode[]
  initial: RecoveryDraftTarget
  disabled: boolean
  reason: string | null
  onCreate: (nodes: RecoveryNode[]) => void
}) {
  const targetKey = (sourceFile: string | null, recoveryId: string) =>
    `${sourceFile ?? ""}\u0000${recoveryId}`
  const [selected, setSelected] = useState<Set<string>>(
    () => new Set([targetKey(initial.sourceFile, initial.recoveryId)]),
  )
  if (reason) return <p style={{ color: "var(--warning)" }}>{reason}</p>
  if (nodes.length === 0) return null
  return (
    <section aria-label="Grouped recovery">
      <h3 className="font-semibold">Add explicit recovery targets</h3>
      <div className="mt-1 max-h-28 overflow-auto">
        {nodes.map((node) => (
          <label
            key={targetKey(node.source_file, node.recovery_id)}
            className="mr-3 inline-flex gap-1"
          >
            <input
              type="checkbox"
              checked={selected.has(targetKey(node.source_file, node.recovery_id))}
              disabled={disabled}
              onChange={(event) =>
                setSelected((current) => {
                  const next = new Set(current)
                  const key = targetKey(node.source_file, node.recovery_id)
                  if (event.target.checked) next.add(key)
                  else next.delete(key)
                  return next
                })
              }
            />
            {node.label} ({node.availability})
          </label>
        ))}
      </div>
      <button
        type="button"
        disabled={disabled || selected.size === 0}
        onClick={() =>
          onCreate(
            nodes.filter((node) => selected.has(targetKey(node.source_file, node.recovery_id))),
          )
        }
      >
        Create grouped draft
      </button>
      {disabled && (
        <p style={{ color: "var(--warning)" }}>Save draft before changing recovery targets.</p>
      )}
    </section>
  )
}

function SubmodelDraftEditor({
  node,
  config,
  onChange,
  disabled,
}: {
  node: RecoveryDraft["nodes"][number]
  config: Record<string, JsonValue>
  onChange: (next: Record<string, JsonValue>) => void
  disabled: boolean
}) {
  const inputPorts = Array.isArray(config.input_ports) ? config.input_ports : []
  const outputPorts = Array.isArray(config.output_ports) ? config.output_ports : []
  const updateFile = (file: string) => onChange({ ...config, file })
  return (
    <section aria-label="Submodel recovery settings" className="space-y-3">
      <p className="text-xs" style={{ color: "var(--text-secondary)" }}>
        Definition identity is preserved. Relink the file and edit public port routes explicitly.
      </p>
      <label className="block">
        Name <input value={typeof config.name === "string" ? config.name : ""} readOnly />
      </label>
      <label className="block">
        Definition ID{" "}
        <input
          value={typeof config.definition_id === "string" ? config.definition_id : ""}
          readOnly
        />
      </label>
      <label className="block">
        Definition file{" "}
        <input
          aria-label="Definition file"
          value={typeof config.file === "string" ? config.file : ""}
          disabled={disabled}
          onChange={(event) => updateFile(event.target.value)}
        />
      </label>
      <p>
        Owners:{" "}
        {node.affected_owners?.join(", ") ||
          "Owner information is unavailable; this proposal remains server-validated."}
      </p>
      <h4 className="font-semibold">Input ports</h4>
      {inputPorts.map((port, index) =>
        !isJsonObject(port) ? (
          <p key={index} style={{ color: "var(--warning)" }}>
            Malformed input-port evidence is retained and must be corrected by the server-guided
            draft.
          </p>
        ) : (
          <InputPortEditor
            key={index}
            port={port}
            index={index}
            allPorts={inputPorts}
            config={config}
            disabled={disabled}
            onChange={onChange}
          />
        ),
      )}
      <h4 className="font-semibold">Output ports</h4>
      {outputPorts.map((port, index) =>
        !isJsonObject(port) ? (
          <p key={index} style={{ color: "var(--warning)" }}>
            Malformed output-port evidence is retained and must be corrected by the server-guided
            draft.
          </p>
        ) : (
          (() => {
            const source = isJsonObject(port.source) ? port.source : {}
            return (
              <div key={index} className="grid grid-cols-3 gap-2">
                <input
                  aria-label={`Output port ${index + 1} name`}
                  value={stringValue(port.name)}
                  readOnly
                  title="Public port names are structural identities."
                />
                <input
                  aria-label={`Output port ${index + 1} source node`}
                  value={stringValue(source.nodeId)}
                  disabled={disabled}
                  onChange={(event) =>
                    updateOutputPort(
                      config,
                      outputPorts,
                      index,
                      { ...port, source: { ...source, nodeId: event.target.value } },
                      onChange,
                    )
                  }
                />
                <input
                  aria-label={`Output port ${index + 1} source handle`}
                  value={stringValue(source.handleId)}
                  disabled={disabled}
                  onChange={(event) =>
                    updateOutputPort(
                      config,
                      outputPorts,
                      index,
                      {
                        ...port,
                        source: { ...source, handleId: optionalHandle(event.target.value) },
                      },
                      onChange,
                    )
                  }
                />
              </div>
            )
          })()
        ),
      )}
    </section>
  )
}

function isJsonObject(value: JsonValue | undefined): value is { [key: string]: JsonValue } {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}
function stringValue(value: JsonValue | undefined): string {
  return typeof value === "string" ? value : ""
}
function optionalHandle(value: string): string | null {
  return value || null
}
function updateInputPort(
  config: Record<string, JsonValue>,
  ports: JsonValue[],
  index: number,
  next: { [key: string]: JsonValue },
  onChange: (next: Record<string, JsonValue>) => void,
) {
  const updated = [...ports]
  updated[index] = next
  onChange({ ...config, input_ports: updated })
}
function updateOutputPort(
  config: Record<string, JsonValue>,
  ports: JsonValue[],
  index: number,
  next: { [key: string]: JsonValue },
  onChange: (next: Record<string, JsonValue>) => void,
) {
  const updated = [...ports]
  updated[index] = next
  onChange({ ...config, output_ports: updated })
}
function InputPortEditor({
  port,
  index,
  allPorts,
  config,
  disabled,
  onChange,
}: {
  port: { [key: string]: JsonValue }
  index: number
  allPorts: JsonValue[]
  config: Record<string, JsonValue>
  disabled: boolean
  onChange: (next: Record<string, JsonValue>) => void
}) {
  const targets = Array.isArray(port.targets) ? port.targets : []
  return (
    <div className="space-y-1">
      <input
        aria-label={`Input port ${index + 1} name`}
        value={stringValue(port.name)}
        readOnly
        title="Public port names are structural identities."
      />
      {targets.map((target, targetIndex) =>
        !isJsonObject(target) ? (
          <p key={targetIndex} style={{ color: "var(--warning)" }}>
            Malformed target evidence retained.
          </p>
        ) : (
          <div key={targetIndex} className="grid grid-cols-4 gap-2">
            <input
              aria-label={`Input port ${index + 1} target ${targetIndex + 1} node`}
              value={stringValue(target.nodeId)}
              disabled={disabled}
              onChange={(event) =>
                updateInputPort(
                  config,
                  allPorts,
                  index,
                  {
                    ...port,
                    targets: targets.map((item, itemIndex) =>
                      itemIndex === targetIndex ? { ...target, nodeId: event.target.value } : item,
                    ),
                  },
                  onChange,
                )
              }
            />
            <input
              aria-label={`Input port ${index + 1} target ${targetIndex + 1} handle`}
              value={stringValue(target.handleId)}
              disabled={disabled}
              onChange={(event) =>
                updateInputPort(
                  config,
                  allPorts,
                  index,
                  {
                    ...port,
                    targets: targets.map((item, itemIndex) =>
                      itemIndex === targetIndex
                        ? { ...target, handleId: optionalHandle(event.target.value) }
                        : item,
                    ),
                  },
                  onChange,
                )
              }
            />
            <button
              type="button"
              disabled={disabled}
              onClick={() =>
                updateInputPort(
                  config,
                  allPorts,
                  index,
                  { ...port, targets: targets.filter((_, itemIndex) => itemIndex !== targetIndex) },
                  onChange,
                )
              }
            >
              Remove target
            </button>
          </div>
        ),
      )}
      <button
        type="button"
        disabled={disabled}
        onClick={() =>
          updateInputPort(
            config,
            allPorts,
            index,
            { ...port, targets: [...targets, { nodeId: "", handleId: null }] },
            onChange,
          )
        }
      >
        Add target
      </button>
    </div>
  )
}

export default function RecoveryDraftDialog({
  sourceFile,
  sourceRevision,
  target,
  onClose,
  onApplied,
}: Props) {
  const [draft, setDraft] = useState<RecoveryDraft | null>(null)
  const [configs, setConfigs] = useState<Record<string, Record<string, JsonValue>>>({})
  const [selectedKey, setSelectedKey] = useState<string | null>(null)
  const [dirty, setDirty] = useState(false)
  const [preview, setPreview] = useState<RecoveryDraftPreview | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [reviewed, setReviewed] = useState(false)
  const [history, setHistory] = useState<RecoveryDraft[]>([])
  const [restoreMode, setRestoreMode] = useState(false)
  const [groupTargets, setGroupTargets] = useState<RecoveryNode[]>([])
  const [groupReason, setGroupReason] = useState<string | null>(null)
  const generation = useRef(0)
  const operation = useRef(operationId())
  const readOnly =
    !!draft && ["applied", "restored", "discarded", "stale", "applying"].includes(draft.state)

  const rememberDraft = useCallback((next: RecoveryDraft) => {
    setDraft(next)
    setHistory((current) => [next, ...current.filter((item) => item.draft_id !== next.draft_id)])
  }, [])

  useEffect(() => {
    const current = ++generation.current
    const controller = new AbortController()
    void listRecoveryDrafts(sourceFile, { signal: controller.signal })
      .then(async (listed) => {
        if (generation.current !== current) return
        const matching = listed.drafts.filter(
          (item) =>
            item.nodes.some(
              (node) =>
                node.source_file === target.sourceFile && node.recovery_id === target.recoveryId,
            ) && item.state !== "discarded",
        )
        setHistory(matching)
        const existing =
          matching.find(
            (item) =>
              item.source_revision === sourceRevision &&
              item.state !== "applied" &&
              item.state !== "restored",
          ) ?? matching[0]
        const next =
          existing ??
          (await createRecoveryDraft({
            source_file: sourceFile,
            source_revision: sourceRevision,
            targets: [{ source_file: target.sourceFile, recovery_id: target.recoveryId }],
            mode: "recover",
          }))
        if (generation.current !== current) return
        rememberDraft(next)
        setConfigs(configsFor(next))
        setSelectedKey(next.nodes[0]?.key ?? null)
        setReviewed(next.reviewed ?? false)
        setRestoreMode(next.state === "applied" || next.state === "restored")
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted && generation.current === current)
          setError(errorMessage(reason))
      })
    return () => {
      controller.abort()
      // This counter fences requests, not a DOM ref; invalidate all in-flight work.
      // eslint-disable-next-line react-hooks/exhaustive-deps
      generation.current++
    }
  }, [sourceFile, sourceRevision, target.recoveryId, target.sourceFile, rememberDraft])

  useEffect(() => {
    const controller = new AbortController()
    void loadPipeline({ signal: controller.signal })
      .then((raw) => {
        const document = parsePipelineEditorDocument(raw)
        if (document.source_file !== sourceFile || document.source_revision !== sourceRevision) {
          setGroupReason("The current source changed; grouped recovery is unavailable.")
          return
        }
        setGroupTargets(
          flattenRecoveryNodes(document.nodes, document.submodels).filter(
            (node) => node.node_type !== "submodelPort" && node.source_file !== null,
          ),
        )
      })
      .catch(() => {
        if (!controller.signal.aborted)
          setGroupReason("Could not load the current recovery inventory.")
      })
    return () => controller.abort()
  }, [sourceFile, sourceRevision])

  const save = async () => {
    if (!draft || readOnly) return
    const current = ++generation.current
    setBusy(true)
    setError(null)
    try {
      const next = await editRecoveryDraft(draft.draft_id, {
        draft_revision: draft.draft_revision,
        configs,
        reviewed,
      })
      if (generation.current !== current) return
      rememberDraft(next)
      setConfigs(configsFor(next))
      setDirty(false)
      setReviewed(next.reviewed ?? false)
      setPreview(null)
    } catch (reason) {
      if (generation.current === current) setError(errorMessage(reason))
    } finally {
      if (generation.current === current) setBusy(false)
    }
  }

  const review = async () => {
    if (!draft || dirty) return
    const current = ++generation.current
    setBusy(true)
    setError(null)
    try {
      const next = await previewRecoveryDraft(draft.draft_id, draft.draft_revision)
      if (generation.current === current) {
        operation.current = operationId()
        setPreview(next)
      }
    } catch (reason) {
      if (generation.current === current) setError(errorMessage(reason))
    } finally {
      if (generation.current === current) setBusy(false)
    }
  }

  const apply = async () => {
    if (
      !draft ||
      !preview?.plan_hash ||
      dirty ||
      !reviewed ||
      (!restoreMode && draft.state !== "ready_to_apply")
    )
      return
    if (useGraphStore.getState().isDirty()) {
      setError("Save or discard unsaved pipeline changes before applying recovery or restore.")
      return
    }
    const current = ++generation.current
    setBusy(true)
    setError(null)
    try {
      const response = await (restoreMode ? restoreRecoveryDraft : applyRecoveryDraft)(
        draft.draft_id,
        {
          draft_revision: draft.draft_revision,
          source_revision: sourceRevision,
          plan_hash: preview.plan_hash,
          operation_id: operation.current,
        },
      )
      if (generation.current === current) onApplied(response.document)
    } catch (reason) {
      if (generation.current === current) setError(errorMessage(reason))
    } finally {
      if (generation.current === current) setBusy(false)
    }
  }

  const discard = async () => {
    if (!draft || busy || ["applied", "restored", "applying"].includes(draft.state)) return
    setBusy(true)
    try {
      await discardRecoveryDraft(draft.draft_id, draft.draft_revision)
      onClose()
    } catch (reason) {
      setError(errorMessage(reason))
    } finally {
      setBusy(false)
    }
  }

  const resetAll = async () => {
    if (busy || dirty) return
    const current = ++generation.current
    setBusy(true)
    setError(null)
    try {
      const next = await createRecoveryDraft({
        source_file: sourceFile,
        source_revision: sourceRevision,
        targets: [{ source_file: target.sourceFile, recovery_id: target.recoveryId }],
        mode: "reset",
      })
      if (generation.current !== current) return
      rememberDraft(next)
      setConfigs(configsFor(next))
      setSelectedKey(next.nodes[0]?.key ?? null)
      setDirty(false)
      setReviewed(false)
      setPreview(null)
      setRestoreMode(false)
      operation.current = operationId()
    } catch (reason) {
      if (generation.current === current) setError(errorMessage(reason))
    } finally {
      if (generation.current === current) setBusy(false)
    }
  }
  const createGrouped = async (nodes: RecoveryNode[]) => {
    if (dirty || busy || nodes.length === 0) return
    const current = ++generation.current
    setBusy(true)
    setError(null)
    try {
      const next = await createRecoveryDraft({
        source_file: sourceFile,
        source_revision: sourceRevision,
        targets: nodes.map((node) => ({
          source_file: node.source_file!,
          recovery_id: node.recovery_id,
        })),
        mode: "recover",
      })
      if (generation.current === current) {
        rememberDraft(next)
        setConfigs(configsFor(next))
        setSelectedKey(next.nodes[0]?.key ?? null)
        setReviewed(false)
        setPreview(null)
        setRestoreMode(false)
        operation.current = operationId()
      }
    } catch (reason) {
      if (generation.current === current) setError(errorMessage(reason))
    } finally {
      if (generation.current === current) setBusy(false)
    }
  }

  const selectHistory = (next: RecoveryDraft) => {
    if (busy) return
    if (dirty) {
      setError("Save or discard local changes before switching recovery history.")
      return
    }
    operation.current = operationId()
    rememberDraft(next)
    setConfigs(configsFor(next))
    setSelectedKey(next.nodes[0]?.key ?? null)
    setDirty(false)
    setReviewed(next.reviewed ?? false)
    setPreview(null)
    setRestoreMode(next.state === "applied" || next.state === "restored")
  }
  const reviewRestore = async () => {
    if (!draft || dirty) return
    const current = ++generation.current
    setBusy(true)
    setError(null)
    try {
      const next = await restorePreviewRecoveryDraft(draft.draft_id, draft.draft_revision)
      if (generation.current === current) {
        operation.current = operationId()
        setPreview(next)
        setRestoreMode(true)
      }
    } catch (reason) {
      if (generation.current === current) setError(errorMessage(reason))
    } finally {
      if (generation.current === current) setBusy(false)
    }
  }
  const requestClose = async () => {
    if (busy) return
    if (dirty) {
      await save()
      return
    }
    onClose()
  }

  return (
    <ModalShell
      ariaLabel="Recover settings"
      onClose={() => {
        void requestClose()
      }}
      width="w-[760px]"
      testId="recovery-draft-dialog"
    >
      <div className="border-b px-5 py-4" style={{ borderColor: "var(--border)" }}>
        <h2 className="text-sm font-semibold">Recover settings</h2>
        <p className="mt-1 text-xs" style={{ color: "var(--text-secondary)" }}>
          This is a saved proposal. It does not change the pipeline until you apply the reviewed
          plan.
        </p>
      </div>
      <div className="max-h-[60vh] space-y-4 overflow-y-auto px-5 py-4 text-xs">
        {error && (
          <div
            role="alert"
            className="rounded p-3"
            style={{ color: "var(--danger-text)", background: "var(--danger-soft)" }}
          >
            {error}
          </div>
        )}
        {!draft && <p>Loading recovery draft…</p>}
        {draft && (
          <>
            <p>
              State: <strong>{draft.state.replaceAll("_", " ")}</strong>
            </p>
            <GroupInventory
              nodes={groupTargets}
              initial={target}
              disabled={busy || dirty}
              reason={groupReason}
              onCreate={createGrouped}
            />
            {history.length > 1 && (
              <label>
                Recovery history{" "}
                <select
                  aria-label="Recovery history"
                  value={draft.draft_id}
                  onChange={(event) => {
                    const next = history.find((item) => item.draft_id === event.target.value)
                    if (next) selectHistory(next)
                  }}
                >
                  {history.map((item) => (
                    <option key={item.draft_id} value={item.draft_id}>
                      {item.state} — {item.updated_at}
                    </option>
                  ))}
                </select>
              </label>
            )}
            {draft.nodes.map((node) => (
              <section
                key={node.key}
                className="rounded p-3"
                style={{ border: "1px solid var(--border)" }}
              >
                <h3 className="font-semibold">{node.label}</h3>
                {!node.editable && (
                  <p className="mt-1" style={{ color: "var(--warning)" }}>
                    Automatic recovery is unavailable. See the issues below.
                  </p>
                )}
                {!!node.affected_owners?.length && (
                  <p className="mt-1">Changes affect: {node.affected_owners.join(", ")}.</p>
                )}
                {node.changes.length > 0 && (
                  <ul className="mt-2 space-y-1">
                    {node.changes.map((change) => (
                      <li key={`${change.path}:${change.outcome}`}>
                        {change.path || "settings"}: {change.outcome} — {change.reason}
                      </li>
                    ))}
                  </ul>
                )}
                {(node.issues ?? []).map((issue) => (
                  <p
                    key={`${issue.path}:${issue.code}`}
                    className="mt-1"
                    style={{
                      color: issue.severity === "error" ? "var(--danger-text)" : "var(--warning)",
                    }}
                  >
                    {issue.path}: {issue.message}
                  </p>
                ))}
              </section>
            ))}
            <DraftNodeEditor
              nodes={draft.nodes}
              node={draft.nodes.find((node) => node.key === selectedKey) ?? draft.nodes[0]}
              configs={configs}
              selectedKey={selectedKey}
              onSelect={setSelectedKey}
              onChange={(key, next) => {
                setConfigs((current) => ({ ...current, [key]: next }))
                setDirty(true)
                setReviewed(false)
                setPreview(null)
              }}
              disabled={busy || readOnly}
            />
            <details>
              <summary>Advanced draft JSON</summary>
              <textarea
                key={draft.draft_id + draft.draft_revision + JSON.stringify(configs)}
                aria-label="Advanced draft JSON"
                defaultValue={JSON.stringify(configs, null, 2)}
                disabled={busy || readOnly}
                className="mt-2 h-40 w-full font-mono"
                onBlur={(event) => {
                  if (event.target.value === JSON.stringify(configs, null, 2)) return
                  try {
                    const parsed = JSON.parse(event.target.value) as unknown
                    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed))
                      throw new Error("Expected an object keyed by draft node.")
                    if (
                      Object.keys(parsed).length !== Object.keys(configs).length ||
                      Object.entries(parsed).some(
                        ([key, value]) =>
                          !Object.hasOwn(configs, key) ||
                          typeof value !== "object" ||
                          value === null ||
                          Array.isArray(value),
                      )
                    )
                      throw new Error("Keep the same node keys, each containing a settings object.")
                    setConfigs(parsed as Record<string, Record<string, JsonValue>>)
                    setDirty(true)
                    setReviewed(false)
                    setPreview(null)
                    setError(null)
                  } catch (reason) {
                    setError(`Advanced JSON was not applied: ${errorMessage(reason)}`)
                  }
                }}
              />
            </details>
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={reviewed}
                disabled={busy || readOnly}
                onChange={(event) => {
                  setReviewed(event.target.checked)
                  setDirty(true)
                  setPreview(null)
                  operation.current = operationId()
                }}
              />{" "}
              I reviewed the settings and all affected owners.
            </label>
            {preview && (
              <section aria-label="Recovery preview">
                <h3 className="font-semibold">Review diff</h3>
                {(preview.changes ?? []).map((change) => (
                  <div key={change.path} className="mt-2">
                    <strong>
                      {change.operation}: {change.path}
                    </strong>
                    <pre className="mt-1 whitespace-pre-wrap">{change.diff}</pre>
                  </div>
                ))}
                {(preview.issues ?? []).map((issue) => (
                  <p key={`${issue.path}:${issue.code}`}>
                    {issue.path}: {issue.message}
                  </p>
                ))}
              </section>
            )}
          </>
        )}
      </div>
      <div
        className="flex flex-wrap justify-between gap-2 border-t px-5 py-3 text-xs [&_button]:rounded [&_button]:border [&_button]:border-[var(--border)] [&_button]:px-2 [&_button]:py-1.5 [&_button]:transition-opacity [&_button:disabled]:cursor-not-allowed [&_button:disabled]:opacity-40"
        style={{ borderColor: "var(--border)" }}
      >
        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => void discard()}
            disabled={!draft || busy || ["applied", "restored", "applying"].includes(draft.state)}
          >
            Discard draft
          </button>
          <button type="button" onClick={() => void resetAll()} disabled={busy || dirty}>
            Reset all settings and code
          </button>
        </div>
        <div className="flex gap-2">
          <button type="button" onClick={() => void requestClose()} disabled={busy}>
            Close
          </button>
          <button
            type="button"
            onClick={() => void save()}
            disabled={!draft || !dirty || busy || readOnly}
          >
            Save draft
          </button>
          <button
            type="button"
            onClick={() => void (restoreMode ? reviewRestore() : review())}
            disabled={!draft || dirty || busy || (readOnly && draft.state !== "applied")}
          >
            {restoreMode ? "Review restore" : "Review diff"}
          </button>
          <button
            type="button"
            onClick={() => void apply()}
            style={{ background: "var(--accent)", color: "var(--text-on-accent)" }}
            disabled={
              !draft ||
              dirty ||
              busy ||
              !reviewed ||
              (!restoreMode && draft.state !== "ready_to_apply") ||
              !preview?.plan_hash
            }
          >
            {restoreMode ? "Restore" : "Apply"}
          </button>
        </div>
      </div>
    </ModalShell>
  )
}
