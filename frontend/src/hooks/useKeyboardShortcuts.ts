import { useEffect, useRef } from "react"
import type { Node, Edge } from "@xyflow/react"
import useToastStore from "../stores/useToastStore"
import useUIStore from "../stores/useUIStore"
import useNodeResultsStore from "../stores/useNodeResultsStore"
import { isProtectedSubmodelNodeData, nodeData } from "../types/node"
import { isSingletonType } from "../utils/nodeTypes"
import { requestSubmodelCreation } from "../utils/submodelCreation"
import type { SharedNodeDeletionResult } from "./useSubmodelBoundaryEditing"

interface KeyboardShortcutsParams {
  handleSave: () => void
  setNodes: (updater: Node[] | ((nds: Node[]) => Node[])) => void
  setEdges: (updater: Edge[] | ((eds: Edge[]) => Edge[])) => void
  setNodesAndEdges: (
    nodes: Node[] | ((nds: Node[]) => Node[]),
    edges: Edge[] | ((eds: Edge[]) => Edge[]),
  ) => void
  undo: () => void
  redo: () => void
  fitView: (options?: { padding?: number }) => void
  graphRef: React.MutableRefObject<{ nodes: Node[]; edges: Edge[] }>
  clipboard: React.MutableRefObject<{ nodes: Node[]; edges: Edge[] }>
  nodeIdCounter: React.MutableRefObject<number>
  setSelectedNode: (node: Node | null) => void
  setLastSelectedId?: (id: string | null) => void
  setPreviewData: (data: null) => void
  clearTrace: () => void
  closePanel: () => void
  isInsideSubmodel: boolean
  readOnly: boolean
  resolveGraphIdentities: (
    nodes: readonly Node[],
    edges: readonly Edge[],
  ) => Promise<{ nodes: Node[]; edges: Edge[] }>
  commitSharedNodeDeletion?: (
    nodeIds: ReadonlySet<string>,
    selectedEdgeIds?: ReadonlySet<string>,
  ) => SharedNodeDeletionResult
}

function assertResolvedPasteMatchesCandidate(
  candidateNodes: readonly Node[],
  candidateEdges: readonly Edge[],
  resolved: { nodes: readonly Node[]; edges: readonly Edge[] },
): void {
  if (
    resolved.nodes.length !== candidateNodes.length
    || resolved.nodes.some((node, index) => node.id !== candidateNodes[index]?.id)
  ) {
    throw new Error("identity resolver returned an invalid node batch")
  }
  if (
    resolved.edges.length !== candidateEdges.length
    || resolved.edges.some((edge, index) => {
      const candidate = candidateEdges[index]
      return !candidate
        || edge.id !== candidate.id
        || edge.source !== candidate.source
        || edge.target !== candidate.target
        || (edge.sourceHandle ?? null) !== (candidate.sourceHandle ?? null)
        || (edge.targetHandle ?? null) !== (candidate.targetHandle ?? null)
    })
  ) {
    throw new Error("identity resolver returned an invalid edge batch")
  }
}

