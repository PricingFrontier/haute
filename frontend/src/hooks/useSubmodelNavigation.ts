import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react"
import type { Node, Edge } from "@xyflow/react"
import type { ViewLevel } from "../components/BreadcrumbBar"
import { normalizeEdges } from "../utils/graphHelpers"
import { serializeSnapshot, toCanonicalGraphPayload } from "../utils/graphSnapshot"
import { resolveCanonicalGraphIdentities } from "../utils/editorIdentities"
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

interface SubmodelNavParams {
  setNodesRaw: (nodes: Node[]) => void
  setEdgesRaw: (edges: Edge[]) => void
  setSelectedNode: (node: Node | null) => void
  setLastSelectedId?: (id: string | null) => void
  /** The result key of the node whose panel is open; leaving a view forgets it with the panel. */
  lastSelectedKeyRef: React.MutableRefObject<string | null>
  setPreviewData: (data: null) => void
  /** Abandon the in-flight preview request; its answer describes the view being left. */
  cancelPreview: () => void
  /** Abandon any in-flight trace and clear the trace on show. */
  clearTrace: () => void
  sourceRevisionRef: React.MutableRefObject<string>
  preservedBlocksRef: React.MutableRefObject<string[]>
  descriptionRef: React.MutableRefObject<string>
  sourceFileRef: React.MutableRefObject<string>
  pipelineNameRef: React.MutableRefObject<string>
  fitView: (options?: { padding?: number }) => void
  reservedApiInputFrameLabels: ReadonlySet<string>
  resolveCanonicalIdentities?: typeof resolveCanonicalGraphIdentities
}

/** A created submodel, or the reason it was not created. */
export type SubmodelCreateResult = { ok: true } | { ok: false; error: string }

const STALE_CREATE_SUBMODEL =
  "Create submodel was not applied because the workspace changed while the transform was running."

export interface SubmodelNavReturn {
  viewStack: ViewLevel[]
  handleDrillIntoSubmodel: (nodeId: string) => void
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
  const definition = submodels[config.definition_id]
  if (!isSubmodelDefinition(definition, config.definition_id)) {
    throw new Error(
      `Submodel instance ${nodeId} references missing or malformed definition ${config.definition_id}`,
    )
  }
  const label = config.alias
  return {
    instanceId: nodeId,
    definitionId: config.definition_id,
    alias: config.alias,
    label,
    readOnly: config.instance_of !== undefined,
    definition,
  }
}

function instanceCount(nodes: Node[], definitionId: string): number {
  return nodes.filter((node) => {
    const config = node.data.config
    return node.data.nodeType === "submodel"
      && isSubmodelInstanceConfig(config)
      && config.definition_id === definitionId
  }).length
}

