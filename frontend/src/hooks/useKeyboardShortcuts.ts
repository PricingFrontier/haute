import { useEffect, useRef } from "react"
import type { Node, Edge, NodeChange } from "@xyflow/react"
import useToastStore from "../stores/useToastStore"
import useUIStore from "../stores/useUIStore"
import useNodeResultsStore from "../stores/useNodeResultsStore"
import { isProtectedSubmodelNodeData, nodeData } from "../types/node"
import { NODE_TYPES, isSingletonType, singletonTypesOccupiedBy, type NodeTypeValue } from "../utils/nodeTypes"
import { requestSubmodelCreation } from "../utils/submodelCreation"
import { hasModifier, inModalDialog, isTypingTarget, saveCommittingField } from "./keyboardTargets"
import type { SharedNodeDeletionResult } from "./useSubmodelBoundaryEditing"
import { authoredNodeData, createAfterIdentity } from "./createAfterIdentity"

export interface KeyboardShortcutBinding {
  id:
    | "undo"
    | "redo"
    | "copy"
    | "paste"
    | "selectAll"
    | "groupSubmodel"
    | "save"
    | "searchNodes"
    | "fitView"
    | "refreshPreview"
    | "deleteSelected"
    | "escape"
    | "help"
  keys: (modLabel: string) => string
  label: string
  matches?: (e: KeyboardEvent, mod: boolean) => boolean
}

export const KEYBOARD_SHORTCUT_BINDINGS: readonly KeyboardShortcutBinding[] = [
  {
    id: "undo",
    keys: (mod) => `${mod}+Z`,
    label: "Undo",
    matches: (e, mod) => mod && e.key === "z" && !e.shiftKey,
  },
  {
    id: "redo",
    keys: (mod) => `${mod}+Shift+Z / ${mod}+Y`,
    label: "Redo",
    matches: (e, mod) => mod && ((e.key === "z" && e.shiftKey) || e.key === "y"),
  },
  {
    id: "copy",
    keys: (mod) => `${mod}+C`,
    label: "Copy selected nodes",
    matches: (e, mod) => mod && e.key === "c",
  },
  {
    id: "paste",
    keys: (mod) => `${mod}+V`,
    label: "Paste nodes",
    matches: (e, mod) => mod && e.key === "v",
  },
  {
    id: "selectAll",
    keys: (mod) => `${mod}+A`,
    label: "Select all nodes",
    matches: (e, mod) => mod && e.key === "a",
  },
  {
    id: "groupSubmodel",
    keys: (mod) => `${mod}+G`,
    label: "Group into submodel",
    matches: (e, mod) => mod && e.key === "g",
  },
  {
    id: "save",
    keys: (mod) => `${mod}+S`,
    label: "Save pipeline",
    matches: (e, mod) => mod && e.key === "s",
  },
  {
    id: "searchNodes",
    keys: (mod) => `${mod}+K`,
    label: "Search nodes",
    matches: (e, mod) => mod && e.key === "k",
  },
  {
    id: "fitView",
    keys: (mod) => `${mod}+1`,
    label: "Fit view",
    matches: (e, mod) => mod && e.key === "1",
  },
  {
    id: "refreshPreview",
    keys: (mod) => `${mod}+Enter`,
    label: "Refresh the open preview",
  },
  {
    id: "deleteSelected",
    keys: () => "Delete / Backspace",
    label: "Delete selected",
    matches: (e) => e.key === "Delete" || e.key === "Backspace",
  },
  {
    id: "escape",
    keys: () => "Escape",
    label: "Close panel / clear trace",
    matches: (e) => e.key === "Escape",
  },
  {
    id: "help",
    keys: () => "?",
    label: "Show this help",
    matches: (e) => e.key === "?",
  },
] as const

const BINDING_BY_ID = Object.fromEntries(
  KEYBOARD_SHORTCUT_BINDINGS.map((binding) => [binding.id, binding]),
) as Record<KeyboardShortcutBinding["id"], KeyboardShortcutBinding>

