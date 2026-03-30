import { useEffect } from "react"
import type { Node, Edge } from "@xyflow/react"
import useToastStore from "../stores/useToastStore"
import useUIStore from "../stores/useUIStore"
import useNodeResultsStore from "../stores/useNodeResultsStore"

interface KeyboardShortcutsParams {
  handleSave: () => void
  setNodes: (updater: Node[] | ((nds: Node[]) => Node[])) => void
  setEdges: (updater: Edge[] | ((eds: Edge[]) => Edge[])) => void
  undo: () => void
  redo: () => void
  fitView: (options?: { padding?: number }) => void
  graphRef: React.MutableRefObject<{ nodes: Node[]; edges: Edge[] }>
  clipboard: React.MutableRefObject<{ nodes: Node[]; edges: Edge[] }>
  nodeIdCounter: React.MutableRefObject<number>
  setSelectedNode: (node: Node | null) => void
  setPreviewData: (data: null) => void
  clearTrace: () => void
  closePanel: () => void
  isInsideSubmodel: boolean
}

export default function useKeyboardShortcuts({
  handleSave, setNodes, setEdges, undo, redo, fitView,
  graphRef, clipboard, nodeIdCounter,
  setSelectedNode, setPreviewData, clearTrace, closePanel,
  isInsideSubmodel,
}: KeyboardShortcutsParams) {
  const addToast = useToastStore((s) => s.addToast)
  const { setShortcutsOpen, setSubmodelDialog, setNodeSearchOpen } = useUIStore()
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
      if (mod && e.key === "z" && !e.shiftKey) {
        e.preventDefault()
        undo()
        return
      }
      if (mod && e.key === "z" && e.shiftKey) {
        e.preventDefault()
        redo()
        return
      }
      // Ctrl+Y → redo (Windows convention)
      if (mod && e.key === "y") {
        e.preventDefault()
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
        const { nodes: copiedNodes, edges: copiedEdges } = clipboard.current
        if (copiedNodes.length === 0) return
        e.preventDefault()
        const idMap = new Map<string, string>()
        const newNodes: Node[] = copiedNodes.map((n) => {
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
        setNodes((nds) => [...nds.map((n) => ({ ...n, selected: false })), ...newNodes])
        setEdges((eds) => [...eds, ...newEdges])
        addToast("info", `Pasted ${newNodes.length} node${newNodes.length > 1 ? "s" : ""}`)
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
      if (mod && e.key === "k") {
        e.preventDefault()
        setNodeSearchOpen((prev) => !prev)
        return
      }

      // Escape → clear trace + close panel
      if (e.key === "Escape") {
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
      if (mod && e.key === "g") {
        e.preventDefault()
        if (isInsideSubmodel) {
          addToast("info", "Submodels cannot be nested inside other submodels")
          return
        }
        const { nodes: currentNodes } = graphRef.current
        const selectedIds = currentNodes.filter((n) => n.selected).map((n) => n.id)
        if (selectedIds.length >= 2) {
          setSubmodelDialog({ nodeIds: selectedIds })
        } else {
          addToast("info", "Select at least 2 nodes to create a submodel (Ctrl+G)")
        }
        return
      }

      // Delete / Backspace → remove selected nodes and/or edges (unless typing)
      if ((e.key === "Delete" || e.key === "Backspace") && !isTyping) {
        const { nodes: currentNodes, edges: currentEdges } = graphRef.current
        const selectedNodeIds = new Set(currentNodes.filter((n) => n.selected).map((n) => n.id))
        const selectedEdgeIds = new Set(currentEdges.filter((ed) => ed.selected).map((ed) => ed.id))
        if (selectedNodeIds.size === 0 && selectedEdgeIds.size === 0) return
        if (selectedNodeIds.size > 0) {
          setNodes(currentNodes.filter((n) => !selectedNodeIds.has(n.id)))
          setEdges(currentEdges.filter((ed) => !selectedNodeIds.has(ed.source) && !selectedNodeIds.has(ed.target)))
          setSelectedNode(null)
          setPreviewData(null)
          // Clean up store state for deleted nodes
          for (const nid of selectedNodeIds) {
            useNodeResultsStore.getState().clearNode(nid)
          }
        } else {
          setEdges(currentEdges.filter((ed) => !selectedEdgeIds.has(ed.id)))
        }
      }
    }
    window.addEventListener("keydown", handler)
    return () => window.removeEventListener("keydown", handler)
  }, [
    handleSave, setNodes, setEdges, undo, redo, fitView,
    graphRef, clipboard, nodeIdCounter,
    setSelectedNode, setPreviewData, clearTrace, closePanel,
    addToast, setShortcutsOpen, setSubmodelDialog, setNodeSearchOpen, isInsideSubmodel,
  ])
}
