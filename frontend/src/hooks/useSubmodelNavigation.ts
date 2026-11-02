import { useCallback, useEffect, useRef, useState } from "react"
import type { Node, Edge } from "@xyflow/react"
import type { ViewLevel } from "../components/BreadcrumbBar"
import { getLayoutedElements } from "../utils/layout"
import { normalizeEdges } from "../utils/graphHelpers"
import { serializeSnapshot, toCanonicalGraphPayload } from "../utils/graphSnapshot"
import { buildSubmodelViewGraph } from "../utils/submodelViewGraph"
import {
  resolveCanonicalGraphIdentities,
  resolveEditorGraphIdentities,
} from "../utils/editorIdentities"
import { createNodeInstance, createSubmodel, dissolveSubmodel } from "../api/client"
import { apiErrorMessage } from "../api/errors"
import useDocumentStatusStore from "../stores/useDocumentStatusStore"
import useToastStore from "../stores/useToastStore"
import useGraphStore from "../stores/useGraphStore"
import useUIStore from "../stores/useUIStore"
import {
  isSubmodelDefinition,
  isSubmodelInstanceConfig,
  type SubmodelDefinition,
} from "../types/node"
import type { DrilledOccurrenceIdentity } from "../utils/submodelRuntimeTarget"

interface SubmodelNavParams {
  graphRef: React.MutableRefObject<{ nodes: Node[]; edges: Edge[] }>
  parentGraphRef: React.MutableRefObject<{ nodes: Node[]; edges: Edge[]; submodels: Record<string, unknown> } | null>
  activeSubmodelIdentity?: DrilledOccurrenceIdentity | null
  setActiveSubmodelIdentity: (identity: DrilledOccurrenceIdentity | null) => void
  submodelsRef: React.MutableRefObject<Record<string, unknown>>
  setNodesRaw: (nodes: Node[]) => void
  setEdgesRaw: (edges: Edge[]) => void
  setSelectedNode: (node: Node | null) => void
  setLastSelectedId?: (id: string | null) => void
  setPreviewData: (data: null) => void
  /** Abandon the in-flight preview request; its answer describes the view being left. */
  cancelPreview: () => void
  /** Abandon any in-flight trace and clear the trace on show. */
  clearTrace: () => void
  preambleRef: React.MutableRefObject<string>
  sourceRevisionRef: React.MutableRefObject<string>
  preservedBlocksRef: React.MutableRefObject<string[]>
  descriptionRef: React.MutableRefObject<string>
  sourceFileRef: React.MutableRefObject<string>
  pipelineNameRef: React.MutableRefObject<string>
  fitView: (options?: { padding?: number }) => void
  reservedApiInputFrameLabels: ReadonlySet<string>
  resolveGraphIdentities?: typeof resolveEditorGraphIdentities
  resolveCanonicalIdentities?: typeof resolveCanonicalGraphIdentities
}

/** A created submodel, or the reason it was not created. */
export type SubmodelCreateResult = { ok: true } | { ok: false; error: string }

const STALE_CREATE_SUBMODEL =
  "Create submodel was not applied because the workspace changed while the transform was running."

export interface SubmodelNavReturn {
  viewStack: ViewLevel[]
  handleDrillIntoSubmodel: (nodeId: string) => Promise<void>
  handleBreadcrumbNavigate: (depth: number) => void
  resetToAuthoritativeRoot: (sourceFile: string, pipelineName: string) => void
  handleDocumentReload: (reloaded: { nodes: Node[]; edges: Edge[] }) => void
  /** Creates the submodel, or says why not; the dialog shows a refusal inline. */
  handleCreateSubmodel: (name: string, nodeIds: string[]) => Promise<SubmodelCreateResult>
  handleCreateNodeInstance: (nodeId: string) => Promise<SubmodelCreateResult>
  handleDissolveSubmodel: (instanceId: string) => Promise<void>
}

interface CanonicalOccurrence {
  instanceId: string
  definitionId: string
  alias: string
  label: string
  readOnly: boolean
  definition: SubmodelDefinition
}

