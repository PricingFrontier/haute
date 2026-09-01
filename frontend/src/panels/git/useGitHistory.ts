import { useCallback, useEffect, useReducer, useRef } from "react"

import {
  getGitGraph,
  getMilestoneSaves,
  getMilestones,
  getPendingSaves,
} from "../../api/client"
import type { GitGraphResponse, GitLedgerSave, GitMilestoneEntry } from "../../api/types"
import { gitErrorMessage } from "../../utils/gitError"
import {
  readBranchHistory,
  readGraphCache,
  readMilestoneSaves,
  serializePayload,
  writeBranchHistory,
  writeGraphCache,
  writeMilestoneSaves,
} from "../gitPanelCache"

type ExpandState = Record<string, GitLedgerSave[] | "loading">

type GitHistoryState = {
  milestones: GitMilestoneEntry[]
  pending: GitLedgerSave[]
  expanded: ExpandState
  loading: boolean
  selectedSha: string | null
  graph: GitGraphResponse | null
  rowsBranch: string | null
  milestonesJson: string | null
  pendingJson: string | null
  graphJson: string | null
}

type GitHistoryAction =
  | { type: "loading"; value: boolean }
  | {
      type: "rows"
      branch: string | null
      milestones: GitMilestoneEntry[]
      pending: GitLedgerSave[]
      milestonesJson: string
      pendingJson: string
    }
  | { type: "graph"; graph: GitGraphResponse; json: string }
  | { type: "select"; sha: string | null }
  | { type: "expand"; sha: string; value: GitLedgerSave[] | "loading" | undefined }
  | { type: "resolve-expansion"; sha: string; saves?: GitLedgerSave[] }

type UseGitHistoryOptions = {
  branchKey: string | null
  peeking: boolean
  historyNonce: number
  commitNonce: number
  addToast: (kind: "error", message: string) => void
}

function initialState(branchKey: string | null): GitHistoryState {
  const history = branchKey === null ? undefined : readBranchHistory(branchKey)
  const graph = readGraphCache()
  return {
    milestones: history?.milestones ?? [],
    pending: history?.pending ?? [],
    expanded: {},
    loading: false,
    selectedSha: null,
    graph: graph?.graph ?? null,
    rowsBranch: history === undefined ? null : branchKey,
    milestonesJson: history?.milestonesJson ?? null,
    pendingJson: history?.pendingJson ?? null,
    graphJson: graph?.json ?? null,
  }
}

function reducer(state: GitHistoryState, action: GitHistoryAction): GitHistoryState {
  switch (action.type) {
    case "loading":
      return state.loading === action.value ? state : { ...state, loading: action.value }
    case "graph":
      return state.graphJson === action.json
        ? state
        : { ...state, graph: action.graph, graphJson: action.json }
    case "select":
      return state.selectedSha === action.sha
        ? state
        : { ...state, selectedSha: action.sha }
    case "rows": {
      const sameBranch = state.rowsBranch === action.branch
      const milestones = sameBranch && state.milestonesJson === action.milestonesJson
        ? state.milestones
        : action.milestones
      const pending = sameBranch && state.pendingJson === action.pendingJson
        ? state.pending
        : action.pending
      if (milestones === state.milestones && pending === state.pending && sameBranch) {
        return state
      }
      return {
        ...state,
        milestones,
        pending,
        rowsBranch: action.branch,
        milestonesJson: action.milestonesJson,
        pendingJson: action.pendingJson,
      }
    }
    case "expand": {
      if (action.value === undefined) {
        if (state.expanded[action.sha] === undefined) return state
        const expanded = { ...state.expanded }
        delete expanded[action.sha]
        return { ...state, expanded }
      }
      if (state.expanded[action.sha] === action.value) return state
      return { ...state, expanded: { ...state.expanded, [action.sha]: action.value } }
    }
    case "resolve-expansion": {
      if (state.expanded[action.sha] !== "loading") return state
      const expanded = { ...state.expanded }
      if (action.saves === undefined) delete expanded[action.sha]
      else expanded[action.sha] = action.saves
      return { ...state, expanded }
    }
  }
}