export default function useKeyboardShortcuts({
  handleSave, setNodes, setEdges, setNodesAndEdges, undo, redo, fitView,
  graphRef, clipboard, nodeIdCounter,
  setSelectedNode, setLastSelectedId, setPreviewData, clearTrace, closePanel,
  isInsideSubmodel, readOnly,
  resolveGraphIdentities,
  commitSharedNodeDeletion,
}: KeyboardShortcutsParams) {
  const addToast = useToastStore((s) => s.addToast)
  const { setShortcutsOpen, setSubmodelDialog, setNodeSearchOpen } = useUIStore()
  const pasteRequestSerialRef = useRef(0)
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName
      const el = e.target as HTMLElement
      const isTyping = tag === "INPUT" || tag === "TEXTAREA" || el.closest?.(".cm-editor") != null
      const mod = e.ctrlKey || e.metaKey

      // Ctrl+S / Cmd+S → save
      if (mod && e.key === "s") {
        e.preventDefault()
        handleSave()
        return
      }

      // Ctrl+Z → undo, Ctrl+Shift+Z → redo
      if (mod && e.key === "z" && !e.shiftKey && !isTyping) {
        e.preventDefault()
        if (readOnly) return
        undo()
        return
      }
      if (mod && e.key === "z" && e.shiftKey && !isTyping) {
        e.preventDefault()
        if (readOnly) return
        redo()
        return
      }
      // Ctrl+Y → redo (Windows convention)
      if (mod && e.key === "y" && !isTyping) {
        e.preventDefault()
        if (readOnly) return
        redo()
        return
      }

      // Ctrl+C → copy selected nodes
      if (mod && e.key === "c" && !isTyping) {
        const { nodes: currentNodes, edges: currentEdges } = graphRef.current
        const selected = currentNodes.filter((n) => n.selected)
        if (selected.length === 0) return
        const selectedIds = new Set(selected.map((n) => n.id))
        const internalEdges = currentEdges.filter(
          (ed) => selectedIds.has(ed.source) && selectedIds.has(ed.target)
        )
        clipboard.current = { nodes: selected, edges: internalEdges }
        addToast("info", `Copied ${selected.length} node${selected.length > 1 ? "s" : ""}`)
        return
      }

      // Ctrl+V → paste copied nodes
      if (mod && e.key === "v" && !isTyping) {
        if (readOnly) {
          e.preventDefault()
          return
        }
        const { nodes: copiedNodes, edges: copiedEdges } = clipboard.current
        if (copiedNodes.length === 0) return
        e.preventDefault()
        const capturedGraph = graphRef.current
        // Filter out singleton types that already exist in the graph
        const existingSingletonTypes = new Set<string>()
        for (const n of capturedGraph.nodes) {
          const nt = nodeData(n).nodeType
          if (isSingletonType(nt)) existingSingletonTypes.add(nt!)
        }
        const pasteable = copiedNodes.filter((n) => {
          const nt = nodeData(n).nodeType
          return !(isSingletonType(nt) && existingSingletonTypes.has(nt!))
        })
        if (pasteable.length === 0) return
        const idMap = new Map<string, string>()
        const newNodes: Node[] = pasteable.map((n) => {
          nodeIdCounter.current += 1
          const newId = `${n.type}_${nodeIdCounter.current}`
          idMap.set(n.id, newId)
          return {
            ...n,
            id: newId,
            position: { x: n.position.x + 60, y: n.position.y + 60 },
            selected: true,
            data: { ...n.data, label: `${n.data.label} copy` },
          }
        })
        const newEdges: Edge[] = copiedEdges.flatMap((ed) => {
          const newSource = idMap.get(ed.source)
          const newTarget = idMap.get(ed.target)
          if (!newSource || !newTarget) return []
          return [{ ...ed, id: `e-${newSource}-${newTarget}`, source: newSource, target: newTarget }]
        })
        const requestSerial = ++pasteRequestSerialRef.current
        void resolveGraphIdentities(newNodes, newEdges).then((resolved) => {
          if (
            pasteRequestSerialRef.current !== requestSerial
            || graphRef.current !== capturedGraph
          ) {
            addToast(
              "error",
              "Paste was not applied because the graph changed while identity resolution was running.",
            )
            return
          }
          assertResolvedPasteMatchesCandidate(newNodes, newEdges, resolved)
          // Pasted nodes + their internal edges land in ONE undo step, so a
          // single ⌘/Ctrl-Z removes the whole paste. The captured graph is
          // still current here; any intervening mutation rejects above.
          setNodesAndEdges(
            [
              ...capturedGraph.nodes.map((node) => ({ ...node, selected: false })),
              ...resolved.nodes,
            ],
            [...capturedGraph.edges, ...resolved.edges],
          )
          addToast("info", `Pasted ${resolved.nodes.length} node${resolved.nodes.length > 1 ? "s" : ""}`)
        }).catch((error: unknown) => {
          if (
            pasteRequestSerialRef.current !== requestSerial
            || graphRef.current !== capturedGraph
          ) {
            addToast(
              "error",
              "Paste was not applied because the graph changed while identity resolution was running.",
            )
            return
          }
          addToast(
            "error",
            `Paste failed: ${error instanceof Error ? error.message : String(error)}`,
          )
        })
        return
      }

      // Ctrl+A → select all nodes
      if (mod && e.key === "a" && !isTyping) {
        e.preventDefault()
        setNodes((nds) => nds.map((n) => ({ ...n, selected: true })))
        return
      }

      // Ctrl+1 → fit view
      if (mod && e.key === "1") {
        e.preventDefault()
        fitView({ padding: 0.8 })
        return
      }

      // Ctrl+K → open node search
      if (mod && e.key === "k" && (!isTyping || useUIStore.getState().nodeSearchOpen)) {
        e.preventDefault()
        setNodeSearchOpen((prev) => !prev)
        return
      }

      // Escape → clear trace + close panel
      if (e.key === "Escape" && !isTyping) {
        clearTrace()
        closePanel()
        return
      }

      // ? → toggle keyboard shortcuts help (unless typing)
      if (e.key === "?" && !isTyping) {
        e.preventDefault()
        setShortcutsOpen((prev) => !prev)
        return
      }

      // Ctrl+G → group selected nodes into a submodel
      if (mod && e.key === "g" && !isTyping) {
        e.preventDefault()
        requestSubmodelCreation({
          nodes: graphRef.current.nodes,
          readOnly,
          isInsideSubmodel,
          setSubmodelDialog,
          addToast,
        })
        return
      }

      // Delete / Backspace → remove selected nodes and/or edges (unless typing)
      if ((e.key === "Delete" || e.key === "Backspace") && !isTyping) {
        if (readOnly) {
          e.preventDefault()
          return
        }
        const { nodes: currentNodes, edges: currentEdges } = graphRef.current
        const selectedNodeIds = new Set(currentNodes.filter((n) => n.selected).map((n) => n.id))
        const selectedEdgeIds = new Set(currentEdges.filter((ed) => ed.selected).map((ed) => ed.id))
        if (selectedNodeIds.size === 0 && selectedEdgeIds.size === 0) return
        // Owner occurrences anchor their shared definition: dissolve them,
        // never raw-delete. Copies and ordinary nodes in the selection still
        // delete, matching the context-menu and panel guards.
        const protectedOwnerIds = currentNodes
          .filter((n) => selectedNodeIds.has(n.id) && isProtectedSubmodelNodeData(nodeData(n)))
          .map((n) => n.id)
        if (protectedOwnerIds.length > 0) {
          addToast("error", 'Use "Dissolve Submodel" to remove a submodel owner; instance copies delete directly')
          for (const ownerId of protectedOwnerIds) selectedNodeIds.delete(ownerId)
          if (selectedNodeIds.size === 0 && selectedEdgeIds.size === 0) return
        }
        if (selectedNodeIds.size > 0) {
          const sharedDeletion = commitSharedNodeDeletion?.(selectedNodeIds, selectedEdgeIds)
          if (sharedDeletion === "blocked") return
          // Nodes + their edges removed in ONE undo step. setNodes-then-setEdges
          // would push two snapshots, so one delete would take two undos to
          // reverse (the undo-atomicity bug class).
          if (sharedDeletion !== "committed") {
            setNodesAndEdges(
              currentNodes.filter((n) => !selectedNodeIds.has(n.id)),
              currentEdges.filter((ed) => !selectedNodeIds.has(ed.source) && !selectedNodeIds.has(ed.target)),
            )
          }
          setSelectedNode(null)
          setLastSelectedId?.(null)
          setPreviewData(null)
          // Clean up store state for deleted nodes
          for (const nid of selectedNodeIds) {
            useNodeResultsStore.getState().clearNode(nid)
          }
        } else {
          // Pure-edge delete: only edges selected, no nodes — a single setEdges
          // is already one snapshot.
          setEdges(currentEdges.filter((ed) => !selectedEdgeIds.has(ed.id)))
        }
      }
    }
    window.addEventListener("keydown", handler)
    return () => window.removeEventListener("keydown", handler)
  }, [
    handleSave, setNodes, setEdges, setNodesAndEdges, undo, redo, fitView,
    graphRef, clipboard, nodeIdCounter,
    setSelectedNode, setLastSelectedId, setPreviewData, clearTrace, closePanel,
    addToast, setShortcutsOpen, setSubmodelDialog, setNodeSearchOpen, isInsideSubmodel, readOnly,
    resolveGraphIdentities, commitSharedNodeDeletion,
  ])
}