interface KeyboardShortcutsParams {
  /** False while the workbench's view shows over the pipeline editor: then no shortcut
   *  is registered, so keys pressed there never edit the hidden canvas. */
  enabled: boolean
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
  existingSingletonTypes: ReadonlySet<NodeTypeValue>
  resolveGraphIdentities: (
    nodes: readonly Node[],
    edges: readonly Edge[],
  ) => Promise<{ nodes: Node[]; edges: Edge[] }>
  commitSharedNodeDeletion?: (
    nodeIds: ReadonlySet<string>,
    selectedEdgeIds?: ReadonlySet<string>,
    nodeChanges?: NodeChange[],
    onSettled?: (committed: boolean) => void,
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
  enabled,
  handleSave, setNodes, setEdges, setNodesAndEdges, undo, redo, fitView,
  graphRef, clipboard, nodeIdCounter,
  setSelectedNode, setLastSelectedId, setPreviewData, clearTrace, closePanel,
  isInsideSubmodel, readOnly, existingSingletonTypes,
  resolveGraphIdentities,
  commitSharedNodeDeletion,
}: KeyboardShortcutsParams) {
  const addToast = useToastStore((s) => s.addToast)
  const setShortcutsOpen = useUIStore((s) => s.setShortcutsOpen)
  const setSubmodelDialog = useUIStore((s) => s.setSubmodelDialog)
  const setNodeSearchOpen = useUIStore((s) => s.setNodeSearchOpen)
  const pasteRequestSerialRef = useRef(0)
  useEffect(() => {
    if (!enabled) return
    const handler = (e: KeyboardEvent) => {
      const mod = hasModifier(e)
      // Focused dialogs own their keyboard interactions, including Escape. The
      // node-search palette is itself a modal dialog, so its toggle key still
      // reaches the Ctrl+K handler below while it is open.
      const closesNodeSearch = Boolean(BINDING_BY_ID.searchNodes.matches?.(e, mod)) && useUIStore.getState().nodeSearchOpen
      if (e.defaultPrevented) return
      if (inModalDialog(e.target) && !closesNodeSearch) return
      const isTyping = isTypingTarget(e.target)

      // Ctrl+S / Cmd+S → save, with a focused field's edit included.
      if (BINDING_BY_ID.save.matches?.(e, mod)) {
        e.preventDefault()
        saveCommittingField(e.target, handleSave)
        return
      }

      // Ctrl+Z → undo, Ctrl+Shift+Z / Ctrl+Y → redo
      if (BINDING_BY_ID.undo.matches?.(e, mod) && !isTyping) {
        e.preventDefault()
        if (readOnly) return
        undo()
        return
      }
      if (BINDING_BY_ID.redo.matches?.(e, mod) && !isTyping) {
        e.preventDefault()
        if (readOnly) return
        redo()
        return
      }

      // Ctrl+C → copy selected nodes
      if (BINDING_BY_ID.copy.matches?.(e, mod) && !isTyping) {
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
      if (BINDING_BY_ID.paste.matches?.(e, mod) && !isTyping) {
        if (readOnly) {
          e.preventDefault()
          return
        }
        const { nodes: copiedNodes, edges: copiedEdges } = clipboard.current
        if (copiedNodes.length === 0) return
        e.preventDefault()
        const capturedGraph = graphRef.current
        // Submodel boundaries do not create a second singleton scope. Include
        // the immediate graph too in case it changed before React re-rendered.
        const occupiedSingletonTypes = new Set<string>(existingSingletonTypes)
        for (const n of capturedGraph.nodes) {
          for (const occupied of singletonTypesOccupiedBy(nodeData(n).nodeType)) occupiedSingletonTypes.add(occupied)
        }
        // A copy of a submodel occurrence would carry the original's alias under
        // a new id, and a boundary card belongs to its view: neither pastes.
        const copiedOccurrence = copiedNodes.some((n) => nodeData(n).nodeType === NODE_TYPES.SUBMODEL)
        if (copiedOccurrence) addToast("error", "Use Create Instance to duplicate a reusable submodel")
        const pasteable = copiedNodes.filter((n) => {
          const nt = nodeData(n).nodeType
          if (nt === NODE_TYPES.SUBMODEL || nt === NODE_TYPES.SUBMODEL_PORT) return false
          if (!isSingletonType(nt)) return true
          if (occupiedSingletonTypes.has(nt!)) return false
          for (const occupied of singletonTypesOccupiedBy(nt)) occupiedSingletonTypes.add(occupied)
          return true
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
            // Another node's recovery identity, column stash or editor identities are not the copy's.
            data: { ...authoredNodeData(n.data), label: `${n.data.label} copy` },
          }
        })
        const newEdges: Edge[] = copiedEdges.flatMap((ed) => {
          const newSource = idMap.get(ed.source)
          const newTarget = idMap.get(ed.target)
          if (!newSource || !newTarget) return []
          return [{ ...ed, id: `e-${newSource}-${newTarget}`, source: newSource, target: newTarget }]
        })
        void createAfterIdentity({
          graphRef,
          serialRef: pasteRequestSerialRef,
          resolve: async () => {
            const resolved = await resolveGraphIdentities(newNodes, newEdges)
            assertResolvedPasteMatchesCandidate(newNodes, newEdges, resolved)
            return resolved
          },
          apply: (resolved) => {
            // Pasted nodes + their internal edges land in ONE undo step, so a
            // single ⌘/Ctrl-Z removes the whole paste, added to the live graph.
            setNodesAndEdges(
              (nds) => [...nds.map((node) => ({ ...node, selected: false })), ...resolved.nodes],
              (eds) => [...eds, ...resolved.edges],
            )
            addToast("info", `Pasted ${resolved.nodes.length} node${resolved.nodes.length > 1 ? "s" : ""}`)
          },
          addToast,
          action: "Paste",
          failure: "Paste failed",
        })
        return
      }

      // Ctrl+A → select all nodes
      if (BINDING_BY_ID.selectAll.matches?.(e, mod) && !isTyping) {
        e.preventDefault()
        setNodes((nds) => nds.map((n) => ({ ...n, selected: true })))
        return
      }

      // Ctrl+1 → fit view
      if (BINDING_BY_ID.fitView.matches?.(e, mod)) {
        e.preventDefault()
        fitView({ padding: 0.8 })
        return
      }

      // Ctrl+K → open node search
      if (BINDING_BY_ID.searchNodes.matches?.(e, mod) && (!isTyping || useUIStore.getState().nodeSearchOpen)) {
        e.preventDefault()
        setNodeSearchOpen((prev) => !prev)
        return
      }

      // Escape → clear trace + close panel
      if (BINDING_BY_ID.escape.matches?.(e, mod) && !isTyping) {
        clearTrace()
        closePanel()
        return
      }

      // ? → toggle keyboard shortcuts help (unless typing)
      if (BINDING_BY_ID.help.matches?.(e, mod) && !isTyping) {
        e.preventDefault()
        setShortcutsOpen((prev) => !prev)
        return
      }

      // Ctrl+G → group selected nodes into a submodel
      if (BINDING_BY_ID.groupSubmodel.matches?.(e, mod) && !isTyping) {
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
      if (BINDING_BY_ID.deleteSelected.matches?.(e, mod) && !isTyping) {
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
          // Selection, preview and cached-result cleanup only runs once the
          // nodes have actually left the graph; a shared-boundary deletion may
          // still be resolving parent identities and can yet fail.
          const cleanupAfterRemoval = () => {
            setSelectedNode(null)
            setLastSelectedId?.(null)
            setPreviewData(null)
            // Clean up store state for deleted nodes
            for (const nid of selectedNodeIds) {
              useNodeResultsStore.getState().clearNode(nid)
            }
          }
          let cleanedUp = false
          const sharedDeletion = commitSharedNodeDeletion?.(
            selectedNodeIds,
            selectedEdgeIds,
            undefined,
            (committed) => {
              if (!committed) return
              cleanedUp = true
              cleanupAfterRemoval()
            },
          )
          if (sharedDeletion === "blocked") return
          if (sharedDeletion === "pending") return
          // Nodes + their edges removed in ONE undo step. setNodes-then-setEdges
          // would push two snapshots, so one delete would take two undos to
          // reverse (the undo-atomicity bug class).
          if (sharedDeletion !== "committed") {
            setNodesAndEdges(
              currentNodes.filter((n) => !selectedNodeIds.has(n.id)),
              currentEdges.filter(
                (ed) =>
                  !selectedNodeIds.has(ed.source)
                  && !selectedNodeIds.has(ed.target)
                  && !selectedEdgeIds.has(ed.id),
              ),
            )
          }
          if (!cleanedUp) cleanupAfterRemoval()
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
    enabled,
    handleSave, setNodes, setEdges, setNodesAndEdges, undo, redo, fitView,
    graphRef, clipboard, nodeIdCounter,
    setSelectedNode, setLastSelectedId, setPreviewData, clearTrace, closePanel,
    addToast, setShortcutsOpen, setSubmodelDialog, setNodeSearchOpen, isInsideSubmodel, readOnly,
    existingSingletonTypes,
    resolveGraphIdentities, commitSharedNodeDeletion,
  ])
}
