import { useCallback, useEffect, useRef, useState } from "react"
import type { Node, Edge } from "@xyflow/react"
import type { ViewLevel } from "../components/BreadcrumbBar"
import { getLayoutedElements } from "../utils/layout"
import { normalizeEdges } from "../utils/graphHelpers"
import { buildSubmodelViewGraph } from "../utils/submodelViewGraph"
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
}

export interface SubmodelNavReturn {
  viewStack: ViewLevel[]
  handleDrillIntoSubmodel: (nodeId: string) => Promise<void>
  handleBreadcrumbNavigate: (depth: number) => void
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
  const label = typeof node.data.label === "string" && node.data.label.length > 0
    ? node.data.label
    : config.alias
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
  graphRef, parentGraphRef, setActiveSubmodelIdentity, submodelsRef,
  setNodesRaw, setEdgesRaw, setSubmodelsRaw,
  setSelectedNode, setLastSelectedId, setCurrentSourceFile, setPreviewData,
  preambleRef, sourceRevisionRef, preservedBlocksRef, descriptionRef, sourceFileRef, pipelineNameRef,
  fitView,
}: SubmodelNavParams): SubmodelNavReturn {
  const addToast = useToastStore((s) => s.addToast)
  const [viewStack, setViewStack] = useState<ViewLevel[]>([{ type: "pipeline", name: "main", file: "" }])
  const viewStackRef = useRef(viewStack)
  useEffect(() => { viewStackRef.current = viewStack }, [viewStack])

  const handleCreateSubmodel = useCallback(async (name: string, nodeIds: string[]) => {
    if (parentGraphRef.current) {
      addToast("error", "Return to the main pipeline before creating a submodel.")
      return
    }
    try {
      const { nodes, edges, submodels } = useGraphStore.getState()
      const graph = { nodes, edges, submodels }
      const data = await createSubmodel({
        name,
        node_ids: nodeIds,
        graph,
        preamble: preambleRef.current,
        source_file: sourceFileRef.current,
        pipeline_name: pipelineNameRef.current,
        pipeline_description: descriptionRef.current,
        base_revision: sourceRevisionRef.current,
        preserved_blocks: preservedBlocksRef.current,
      })
      const newGraph = data.graph
      if (newGraph) {
        const nextNodes = newGraph.nodes ?? []
        const nextEdges = normalizeEdges(newGraph.edges ?? [])
        const nextSubmodels = newGraph.submodels ?? {}
        const nextPreamble = newGraph.preamble ?? preambleRef.current
        const nextPreservedBlocks = newGraph.preserved_blocks ?? preservedBlocksRef.current
        graphRef.current = { nodes: nextNodes, edges: nextEdges }
        submodelsRef.current = nextSubmodels
        preambleRef.current = nextPreamble
        preservedBlocksRef.current = nextPreservedBlocks
        useGraphStore.getState().setNodesAndEdgesAndSubmodels(
          nextNodes,
          nextEdges,
          nextSubmodels,
          nextPreamble,
        )
        addToast("success", `Submodel "${name}" created — save to apply`)
        setTimeout(() => fitView({ padding: 0.8 }), 100)
      }
    } catch (err: unknown) {
      addToast("error", `Create submodel failed: ${err instanceof Error ? err.message : String(err)}`)
    }
  }, [graphRef, parentGraphRef, submodelsRef, preambleRef, sourceRevisionRef, preservedBlocksRef, descriptionRef, sourceFileRef, pipelineNameRef, fitView, addToast])

  const handleDrillIntoSubmodel = useCallback(async (nodeId: string) => {
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
      const layouted = await getLayoutedElements(projected.nodes, projected.edges)

      // Commit navigation only after loading, projection, and layout all succeed.
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
      setEdgesRaw(projected.edges)
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
  }, [graphRef, parentGraphRef, setActiveSubmodelIdentity, submodelsRef, setNodesRaw, setEdgesRaw, setSelectedNode, setLastSelectedId, setCurrentSourceFile, setPreviewData, sourceFileRef, fitView, addToast])

  const handleBreadcrumbNavigate = useCallback((depth: number) => {
    const prev = viewStackRef.current
    if (depth >= prev.length - 1) return
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

  const handleDissolveSubmodel = useCallback(async (instanceId: string) => {
    if (parentGraphRef.current) {
      addToast("error", "Return to the main pipeline before dissolving a submodel.")
      return
    }
    try {
      const occurrence = canonicalOccurrence(
        instanceId,
        graphRef.current.nodes,
        submodelsRef.current,
      )
      const displayName = occurrence.label
      const graph = {
        nodes: graphRef.current.nodes,
        edges: graphRef.current.edges,
        submodels: submodelsRef.current,
      }
      const data = await dissolveSubmodel({
        instance_id: occurrence.instanceId,
        graph,
        preamble: preambleRef.current,
        source_file: sourceFileRef.current,
        pipeline_name: pipelineNameRef.current,
        pipeline_description: descriptionRef.current,
        base_revision: sourceRevisionRef.current,
        preserved_blocks: preservedBlocksRef.current,
      })
      const flat = data.graph
      if (flat) {
        const nextNodes = flat.nodes ?? []
        const nextEdges = normalizeEdges(flat.edges ?? [])
        const nextSubmodels = flat.submodels ?? {}
        const nextPreamble = flat.preamble ?? preambleRef.current
        graphRef.current = { nodes: nextNodes, edges: nextEdges }
        preambleRef.current = nextPreamble
        preservedBlocksRef.current = flat.preserved_blocks ?? preservedBlocksRef.current
        submodelsRef.current = nextSubmodels
        useGraphStore.getState().setNodesAndEdgesAndSubmodels(
          nextNodes,
          nextEdges,
          nextSubmodels,
          nextPreamble,
        )
        addToast("success", `Submodel "${displayName}" dissolved — save to apply`)
        setTimeout(() => fitView({ padding: 0.8 }), 100)
      }
    } catch (err: unknown) {
      addToast("error", `Dissolve failed: ${err instanceof Error ? err.message : String(err)}`)
    }
  }, [graphRef, parentGraphRef, submodelsRef, preambleRef, sourceRevisionRef, preservedBlocksRef, descriptionRef, sourceFileRef, pipelineNameRef, fitView, addToast])

  return {
    viewStack,
    handleDrillIntoSubmodel,
    handleBreadcrumbNavigate,
    handleCreateSubmodel,
    handleDissolveSubmodel,
  }
}
