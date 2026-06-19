import { useCallback, useEffect, useRef, useState } from "react"
import type { Node, Edge } from "@xyflow/react"
import type { ViewLevel } from "../components/BreadcrumbBar"
import { getLayoutedElements } from "../utils/layout"
import { normalizeEdges } from "../utils/graphHelpers"
import { buildSubmodelBoundary } from "../utils/submodelBoundary"
import { createSubmodel, loadSubmodel, dissolveSubmodel } from "../api/client"
import useToastStore from "../stores/useToastStore"

interface SubmodelNavParams {
  graphRef: React.MutableRefObject<{ nodes: Node[]; edges: Edge[] }>
  parentGraphRef: React.MutableRefObject<{ nodes: Node[]; edges: Edge[]; submodels: Record<string, unknown> } | null>
  submodelsRef: React.MutableRefObject<Record<string, unknown>>
  setNodesRaw: (nodes: Node[]) => void
  setEdgesRaw: (edges: Edge[]) => void
  setSelectedNode: (node: Node | null) => void
  setPreviewData: (data: null) => void
  preambleRef: React.MutableRefObject<string>
  descriptionRef: React.MutableRefObject<string>
  sourceFileRef: React.MutableRefObject<string>
  pipelineNameRef: React.MutableRefObject<string>
  fitView: (options?: { padding?: number }) => void
}

export interface SubmodelNavReturn {
  viewStack: ViewLevel[]
  /**
   * Drill into a submodel, replacing the canvas with its internal graph.
   * `selectChildId` (used by node-explosion peek click-to-drill) selects that
   * child on the submodel canvas once layout completes; omitted for the normal
   * double-click / header-"Open" path, which lands with nothing selected.
   */
  handleDrillIntoSubmodel: (nodeId: string, selectChildId?: string) => Promise<void>
  handleBreadcrumbNavigate: (depth: number) => void
  handleCreateSubmodel: (name: string, nodeIds: string[]) => Promise<void>
  handleDissolveSubmodel: (smName: string) => Promise<void>
}

export default function useSubmodelNavigation({
  graphRef, parentGraphRef, submodelsRef,
  setNodesRaw, setEdgesRaw,
  setSelectedNode, setPreviewData,
  preambleRef, descriptionRef, sourceFileRef, pipelineNameRef,
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
      })
      const newGraph = data.graph
      if (newGraph) {
        setNodesRaw(newGraph.nodes ?? [])
        setEdgesRaw(normalizeEdges(newGraph.edges ?? []))
        submodelsRef.current = newGraph.submodels ?? {}
        addToast("success", `Wrapper "${name}" created`)
        // Dirty is derived — the graph replacement itself triggers the
        // selectIsDirty comparison at the next render.
        setTimeout(() => fitView({ padding: 0.8 }), 100)
      }
    } catch (err: unknown) {
      addToast("error", `Create submodel failed: ${err instanceof Error ? err.message : String(err)}`)
    }
  }, [graphRef, submodelsRef, setNodesRaw, setEdgesRaw, preambleRef, descriptionRef, sourceFileRef, pipelineNameRef, fitView, addToast])

  const handleDrillIntoSubmodel = useCallback(async (nodeId: string, selectChildId?: string) => {
    const smName = nodeId.replace("submodel__", "")
    try {
      const data = await loadSubmodel(smName)
      const smGraph = data.graph
      if (smGraph) {
        const parentSourceFile = sourceFileRef.current
        const submodelSourceFile = `modules/${smName}.py`
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
        const newNodes: Node[] = smGraph.nodes ?? []
        const newEdges: Edge[] = normalizeEdges(smGraph.edges ?? [])

        // Derive the I/O boundary (port nodes + dashed child links) from the
        // parent's cross-boundary edges. Shared with the read-only peek, so the
        // peek mirrors exactly the canvas this drill-in produces.
        const { portNodes, boundaryEdges } = buildSubmodelBoundary({
          smNodeId: `submodel__${smName}`,
          parentNodes,
          parentEdges,
          childIds: new Set(newNodes.map((n: Node) => n.id)),
        })
        newNodes.push(...portNodes)
        newEdges.push(...boundaryEdges)

        const layouted = await getLayoutedElements(newNodes, newEdges)
        // Click-to-drill from a peek selects the clicked child on the submodel
        // canvas: mark it selected in the raw nodes (React Flow's `selected`
        // prop) and open the panel on it. The normal drill path passes no id
        // and lands with nothing selected.
        const childToSelect = selectChildId
          ? layouted.find((n) => n.id === selectChildId)
          : undefined
        const finalNodes = childToSelect
          ? layouted.map((n) => (n.id === selectChildId ? { ...n, selected: true } : n))
          : layouted
        setNodesRaw(finalNodes)
        setEdgesRaw(newEdges)
        setSelectedNode(childToSelect ? { ...childToSelect, selected: true } : null)
        setPreviewData(null)
        setTimeout(() => fitView({ padding: 0.8 }), 100)
      }
    } catch (err: unknown) {
      addToast("error", `Drill-down failed: ${err instanceof Error ? err.message : String(err)}`)
    }
  }, [graphRef, parentGraphRef, submodelsRef, setNodesRaw, setEdgesRaw, setSelectedNode, setPreviewData, sourceFileRef, fitView, addToast])

  const handleBreadcrumbNavigate = useCallback((depth: number) => {
    const prev = viewStackRef.current
    if (depth >= prev.length - 1) return
    const target = prev[depth]
    if (target._savedNodes && target._savedEdges) {
      setNodesRaw(target._savedNodes)
      setEdgesRaw(normalizeEdges(target._savedEdges))
      setSelectedNode(null)
      setPreviewData(null)
      setTimeout(() => fitView({ padding: 0.8 }), 100)
    }
    if (depth === 0) parentGraphRef.current = null
    sourceFileRef.current = target.file
    setViewStack(prev.slice(0, depth + 1))
  }, [parentGraphRef, sourceFileRef, setNodesRaw, setEdgesRaw, setSelectedNode, setPreviewData, fitView])

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
      })
      const flat = data.graph
      if (flat) {
        setNodesRaw(flat.nodes ?? [])
        setEdgesRaw(normalizeEdges(flat.edges ?? []))
        submodelsRef.current = data.graph?.submodels ?? submodelsRef.current
        addToast("success", `Wrapper "${smName}" dissolved`)
        // Dirty is derived — the graph replacement itself triggers the
        // selectIsDirty comparison at the next render.
        setTimeout(() => fitView({ padding: 0.8 }), 100)
      }
    } catch (err: unknown) {
      addToast("error", `Dissolve failed: ${err instanceof Error ? err.message : String(err)}`)
    }
  }, [graphRef, submodelsRef, setNodesRaw, setEdgesRaw, preambleRef, descriptionRef, sourceFileRef, pipelineNameRef, fitView, addToast])

  return {
    viewStack,
    handleDrillIntoSubmodel,
    handleBreadcrumbNavigate,
    handleCreateSubmodel,
    handleDissolveSubmodel,
  }
}