interface TransformRequestContext {
  serial: number
  graph: {
    nodes: Node[]
    edges: Edge[]
    submodels: Record<string, unknown>
  }
  preamble: string
  snapshot: string
  sourceFile: string
  pipelineName: string
  sourceRevision: string
  preservedBlocks: string[]
}

interface DrillRequestContext {
  serial: number
  nodeId: string
  definitionId: string
  alias: string
  readOnly: boolean
  submodelFile: string
  parentGraphPresent: boolean
  viewStackSnapshot: string
  activeSubmodelIdentityKey: string
  refSnapshot: string
  storeSnapshot: string
  sourceFile: string
  pipelineName: string
  sourceRevision: string
  preservedBlocks: string
  documentFence: string
}

function serializeViewStackIdentity(viewStack: readonly ViewLevel[]): string {
  return JSON.stringify(
    viewStack.map((level) =>
      level.type === "submodel"
        ? {
            type: level.type,
            name: level.name,
            file: level.file,
            instanceId: level.instanceId,
            definitionId: level.definitionId,
            readOnly: level.readOnly ?? false,
          }
        : {
            type: level.type,
            name: level.name,
            file: level.file,
          },
    ),
  )
}

function documentNavigationFence(): { fence: string; allowed: boolean } {
  const status = useDocumentStatusStore.getState()
  const allowed =
    status.systemFailure === null
    && (status.capabilities === null
      || (status.capabilities.can_manage_submodels && status.graphSynchronized))
  return {
    fence: JSON.stringify([
      status.executionGeneration,
      status.sourceFile,
      status.sourceRevision,
      status.loadStatus,
      status.capabilities?.can_manage_submodels ?? null,
      status.graphSynchronized,
      status.systemFailure,
    ]),
    allowed,
  }
}

function canonicalOccurrence(
  nodeId: string,
  nodes: Node[],
  submodels: Record<string, unknown>,
): CanonicalOccurrence {
  const node = nodes.find((candidate) => candidate.id === nodeId)
  if (!node || node.data.nodeType !== "submodel") {
    throw new Error(`Submodel instance ${nodeId} is missing or has the wrong node type`)
  }
  const config = node.data.config
  if (!isSubmodelInstanceConfig(config)) {
    throw new Error(`Submodel instance ${nodeId} has malformed identity config`)
  }
  const definition = submodels[config.definitionId]
  if (!isSubmodelDefinition(definition, config.definitionId)) {
    throw new Error(
      `Submodel instance ${nodeId} references missing or malformed definition ${config.definitionId}`,
    )
  }
  const label = config.alias
  return {
    instanceId: nodeId,
    definitionId: config.definitionId,
    alias: config.alias,
    label,
    readOnly: config.instanceOf !== undefined,
    definition,
  }
}

function instanceCount(nodes: Node[], definitionId: string): number {
  return nodes.filter((node) => {
    const config = node.data.config
    return node.data.nodeType === "submodel"
      && isSubmodelInstanceConfig(config)
      && config.definitionId === definitionId
  }).length
}

