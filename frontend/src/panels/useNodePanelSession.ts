import { useCallback, useEffect, useReducer, useRef } from "react"

import type { OnUpdateConfigResult } from "./editors"

export type NodePanelTab = "config" | "polars" | "columns"

type PanelSessionState = {
  activeTab: NodePanelTab
  dismissedWarningSignature: string | null
}

type PanelSessionAction =
  | { type: "select-tab"; tab: NodePanelTab }
  | { type: "dismiss-warning"; signature: string }

function panelSessionReducer(
  state: PanelSessionState,
  action: PanelSessionAction,
): PanelSessionState {
  switch (action.type) {
    case "select-tab":
      return state.activeTab === action.tab ? state : { ...state, activeTab: action.tab }
    case "dismiss-warning":
      return state.dismissedWarningSignature === action.signature
        ? state
        : { ...state, dismissedWarningSignature: action.signature }
  }
}

export type NodePanelSession = PanelSessionState & {
  selectTab: (tab: NodePanelTab) => void
  dismissWarning: (signature: string) => void
}

/** Node-keyed local navigation and schema-warning state. */
export function useNodePanelSession(): NodePanelSession {
  const [state, dispatch] = useReducer(panelSessionReducer, {
    activeTab: "config",
    dismissedWarningSignature: null,
  })
  const selectTab = useCallback((tab: NodePanelTab) => {
    dispatch({ type: "select-tab", tab })
  }, [])
  const dismissWarning = useCallback((signature: string) => {
    dispatch({ type: "dismiss-warning", signature })
  }, [])
  return { ...state, selectTab, dismissWarning }
}

type RenameSessionState = {
  pending: boolean
  error: string | null
}

type RenameSessionAction =
  | { type: "started" }
  | { type: "settled"; error: string | null }

function renameSessionReducer(
  state: RenameSessionState,
  action: RenameSessionAction,
): RenameSessionState {
  if (action.type === "started") {
    return state.pending ? state : { ...state, pending: true }
  }
  return { pending: false, error: action.error }
}

export type NodeRenameSession = RenameSessionState & {
  commit: (
    label: string,
    onRenameNode?: (nodeId: string, label: string) => Promise<OnUpdateConfigResult>,
  ) => void
}

/** Label-keyed request state; the owning component remounts when the label changes. */
export function useNodeRenameSession(nodeId: string): NodeRenameSession {
  const [state, dispatch] = useReducer(renameSessionReducer, {
    pending: false,
    error: null,
  })
  const generationRef = useRef(0)
  const mountedRef = useRef(false)
  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
    }
  }, [])

  const commit = useCallback<NodeRenameSession["commit"]>((label, onRenameNode) => {
    const generation = ++generationRef.current
    if (!onRenameNode) {
      dispatch({ type: "settled", error: "Node rename handler is unavailable." })
      return
    }
    dispatch({ type: "started" })
    void Promise.resolve()
      .then(() => onRenameNode(nodeId, label))
      .then((result) => {
        if (!mountedRef.current || generationRef.current !== generation) return
        dispatch({ type: "settled", error: result.ok ? null : result.error })
      })
      .catch((error: unknown) => {
        if (!mountedRef.current || generationRef.current !== generation) return
        dispatch({
          type: "settled",
          error: `Rename failed: ${error instanceof Error ? error.message : String(error)}`,
        })
      })
  }, [nodeId])

  return { ...state, commit }
}
