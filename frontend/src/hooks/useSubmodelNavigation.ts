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
import { createSubmodel, dissolveSubmodel } from "../api/client"
import useToastStore from "../stores/useToastStore"
import useGraphStore from "../stores/useGraphStore"
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
  setSubmodelsRaw?: (submodels: Record<string, unknown>) => void
  setSelectedNode: (node: Node | null) => void
  setLastSelectedId?: (id: string | null) => void
  setCurrentSourceFile?: (sourceFile: string | null) => void
  setPreviewData: (data: null) => void
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

export interface SubmodelNavReturn {
  viewStack: ViewLevel[]
  handleDrillIntoSubmodel: (nodeId: string) => Promise<void>
  handleBreadcrumbNavigate: (depth: number) => void
  resetToAuthoritativeRoot: (sourceFile: string, pipelineName: string) => void
  handleDocumentReload: (reloaded: Node[] | { nodes: Node[]; edges?: Edge[] }) => void
  handleCreateSubmodel: (name: string, nodeIds: string[]) => Promise<void>
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
  setNodesRaw, setEdgesRaw, setSubmodelsRaw,
  setSelectedNode, setLastSelectedId, setCurrentSourceFile, setPreviewData,
  preambleRef, sourceRevisionRef, preservedBlocksRef, descriptionRef, sourceFileRef, pipelineNameRef,
  fitView, reservedApiInputFrameLabels, resolveGraphIdentities = resolveEditorGraphIdentities,
  resolveCanonicalIdentities = resolveCanonicalGraphIdentities,
}: SubmodelNavParams): SubmodelNavReturn {
  const addToast = useToastStore((s) => s.addToast)
  const [viewStack, setViewStack] = useState<ViewLevel[]>([{ type: "pipeline", name: "main", file: "" }])
  const viewStackRef = useRef(viewStack)
  const transformRequestSerialRef = useRef(0)
  useEffect(() => { viewStackRef.current = viewStack }, [viewStack])

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

  const handleCreateSubmodel = useCallback(async (name: string, nodeIds: string[]) => {
    if (parentGraphRef.current) {
      addToast("error", "Return to the main pipeline before creating a submodel.")
      return
    }
    try {
      const request = beginTransformRequest()
      const data = await createSubmodel({
        name,
        node_ids: nodeIds,
        graph: request.graph,
        preamble: request.preamble,
        source_file: request.sourceFile,
        pipeline_name: request.pipelineName,
        pipeline_description: descriptionRef.current,
        base_revision: request.sourceRevision,
        preserved_blocks: request.preservedBlocks,
      })
      if (transformRequestIsStale(request)) {
        addToast("error", "Create submodel was not applied because the workspace changed while the transform was running.")
        return
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
          addToast("error", "Create submodel was not applied because the workspace changed while the transform was running.")
          return
        }
        graphRef.current = { nodes: resolved.nodes, edges: resolved.edges }
        submodelsRef.current = resolved.submodels
        preambleRef.current = nextPreamble
        preservedBlocksRef.current = nextPreservedBlocks
        useGraphStore.getState().setNodesAndEdgesAndSubmodels(
          resolved.nodes,
          resolved.edges,
          resolved.submodels,
          nextPreamble,
        )
        addToast("success", `Submodel "${name}" created — save to apply`)
        setTimeout(() => fitView({ padding: 0.8 }), 100)
      }
    } catch (err: unknown) {
      addToast("error", `Create submodel failed: ${err instanceof Error ? err.message : String(err)}`)
    }
  }, [graphRef, parentGraphRef, submodelsRef, preambleRef, preservedBlocksRef, descriptionRef, fitView, addToast, beginTransformRequest, transformRequestIsStale, reservedApiInputFrameLabels, resolveCanonicalIdentities])

  const handleDrillIntoSubmodel = useCallback(async (nodeId: string) => {
    transformRequestSerialRef.current += 1
    try {
      const parentNodes = [...graphRef.current.nodes]
      const parentEdges = [...graphRef.current.edges]
      const occurrence = canonicalOccurrence(nodeId, parentNodes, submodelsRef.current)
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
      const layouted = await getLayoutedElements(resolved.nodes, resolved.edges)

      // Commit navigation only after projection, identity resolution, and layout all succeed.
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
        return [
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
      })
      sourceFileRef.current = submodelSourceFile
      setCurrentSourceFile?.(submodelSourceFile)
      setLastSelectedId?.(null)
      setNodesRaw(layouted)
      setEdgesRaw(resolved.edges)
      setSelectedNode(null)
      setPreviewData(null)
      const count = instanceCount(parentNodes, occurrence.definitionId)
      if (occurrence.readOnly) {
        addToast("info", `Viewing read-only instance "${displayName}". Edit the original submodel to change its definition.`)
      } else if (count > 1) {
        addToast("info", `Editing shared definition "${displayName}" affects ${count} instances.`)
      }
      setTimeout(() => fitView({ padding: 0.8 }), 100)
    } catch (err: unknown) {
      addToast("error", `Drill-down failed: ${err instanceof Error ? err.message : String(err)}`)
    }
  }, [graphRef, parentGraphRef, setActiveSubmodelIdentity, submodelsRef, setNodesRaw, setEdgesRaw, setSelectedNode, setLastSelectedId, setCurrentSourceFile, setPreviewData, sourceFileRef, fitView, addToast, reservedApiInputFrameLabels, resolveGraphIdentities])

  const handleBreadcrumbNavigate = useCallback((depth: number) => {
    const prev = viewStackRef.current
    if (depth >= prev.length - 1) return
    transformRequestSerialRef.current += 1
    const target = prev[depth]
    const reconciledParent = depth === 0 ? parentGraphRef.current : null
    const restoredNodes = reconciledParent?.nodes ?? target._savedNodes
    const restoredEdges = reconciledParent?.edges ?? target._savedEdges
    if (restoredNodes && restoredEdges) {
      setNodesRaw(restoredNodes)
      setEdgesRaw(normalizeEdges(restoredEdges))
      setSelectedNode(null)
      setLastSelectedId?.(null)
      setPreviewData(null)
      setTimeout(() => fitView({ padding: 0.8 }), 100)
    }
    if (reconciledParent) {
      submodelsRef.current = reconciledParent.submodels
      setSubmodelsRaw?.(reconciledParent.submodels)
    }
    if (depth === 0) parentGraphRef.current = null
    sourceFileRef.current = target.file
    setCurrentSourceFile?.(target.file || null)
    setActiveSubmodelIdentity(target.type === "submodel"
      ? { instanceId: target.instanceId, definitionId: target.definitionId }
      : null)
    setViewStack(prev.slice(0, depth + 1))
  }, [parentGraphRef, setActiveSubmodelIdentity, submodelsRef, sourceFileRef, setNodesRaw, setEdgesRaw, setSubmodelsRaw, setSelectedNode, setLastSelectedId, setCurrentSourceFile, setPreviewData, fitView])

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
    reloaded: Node[] | { nodes: Node[]; edges?: Edge[] },
  ) => {
    const reloadedNodes = Array.isArray(reloaded) ? reloaded : reloaded.nodes
    const reloadedEdges = Array.isArray(reloaded) ? undefined : reloaded.edges
    const activeView = viewStackRef.current[viewStackRef.current.length - 1]
    const drilledInstanceId =
      activeSubmodelIdentity?.instanceId ??
      (activeView?.type === "submodel" ? activeView.instanceId : null)
    if (!drilledInstanceId) return

    const existsInReloaded = reloadedNodes.some((node) => node.id === drilledInstanceId)
    if (!existsInReloaded) {
      transformRequestSerialRef.current += 1
      parentGraphRef.current = null
      setActiveSubmodelIdentity(null)
      const rootFile = viewStackRef.current[0]?.file || sourceFileRef.current
      const rootName = viewStackRef.current[0]?.name || pipelineNameRef.current || "main"
      sourceFileRef.current = rootFile
      setCurrentSourceFile?.(rootFile || null)
      const rootView: ViewLevel[] = [{
        type: "pipeline",
        name: rootName,
        file: rootFile,
      }]
      viewStackRef.current = rootView
      setViewStack(rootView)
      setNodesRaw(reloadedNodes)
      if (reloadedEdges) {
        setEdgesRaw(normalizeEdges(reloadedEdges))
      }
      setSelectedNode(null)
      setLastSelectedId?.(null)
      setPreviewData(null)
      setTimeout(() => fitView({ padding: 0.8 }), 100)
    }
  }, [
    activeSubmodelIdentity,
    parentGraphRef,
    setActiveSubmodelIdentity,
    sourceFileRef,
    pipelineNameRef,
    setCurrentSourceFile,
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
        addToast("success", `Submodel "${displayName}" dissolved — save to apply`)
        setTimeout(() => fitView({ padding: 0.8 }), 100)
      }
    } catch (err: unknown) {
      addToast("error", `Dissolve failed: ${err instanceof Error ? err.message : String(err)}`)
    }
  }, [graphRef, parentGraphRef, submodelsRef, preambleRef, preservedBlocksRef, descriptionRef, fitView, addToast, beginTransformRequest, transformRequestIsStale, reservedApiInputFrameLabels, resolveCanonicalIdentities])

  return {
    viewStack,
    handleDrillIntoSubmodel,
    handleBreadcrumbNavigate,
    resetToAuthoritativeRoot,
    handleDocumentReload,
    handleCreateSubmodel,
    handleDissolveSubmodel,
  }
}