export default function useSubmodelNavigation({
  graphRef, parentGraphRef, activeSubmodelIdentity, setActiveSubmodelIdentity, submodelsRef,
  setNodesRaw, setEdgesRaw,
  setSelectedNode, setLastSelectedId, setPreviewData, cancelPreview, clearTrace,
  preambleRef, sourceRevisionRef, preservedBlocksRef, descriptionRef, sourceFileRef, pipelineNameRef,
  fitView, reservedApiInputFrameLabels, resolveGraphIdentities = resolveEditorGraphIdentities,
  resolveCanonicalIdentities = resolveCanonicalGraphIdentities,
}: SubmodelNavParams): SubmodelNavReturn {
  const addToast = useToastStore((s) => s.addToast)
  const [viewStack, setViewStack] = useState<ViewLevel[]>([{ type: "pipeline", name: "main", file: "" }])
  const viewStackRef = useRef(viewStack)
  const activeSubmodelIdentityRef = useRef(activeSubmodelIdentity)
  const transformRequestSerialRef = useRef(0)
  useEffect(() => { viewStackRef.current = viewStack }, [viewStack])
  useEffect(() => {
    activeSubmodelIdentityRef.current = activeSubmodelIdentity
  }, [activeSubmodelIdentity])

  /**
   * What belongs to the view being left ends with it: the side panels, the
   * selection, and the preview and trace, including requests still running.
   */
  const leaveView = useCallback(() => {
    useUIStore.getState().closeSidePanes()
    setSelectedNode(null)
    setLastSelectedId?.(null)
    cancelPreview()
    setPreviewData(null)
    clearTrace()
  }, [cancelPreview, clearTrace, setLastSelectedId, setPreviewData, setSelectedNode])

  const beginTransformRequest = useCallback((): TransformRequestContext => {
    const { nodes, edges, submodels, preamble } = useGraphStore.getState()
    const canonical = toCanonicalGraphPayload({ nodes, edges, submodels })
    return {
      serial: ++transformRequestSerialRef.current,
      graph: {
        nodes: canonical.nodes,
        edges: canonical.edges,
        submodels: canonical.submodels ?? {},
      },
      preamble,
      snapshot: serializeSnapshot({ nodes, edges, preamble, submodels }),
      sourceFile: sourceFileRef.current,
      pipelineName: pipelineNameRef.current,
      sourceRevision: sourceRevisionRef.current,
      preservedBlocks: [...preservedBlocksRef.current],
    }
  }, [pipelineNameRef, preservedBlocksRef, sourceFileRef, sourceRevisionRef])

  const transformRequestIsStale = useCallback((request: TransformRequestContext): boolean => {
    const current = useGraphStore.getState()
    return transformRequestSerialRef.current !== request.serial
      || parentGraphRef.current !== null
      || sourceFileRef.current !== request.sourceFile
      || pipelineNameRef.current !== request.pipelineName
      || sourceRevisionRef.current !== request.sourceRevision
      || JSON.stringify(preservedBlocksRef.current) !== JSON.stringify(request.preservedBlocks)
      || serializeSnapshot({
        nodes: current.nodes,
        edges: current.edges,
        preamble: current.preamble,
        submodels: current.submodels,
      }) !== request.snapshot
  }, [
    parentGraphRef,
    pipelineNameRef,
    preservedBlocksRef,
    sourceFileRef,
    sourceRevisionRef,
  ])

  const beginDrillRequest = useCallback((
    occurrence: CanonicalOccurrence,
    parentNodes: Node[],
    parentEdges: Edge[],
  ): DrillRequestContext => {
    const storeState = useGraphStore.getState()
    const docFence = documentNavigationFence()
    return {
      serial: ++transformRequestSerialRef.current,
      nodeId: occurrence.instanceId,
      definitionId: occurrence.definitionId,
      alias: occurrence.alias,
      readOnly: occurrence.readOnly,
      submodelFile: occurrence.definition.file,
      parentGraphPresent: parentGraphRef.current !== null,
      viewStackSnapshot: serializeViewStackIdentity(viewStackRef.current),
      activeSubmodelIdentityKey: JSON.stringify([
        activeSubmodelIdentityRef.current?.instanceId ?? null,
        activeSubmodelIdentityRef.current?.definitionId ?? null,
      ]),
      refSnapshot: serializeSnapshot({
        nodes: parentNodes,
        edges: parentEdges,
        preamble: preambleRef.current,
        submodels: submodelsRef.current,
      }),
      storeSnapshot: serializeSnapshot({
        nodes: storeState.nodes,
        edges: storeState.edges,
        preamble: storeState.preamble,
        submodels: storeState.submodels,
      }),
      sourceFile: sourceFileRef.current,
      pipelineName: pipelineNameRef.current,
      sourceRevision: sourceRevisionRef.current,
      preservedBlocks: JSON.stringify(preservedBlocksRef.current),
      documentFence: docFence.fence,
    }
  }, [
    parentGraphRef,
    pipelineNameRef,
    preambleRef,
    preservedBlocksRef,
    sourceFileRef,
    sourceRevisionRef,
    submodelsRef,
  ])

  const drillRequestIsStale = useCallback((request: DrillRequestContext): boolean => {
    const currentStore = useGraphStore.getState()
    const currentDoc = documentNavigationFence()
    if (
      !currentDoc.allowed
      || currentDoc.fence !== request.documentFence
      || transformRequestSerialRef.current !== request.serial
      || (parentGraphRef.current !== null) !== request.parentGraphPresent
      || serializeViewStackIdentity(viewStackRef.current) !== request.viewStackSnapshot
      || JSON.stringify([
        activeSubmodelIdentityRef.current?.instanceId ?? null,
        activeSubmodelIdentityRef.current?.definitionId ?? null,
      ]) !== request.activeSubmodelIdentityKey
      || sourceFileRef.current !== request.sourceFile
      || pipelineNameRef.current !== request.pipelineName
      || sourceRevisionRef.current !== request.sourceRevision
      || JSON.stringify(preservedBlocksRef.current) !== request.preservedBlocks
      || serializeSnapshot({
        nodes: graphRef.current.nodes,
        edges: graphRef.current.edges,
        preamble: preambleRef.current,
        submodels: submodelsRef.current,
      }) !== request.refSnapshot
      || serializeSnapshot({
        nodes: currentStore.nodes,
        edges: currentStore.edges,
        preamble: currentStore.preamble,
        submodels: currentStore.submodels,
      }) !== request.storeSnapshot
    ) {
      return true
    }
    try {
      const currentOccurrence = canonicalOccurrence(
        request.nodeId,
        graphRef.current.nodes,
        submodelsRef.current,
      )
      return (
        currentOccurrence.definitionId !== request.definitionId
        || currentOccurrence.alias !== request.alias
        || currentOccurrence.readOnly !== request.readOnly
        || currentOccurrence.definition.file !== request.submodelFile
      )
    } catch {
      return true
    }
  }, [
    graphRef,
    parentGraphRef,
    pipelineNameRef,
    preambleRef,
    preservedBlocksRef,
    sourceFileRef,
    sourceRevisionRef,
    submodelsRef,
  ])

  const createSubmodelTransform = useCallback(async (
    selection: { name: string; nodeIds: string[] } | { nodeId: string },
  ): Promise<SubmodelCreateResult> => {
    if (parentGraphRef.current) {
      return { ok: false, error: "Return to the main pipeline before creating a submodel." }
    }
    try {
      const request = beginTransformRequest()
      const payload = {
        graph: request.graph,
        preamble: request.preamble,
        source_file: request.sourceFile,
        pipeline_name: request.pipelineName,
        pipeline_description: descriptionRef.current,
        base_revision: request.sourceRevision,
        preserved_blocks: request.preservedBlocks,
      }
      const data = "nodeId" in selection
        ? await createNodeInstance({ ...payload, node_id: selection.nodeId })
        : await createSubmodel({ ...payload, name: selection.name, node_ids: selection.nodeIds })
      if (transformRequestIsStale(request)) {
        return { ok: false, error: STALE_CREATE_SUBMODEL }
      }
      const newGraph = data.graph
      if (newGraph) {
        const nextNodes = newGraph.nodes ?? []
        const nextEdges = normalizeEdges(newGraph.edges ?? [])
        const nextSubmodels = newGraph.submodels ?? {}
        const nextPreamble = newGraph.preamble ?? request.preamble
        const nextPreservedBlocks = newGraph.preserved_blocks ?? request.preservedBlocks
        const resolved = await resolveCanonicalIdentities({
          nodes: nextNodes,
          edges: nextEdges,
          submodels: nextSubmodels,
          reservedApiInputFrameLabels,
        })
        if (transformRequestIsStale(request)) {
          return { ok: false, error: STALE_CREATE_SUBMODEL }
        }
        const committedNodes = "instance_name" in data
          ? resolved.nodes.map(node => ({ ...node, selected: node.id === data.instance_name }))
          : resolved.nodes
        const selected = "instance_name" in data
          ? committedNodes.find(node => node.id === data.instance_name)
          : undefined
        if ("instance_name" in data && !selected) {
          throw new Error(`Create Instance returned no occurrence ${data.instance_name}. Reload the pipeline and retry.`)
        }
        graphRef.current = { nodes: committedNodes, edges: resolved.edges }
        submodelsRef.current = resolved.submodels
        preambleRef.current = nextPreamble
        preservedBlocksRef.current = nextPreservedBlocks
        useGraphStore.getState().setNodesAndEdgesAndSubmodels(
          committedNodes,
          resolved.edges,
          resolved.submodels,
          nextPreamble,
        )
        if (selected) {
          setSelectedNode(selected)
          setLastSelectedId?.(selected.id)
        }
        addToast("success", "nodeId" in selection
          ? "Instance created"
          : `Submodel "${selection.name}" created - save to apply`)
        setTimeout(() => fitView({ padding: 0.8 }), 100)
      }
      return { ok: true }
    } catch (err: unknown) {
      return {
        ok: false,
        error: `Create submodel failed: ${apiErrorMessage(err)}`,
      }
    }
  }, [graphRef, parentGraphRef, submodelsRef, preambleRef, preservedBlocksRef, descriptionRef, fitView, addToast, beginTransformRequest, transformRequestIsStale, reservedApiInputFrameLabels, resolveCanonicalIdentities, setSelectedNode, setLastSelectedId])

  const handleCreateSubmodel = useCallback((name: string, nodeIds: string[]) =>
    createSubmodelTransform({ name, nodeIds }), [createSubmodelTransform])
  const handleCreateNodeInstance = useCallback((nodeId: string) =>
    createSubmodelTransform({ nodeId }), [createSubmodelTransform])

  const handleDrillIntoSubmodel = useCallback(async (nodeId: string) => {
    try {
      const parentNodes = [...graphRef.current.nodes]
      const parentEdges = [...graphRef.current.edges]
      const occurrence = canonicalOccurrence(nodeId, parentNodes, submodelsRef.current)
      const request = beginDrillRequest(occurrence, parentNodes, parentEdges)
      if (!documentNavigationFence().allowed) {
        return
      }
      const displayName = occurrence.label
      const smGraph = occurrence.definition.graph
      const parentSourceFile = sourceFileRef.current
      const submodelSourceFile = occurrence.definition.file
      const childEdges = normalizeEdges(smGraph.edges ?? [])
      const liveDefinition = {
        ...occurrence.definition,
        graph: { ...smGraph, nodes: smGraph.nodes ?? [], edges: childEdges },
      }
      const projected = buildSubmodelViewGraph({
        submodelName: displayName,
        instanceId: occurrence.instanceId,
        definition: liveDefinition,
        childNodes: smGraph.nodes ?? [],
        childEdges,
        parentNodes,
        parentEdges,
      })
      const resolved = await resolveGraphIdentities({
        nodes: projected.nodes,
        edges: projected.edges,
        submodels: submodelsRef.current,
        reservedApiInputFrameLabels,
      })
      if (drillRequestIsStale(request)) {
        return
      }
      const layouted = await getLayoutedElements(resolved.nodes, resolved.edges)
      if (drillRequestIsStale(request)) {
        return
      }

      // Commit navigation only after projection, identity resolution, and layout all succeed.
      // The store swaps the canvas and its history first: it refuses a view
      // that cannot open, before anything else changes.
      useGraphStore.getState().enterSubmodelView({ nodes: layouted, edges: resolved.edges })
      graphRef.current = { nodes: layouted, edges: resolved.edges }
      setActiveSubmodelIdentity({
        instanceId: occurrence.instanceId,
        definitionId: occurrence.definitionId,
      })
      parentGraphRef.current = {
        nodes: parentNodes,
        edges: parentEdges,
        submodels: { ...submodelsRef.current },
      }
      setViewStack((prev) => {
        const updated = [...prev]
        if (updated.length > 0) {
          updated[updated.length - 1] = {
            ...updated[updated.length - 1],
            file: parentSourceFile,
            _savedNodes: parentNodes,
            _savedEdges: parentEdges,
          }
        }
        const nextStack = [
          ...updated,
          {
            type: "submodel" as const,
            name: displayName,
            file: submodelSourceFile,
            instanceId: occurrence.instanceId,
            definitionId: occurrence.definitionId,
            readOnly: occurrence.readOnly,
          },
        ]
        viewStackRef.current = nextStack
        return nextStack
      })
      sourceFileRef.current = submodelSourceFile
      leaveView()
      const count = instanceCount(parentNodes, occurrence.definitionId)
      if (occurrence.readOnly) {
        addToast("info", `Viewing read-only instance "${displayName}". Edit the original submodel to change its definition.`)
      } else if (count > 1) {
        addToast("info", `Editing shared definition "${displayName}" affects ${count} instances.`)
      }
      setTimeout(() => fitView({ padding: 0.8 }), 100)
    } catch (err: unknown) {
      addToast("error", `Drill-down failed: ${apiErrorMessage(err)}`)
    }
  }, [graphRef, parentGraphRef, setActiveSubmodelIdentity, submodelsRef, sourceFileRef, fitView, addToast, reservedApiInputFrameLabels, resolveGraphIdentities, beginDrillRequest, drillRequestIsStale, leaveView])

  const handleBreadcrumbNavigate = useCallback((depth: number) => {
    const prev = viewStackRef.current
    if (depth >= prev.length - 1) return
    // Submodels do not nest (the store refuses a second view), so every
    // breadcrumb below the active one is the main pipeline.
    const root = parentGraphRef.current
    if (depth !== 0 || !root) {
      throw new Error(`Breadcrumb ${depth} has no root graph to return to`)
    }
    transformRequestSerialRef.current += 1
    const target = prev[0]
    const rootEdges = normalizeEdges(root.edges)
    useGraphStore.getState().exitSubmodelView({
      nodes: root.nodes,
      edges: rootEdges,
      submodels: root.submodels,
    })
    graphRef.current = { nodes: root.nodes, edges: rootEdges }
    submodelsRef.current = root.submodels
    parentGraphRef.current = null
    sourceFileRef.current = target.file
    setActiveSubmodelIdentity(null)
    const nextStack = prev.slice(0, 1)
    viewStackRef.current = nextStack
    setViewStack(nextStack)
    leaveView()
    setTimeout(() => fitView({ padding: 0.8 }), 100)
  }, [graphRef, parentGraphRef, setActiveSubmodelIdentity, submodelsRef, sourceFileRef, fitView, leaveView])

  const resetToAuthoritativeRoot = useCallback((sourceFile: string, pipelineName: string) => {
    transformRequestSerialRef.current += 1
    parentGraphRef.current = null
    setActiveSubmodelIdentity(null)
    const rootView: ViewLevel[] = [{
      type: "pipeline",
      name: pipelineName || "main",
      file: sourceFile,
    }]
    viewStackRef.current = rootView
    setViewStack(rootView)
  }, [parentGraphRef, setActiveSubmodelIdentity])

  const handleDocumentReload = useCallback((
    reloaded: { nodes: Node[]; edges: Edge[] },
  ) => {
    transformRequestSerialRef.current += 1
    const { nodes: reloadedNodes, edges: reloadedEdges } = reloaded
    const activeView = viewStackRef.current[viewStackRef.current.length - 1]
    const drilledInstanceId =
      activeSubmodelIdentity?.instanceId ??
      (activeView?.type === "submodel" ? activeView.instanceId : null)
    if (!drilledInstanceId) return

    // The synchronizer has installed a new root snapshot. Drop every cached
    // child view, even if the occurrence survives, so breadcrumbs and canvas
    // always describe the same document.
    parentGraphRef.current = null
    setActiveSubmodelIdentity(null)
    const rootFile = viewStackRef.current[0]?.file || sourceFileRef.current
    const rootName = viewStackRef.current[0]?.name || pipelineNameRef.current || "main"
    sourceFileRef.current = rootFile
    const rootView: ViewLevel[] = [{
      type: "pipeline",
      name: rootName,
      file: rootFile,
    }]
    viewStackRef.current = rootView
    setViewStack(rootView)
    setNodesRaw(reloadedNodes)
    setEdgesRaw(normalizeEdges(reloadedEdges))
    setSelectedNode(null)
    setLastSelectedId?.(null)
    setPreviewData(null)
    setTimeout(() => fitView({ padding: 0.8 }), 100)
  }, [
    activeSubmodelIdentity,
    parentGraphRef,
    setActiveSubmodelIdentity,
    sourceFileRef,
    pipelineNameRef,
    setNodesRaw,
    setEdgesRaw,
    setSelectedNode,
    setLastSelectedId,
    setPreviewData,
    fitView,
  ])

  const handleDissolveSubmodel = useCallback(async (instanceId: string) => {
    if (parentGraphRef.current) {
      addToast("error", "Return to the main pipeline before dissolving a submodel.")
      return
    }
    try {
      const request = beginTransformRequest()
      const occurrence = canonicalOccurrence(
        instanceId,
        request.graph.nodes,
        request.graph.submodels,
      )
      const displayName = occurrence.label
      const data = await dissolveSubmodel({
        instance_id: occurrence.instanceId,
        graph: request.graph,
        preamble: request.preamble,
        source_file: request.sourceFile,
        pipeline_name: request.pipelineName,
        pipeline_description: descriptionRef.current,
        base_revision: request.sourceRevision,
        preserved_blocks: request.preservedBlocks,
      })
      if (transformRequestIsStale(request)) {
        addToast("error", "Dissolve submodel was not applied because the workspace changed while the transform was running.")
        return
      }
      const flat = data.graph
      if (flat) {
        const nextNodes = flat.nodes ?? []
        const nextEdges = normalizeEdges(flat.edges ?? [])
        const nextSubmodels = flat.submodels ?? {}
        const nextPreamble = flat.preamble ?? request.preamble
        const resolved = await resolveCanonicalIdentities({
          nodes: nextNodes,
          edges: nextEdges,
          submodels: nextSubmodels,
          reservedApiInputFrameLabels,
        })
        if (transformRequestIsStale(request)) {
          addToast("error", "Dissolve submodel was not applied because the workspace changed while the transform was running.")
          return
        }
        graphRef.current = { nodes: resolved.nodes, edges: resolved.edges }
        preambleRef.current = nextPreamble
        preservedBlocksRef.current = flat.preserved_blocks ?? request.preservedBlocks
        submodelsRef.current = resolved.submodels
        useGraphStore.getState().setNodesAndEdgesAndSubmodels(
          resolved.nodes,
          resolved.edges,
          resolved.submodels,
          nextPreamble,
        )
        addToast("success", `Submodel "${displayName}" dissolved - save to apply`)
        setTimeout(() => fitView({ padding: 0.8 }), 100)
      }
    } catch (err: unknown) {
      addToast("error", `Dissolve failed: ${apiErrorMessage(err)}`)
    }
  }, [graphRef, parentGraphRef, submodelsRef, preambleRef, preservedBlocksRef, descriptionRef, fitView, addToast, beginTransformRequest, transformRequestIsStale, reservedApiInputFrameLabels, resolveCanonicalIdentities])

  return {
    viewStack,
    handleDrillIntoSubmodel,
    handleBreadcrumbNavigate,
    resetToAuthoritativeRoot,
    handleDocumentReload,
    handleCreateSubmodel,
    handleCreateNodeInstance,
    handleDissolveSubmodel,
  }
}