/** Owns one immutable branch scope's cache hydration, requests, rows, and selection state. */
export function useGitHistory({
  branchKey,
  peeking,
  historyNonce,
  commitNonce,
  addToast,
}: UseGitHistoryOptions) {
  const [state, dispatch] = useReducer(reducer, branchKey, initialState)
  const stateRef = useRef(state)
  const generationRef = useRef(0)
  const aliveRef = useRef(false)
  const consumedNonceRef = useRef({ historyNonce, commitNonce })

  const applyAction = useCallback((action: GitHistoryAction) => {
    stateRef.current = reducer(stateRef.current, action)
    dispatch(action)
  }, [])

  useEffect(() => {
    aliveRef.current = true
    return () => {
      aliveRef.current = false
      generationRef.current += 1
    }
  }, [])

  const refresh = useCallback(async () => {
    if (!aliveRef.current) return null
    const requestGeneration = ++generationRef.current
    const scope = branchKey
    const ownsRequest = () => (
      aliveRef.current && requestGeneration === generationRef.current
    )
    applyAction({ type: "loading", value: true })
    const wasCold = stateRef.current.milestones.length === 0
      || stateRef.current.rowsBranch !== scope

    const graphSettled = getGitGraph(50).then(
      (graph) => {
        if (!ownsRequest()) return
        const json = serializePayload(graph)
        writeGraphCache(graph, json)
        applyAction({ type: "graph", graph, json })
      },
      () => {},
    )

    try {
      const [milestoneResponse, pendingResponse] = await Promise.all([
        getMilestones(50, scope),
        getPendingSaves(scope),
      ])
      if (wasCold) {
        await Promise.race([
          graphSettled,
          new Promise((resolve) => setTimeout(resolve, 250)),
        ])
      }
      if (!ownsRequest()) return null

      const resolvedBranch = scope ?? milestoneResponse.working_branch
      const milestonesJson = serializePayload(milestoneResponse.entries)
      const pendingJson = serializePayload(pendingResponse.saves)
      if (resolvedBranch !== null) {
        writeBranchHistory(resolvedBranch, {
          milestones: milestoneResponse.entries,
          milestonesJson,
          pending: pendingResponse.saves,
          pendingJson,
        })
      }
      applyAction({
        type: "rows",
        branch: resolvedBranch,
        milestones: milestoneResponse.entries,
        pending: pendingResponse.saves,
        milestonesJson,
        pendingJson,
      })
      return {
        milestones: milestoneResponse.entries,
        pending: pendingResponse.saves,
      }
    } catch (error) {
      if (!ownsRequest()) return null
      addToast(
        "error",
        `Failed to load version history: ${gitErrorMessage(error, "unknown error")}`,
      )
      return null
    } finally {
      if (ownsRequest()) applyAction({ type: "loading", value: false })
    }
  }, [addToast, applyAction, branchKey])

  useEffect(() => {
    void refresh()
  }, [refresh])

  useEffect(() => {
    if (historyNonce === consumedNonceRef.current.historyNonce) return
    consumedNonceRef.current.historyNonce = historyNonce
    void refresh()
  }, [historyNonce, refresh])

  useEffect(() => {
    if (commitNonce === consumedNonceRef.current.commitNonce) return
    consumedNonceRef.current.commitNonce = commitNonce
    void refresh().then((result) => {
      if (result !== null && result.milestones.length > 0 && !peeking) {
        applyAction({ type: "select", sha: result.milestones[0].sha })
      }
    })
  }, [applyAction, commitNonce, peeking, refresh])

  const toggleExpand = useCallback(async (sha: string) => {
    const current = stateRef.current.expanded[sha]
    if (current !== undefined) {
      applyAction({ type: "expand", sha, value: undefined })
      return
    }

    const cached = readMilestoneSaves(sha)
    if (cached !== undefined) {
      applyAction({ type: "expand", sha, value: cached })
      return
    }

    applyAction({ type: "expand", sha, value: "loading" })
    try {
      const response = await getMilestoneSaves(sha)
      if (!aliveRef.current) return
      writeMilestoneSaves(sha, response.saves)
      applyAction({ type: "resolve-expansion", sha, saves: response.saves })
    } catch (error) {
      if (!aliveRef.current) return
      addToast(
        "error",
        `Failed to load the saves in this milestone: ${gitErrorMessage(error, "unknown error")}`,
      )
      applyAction({ type: "resolve-expansion", sha })
    }
  }, [addToast, applyAction])

  const selectSha = useCallback((sha: string | null) => {
    applyAction({ type: "select", sha })
  }, [applyAction])

  return { ...state, refresh, toggleExpand, selectSha }
}