export default function useSubmodelNavigation({
  
  setNodesRaw, setEdgesRaw,
  setSelectedNode, setLastSelectedId, lastSelectedKeyRef, setPreviewData, cancelPreview, clearTrace,
  sourceRevisionRef, preservedBlocksRef, descriptionRef, sourceFileRef, pipelineNameRef,
  fitView, reservedApiInputFrameLabels,
  resolveCanonicalIdentities = resolveCanonicalGraphIdentities,
}: SubmodelNavParams): SubmodelNavReturn {
  const addToast = useToastStore((s) => s.addToast)
  const [viewStack, setViewStack] = useState<ViewLevel[]>([{ type: "pipeline", name: "main", file: "" }])
  const viewStackRef = useRef(viewStack)
  const transformRequestSerialRef = useRef(0)
  useEffect(() => { viewStackRef.current = viewStack }, [viewStack])

  /**
   * What belongs to the view being left ends with it: the side panels, the
   * selection and the open node, and the preview and trace, including requests
   * still running. Opening the same node again then asks for its preview.
   */
  const leaveView = useCallback(() => {
    useUIStore.getState().closeSidePanes()
    setSelectedNode(null)
    setLastSelectedId?.(null)
    lastSelectedKeyRef.current = null
    cancelPreview()
    setPreviewData(null)
    clearTrace()
  }, [cancelPreview, clearTrace, lastSelectedKeyRef, setLastSelectedId, setPreviewData, setSelectedNode])

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
      || useGraphStore.getState().focus !== null
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
    pipelineNameRef,
    preservedBlocksRef,
    sourceFileRef,
    sourceRevisionRef,
  ])

  const createSubmodelTransform = useCallback(async (
    selection: { name: string; nodeIds: string[] } | { nodeId: string },
  ): Promise<SubmodelCreateResult> => {
    if (useGraphStore.getState().focus !== null) {
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
  }, [preservedBlocksRef, descriptionRef, fitView, addToast, beginTransformRequest, transformRequestIsStale, reservedApiInputFrameLabels, resolveCanonicalIdentities, setSelectedNode, setLastSelectedId])

  const handleCreateSubmodel = useCallback((name: string, nodeIds: string[]) =>
    createSubmodelTransform({ name, nodeIds }), [createSubmodelTransform])
  const handleCreateNodeInstance = useCallback((nodeId: string) =>
    createSubmodelTransform({ nodeId }), [createSubmodelTransform])

  // Opening a submodel is a projection of the document the editor holds, so it
  // asks the server nothing and commits at once.
  const handleDrillIntoSubmodel = useCallback((nodeId: string) => {
    try {
      const { nodes: parentNodes, submodels } = useGraphStore.getState()
      const occurrence = canonicalOccurrence(nodeId, parentNodes, submodels)
      if (!documentNavigationFence().allowed) {
        return
      }
      // A create or dissolve still waiting on the server belongs to the view
      // being left.
      transformRequestSerialRef.current += 1
      const displayName = occurrence.label
      const parentSourceFile = sourceFileRef.current
      const submodelSourceFile = occurrence.definition.file

      // The store draws the view from the document, and refuses one that
      // cannot open before anything else changes.
      useGraphStore.getState().enterSubmodelView({
        instanceId: occurrence.instanceId,
        definitionId: occurrence.definitionId,
      })
      const nextStack: ViewLevel[] = [
        { ...viewStackRef.current[0], file: parentSourceFile },
        {
          type: "submodel",
          name: displayName,
          file: submodelSourceFile,
          instanceId: occurrence.instanceId,
          definitionId: occurrence.definitionId,
          readOnly: occurrence.readOnly,
        },
      ]
      viewStackRef.current = nextStack
      setViewStack(nextStack)
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
  }, [sourceFileRef, fitView, addToast, leaveView])

  const handleBreadcrumbNavigate = useCallback((depth: number) => {
    const prev = viewStackRef.current
    if (depth >= prev.length - 1) return
    // Submodels do not nest (the store refuses a second view), so every
    // breadcrumb below the active one is the main pipeline.
    if (depth !== 0 || useGraphStore.getState().focus === null) {
      throw new Error(`Breadcrumb ${depth} has no root graph to return to`)
    }
    transformRequestSerialRef.current += 1
    const target = prev[0]
    useGraphStore.getState().exitSubmodelView()
    sourceFileRef.current = target.file
    const nextStack = prev.slice(0, 1)
    viewStackRef.current = nextStack
    setViewStack(nextStack)
    leaveView()
    setTimeout(() => fitView({ padding: 0.8 }), 100)
  }, [sourceFileRef, fitView, leaveView])

  const resetToAuthoritativeRoot = useCallback((sourceFile: string, pipelineName: string) => {
    transformRequestSerialRef.current += 1
    const rootView: ViewLevel[] = [{
      type: "pipeline",
      name: pipelineName || "main",
      file: sourceFile,
    }]
    viewStackRef.current = rootView
    setViewStack(rootView)
  }, [])

  // The store's focus is the open view, whatever moved it: an Undo or Redo of a
  // step made in another view shows that view, and one whose submodel is gone
  // shows the main pipeline. The breadcrumbs and source file follow it, and
  // what belonged to the view left ends, as when the user navigates.
  const focusKey = useGraphStore((state) =>
    state.focus ? JSON.stringify([state.focus.instanceId, state.focus.definitionId]) : null,
  )
  useLayoutEffect(() => {
    const top = viewStackRef.current[viewStackRef.current.length - 1]
    const shown = top?.type === "submodel" ? JSON.stringify([top.instanceId, top.definitionId]) : null
    if (shown === focusKey) return
    transformRequestSerialRef.current += 1
    const root = viewStackRef.current[0]
    const { focus, submodels } = useGraphStore.getState()
    let nextStack: ViewLevel[] = [root]
    if (focus) {
      const occurrence = canonicalOccurrence(focus.instanceId, focus.rootNodes, submodels)
      nextStack = [root, {
        type: "submodel",
        name: occurrence.label,
        file: occurrence.definition.file,
        instanceId: occurrence.instanceId,
        definitionId: occurrence.definitionId,
        readOnly: occurrence.readOnly,
      }]
      sourceFileRef.current = occurrence.definition.file
    } else {
      sourceFileRef.current = root.file
    }
    viewStackRef.current = nextStack
    setViewStack(nextStack)
    leaveView()
  }, [focusKey, leaveView, sourceFileRef])

  const handleDocumentReload = useCallback((
    reloaded: { nodes: Node[]; edges: Edge[] },
  ) => {
    transformRequestSerialRef.current += 1
    const { nodes: reloadedNodes, edges: reloadedEdges } = reloaded
    const activeView = viewStackRef.current[viewStackRef.current.length - 1]
    if (activeView?.type !== "submodel") return

    // The synchronizer has installed a new root snapshot, which closed the
    // open submodel, so breadcrumbs and canvas describe the same document.
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
    if (useGraphStore.getState().focus !== null) {
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
        preservedBlocksRef.current = flat.preserved_blocks ?? request.preservedBlocks
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
  }, [preservedBlocksRef, descriptionRef, fitView, addToast, beginTransformRequest, transformRequestIsStale, reservedApiInputFrameLabels, resolveCanonicalIdentities])

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
