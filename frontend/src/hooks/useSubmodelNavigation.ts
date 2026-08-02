import { useCallback, useEffect, useRef, useState } from "react"
import type { Node, Edge } from "@xyflow/react"
import type { ViewLevel } from "../components/BreadcrumbBar"
import { getLayoutedElements } from "../utils/layout"
import { normalizeEdges } from "../utils/graphHelpers"
import { buildSubmodelViewGraph } from "../utils/submodelViewGraph"
import { createSubmodel, loadSubmodel, dissolveSubmodel } from "../api/client"
import useToastStore from "../stores/useToastStore"

interface SubmodelNavParams {
  graphRef: React.MutableRefObject<{ nodes: Node[]; edges: Edge[] }>
  parentGraphRef: React.MutableRefObject<{ nodes: Node[]; edges: Edge[]; submodels: Record<string, unknown> } | null>
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
  setPreamble: (preamble: string) => void
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
  handleDissolveSubmodel: (smName: string) => Promise<void>
}

export default function useSubmodelNavigation({
  graphRef, parentGraphRef, submodelsRef,
  setNodesRaw, setEdgesRaw, setSubmodelsRaw,
  setSelectedNode, setLastSelectedId, setCurrentSourceFile, setPreviewData,
  preambleRef, sourceRevisionRef, preservedBlocksRef, setPreamble, descriptionRef, sourceFileRef, pipelineNameRef,
  fitView,
}: SubmodelNavParams): SubmodelNavReturn {
  const addToast = useToastStore((s) => s.addToast)
  const [viewStack, setViewStack] = useState<ViewLevel[]>([{ type: "pipeline", name: "main", file: "" }])
  const viewStackRef = useRef(viewStack)
  useEffect(() => { viewStackRef.current = viewStack }, [viewStack])

  const handleCreateSubmodel = useCallback(async (name: string, nodeIds: string[]) => {
    try {
      const graph = { nodes: graphRef.current.nodes, edges: graphRef.current.edges, submodels: submodelsRef.current }
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
        setNodesRaw(newGraph.nodes ?? [])
        setEdgesRaw(normalizeEdges(newGraph.edges ?? []))
        const nextSubmodels = newGraph.submodels ?? {}
        sourceRevisionRef.current = data.source_revision
        submodelsRef.current = nextSubmodels
        setSubmodelsRaw?.(nextSubmodels)
        addToast("success", `Submodel "${name}" created`)
        // Dirty is derived — the graph replacement itself triggers the
        // selectIsDirty comparison at the next render.
        setTimeout(() => fitView({ padding: 0.8 }), 100)
      }
    } catch (err: unknown) {
      addToast("error", `Create submodel failed: ${err instanceof Error ? err.message : String(err)}`)
    }
  }, [graphRef, submodelsRef, setNodesRaw, setEdgesRaw, setSubmodelsRaw, preambleRef, sourceRevisionRef, preservedBlocksRef, descriptionRef, sourceFileRef, pipelineNameRef, fitView, addToast])

  const handleDrillIntoSubmodel = useCallback(async (nodeId: string) => {
    const smName = nodeId.replace("submodel__", "")
    try {
      const data = await loadSubmodel(smName, sourceFileRef.current)
      const smGraph = data.graph
      if (smGraph) {
        const parentSourceFile = sourceFileRef.current
        const submodelSourceFile = data.submodel_file
        const parentNodes = [...graphRef.current.nodes]
        const parentEdges = [...graphRef.current.edges]
        parentGraphRef.current = { nodes: parentNodes, edges: parentEdges, submodels: { ...submodelsRef.current } }
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
          return [...updated, { type: "submodel" as const, name: smName, file: submodelSourceFile }]
        })
        sourceFileRef.current = submodelSourceFile
        setCurrentSourceFile?.(submodelSourceFile)
        setLastSelectedId?.(null)
        const projected = buildSubmodelViewGraph({
          submodelName: smName,
          childNodes: smGraph.nodes ?? [],
          childEdges: normalizeEdges(smGraph.edges ?? []),
          parentNodes,
          parentEdges,
        })
        const layouted = await getLayoutedElements(
          projected.nodes,
          projected.edges,
        )
        setNodesRaw(layouted)
        setEdgesRaw(projected.edges)
        setSelectedNode(null)
        setPreviewData(null)
        setTimeout(() => fitView({ padding: 0.8 }), 100)
      }
    } catch (err: unknown) {
      addToast("error", `Drill-down failed: ${err instanceof Error ? err.message : String(err)}`)
    }
  }, [graphRef, parentGraphRef, submodelsRef, setNodesRaw, setEdgesRaw, setSelectedNode, setLastSelectedId, setCurrentSourceFile, setPreviewData, sourceFileRef, fitView, addToast])

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
    setViewStack(prev.slice(0, depth + 1))
  }, [parentGraphRef, submodelsRef, sourceFileRef, setNodesRaw, setEdgesRaw, setSubmodelsRaw, setSelectedNode, setLastSelectedId, setCurrentSourceFile, setPreviewData, fitView])

  const handleDissolveSubmodel = useCallback(async (smName: string) => {
    try {
      const graph = { nodes: graphRef.current.nodes, edges: graphRef.current.edges, submodels: submodelsRef.current }
      const data = await dissolveSubmodel({
        submodel_name: smName,
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
        setNodesRaw(flat.nodes ?? [])
        setEdgesRaw(normalizeEdges(flat.edges ?? []))
        const nextSubmodels = flat.submodels ?? {}
        const nextPreamble = flat.preamble ?? ""
        setPreamble(nextPreamble)
        preambleRef.current = nextPreamble
        preservedBlocksRef.current = flat.preserved_blocks ?? []
        sourceRevisionRef.current = data.source_revision
        submodelsRef.current = nextSubmodels
        setSubmodelsRaw?.(nextSubmodels)
        addToast("success", data.retained_submodel_file
          ? `Submodel "${smName}" dissolved; retained ${data.retained_submodel_file}`
          : `Submodel "${smName}" dissolved`)
        // Dirty is derived — the graph replacement itself triggers the
        // selectIsDirty comparison at the next render.
        setTimeout(() => fitView({ padding: 0.8 }), 100)
      }
    } catch (err: unknown) {
      addToast("error", `Dissolve failed: ${err instanceof Error ? err.message : String(err)}`)
    }
  }, [graphRef, submodelsRef, setNodesRaw, setEdgesRaw, setSubmodelsRaw, preambleRef, sourceRevisionRef, preservedBlocksRef, setPreamble, descriptionRef, sourceFileRef, pipelineNameRef, fitView, addToast])

  return {
    viewStack,
    handleDrillIntoSubmodel,
    handleBreadcrumbNavigate,
    handleCreateSubmodel,
    handleDissolveSubmodel,
  }
}
