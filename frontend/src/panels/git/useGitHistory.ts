import { useCallback, useEffect, useReducer, useRef } from "react"

import {
  getGitGraph,
  getMilestoneSaves,
  getMilestones,
  getPendingSaves,
} from "../../api/client"
import type { GitGraphResponse, GitLedgerSave, GitMilestoneEntry } from "../../api/types"
import { apiErrorMessage } from "../../api/errors"
import { useRequestKind } from "../../hooks/useRequestKind"
import { answer, ask, newest, under } from "../../utils/answers"
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
  /** Why this scope's latest refresh failed, until the next one starts. */
  loadError: string | null
  selectedSha: string | null
  graph: GitGraphResponse | null
  rowsBranch: string | null
  milestonesJson: string | null
  pendingJson: string | null
  graphJson: string | null
}

type GitHistoryAction =
  | { type: "loading"; value: boolean }
  | { type: "load-error"; message: string | null }
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
    loadError: null,
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
    case "load-error":
      return state.loadError === action.message ? state : { ...state, loadError: action.message }
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
  const consumedNonceRef = useRef({ historyNonce, commitNonce })
  // The refresh asked last owns the rows, the graph and the loading flag; an unmount ends it.
  const refreshes = useRequestKind()
  const life = useRequestKind()

  const applyAction = useCallback((action: GitHistoryAction) => {
    stateRef.current = reducer(stateRef.current, action)
    dispatch(action)
  }, [])

  const refresh = useCallback(async () => {
    const rows = ask([newest(refreshes)], {
      pending: (loading) => applyAction({ type: "loading", value: loading }),
    })
    const scope = branchKey
    applyAction({ type: "load-error", message: null })
    const wasCold = stateRef.current.milestones.length === 0
      || stateRef.current.rowsBranch !== scope

    // The graph answers under the same refresh, whenever it comes.
    const graphSettled = answer([under(refreshes)], getGitGraph(50), {
      ok: (graph) => {
        const json = serializePayload(graph)
        writeGraphCache(graph, json)
        applyAction({ type: "graph", graph, json })
      },
      failed: () => {},
    })

    let read = null as { milestones: GitMilestoneEntry[]; pending: GitLedgerSave[] } | null
    try {
      await rows.answer(Promise.all([getMilestones(50, scope), getPendingSaves(scope)]), {
        ok: ([milestoneResponse, pendingResponse], asked) => asked.answer(
          wasCold
            ? Promise.race([graphSettled, new Promise((resolve) => setTimeout(resolve, 250))])
            : Promise.resolve(),
          {
            ok: () => {
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
              read = {
                milestones: milestoneResponse.entries,
                pending: pendingResponse.saves,
              }
            },
          },
        ),
        failed: (error) => {
          // Toasted, and kept for the panel, so the failure still shows once the toast fades.
          const message = `Failed to load version history: ${apiErrorMessage(error, "unknown error")}`
          addToast("error", message)
          applyAction({ type: "load-error", message })
        },
      })
    } finally {
      rows.release()
    }
    return read
  }, [addToast, applyAction, branchKey, refreshes])

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
    const asked = ask([under(life)])
    void asked.answer(refresh(), {
      ok: (result) => {
        if (result !== null && result.milestones.length > 0 && !peeking) {
          applyAction({ type: "select", sha: result.milestones[0].sha })
        }
      },
    })
    return () => asked.release()
  }, [applyAction, commitNonce, peeking, refresh, life])

  const toggleExpand = useCallback((sha: string) => {
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
    void answer([under(life)], getMilestoneSaves(sha), {
      ok: (response) => {
        writeMilestoneSaves(sha, response.saves)
        applyAction({ type: "resolve-expansion", sha, saves: response.saves })
      },
      failed: (error) => {
        addToast(
          "error",
          `Failed to load the saves in this milestone: ${apiErrorMessage(error, "unknown error")}`,
        )
        applyAction({ type: "resolve-expansion", sha })
      },
    })
  }, [addToast, applyAction, life])

  const selectSha = useCallback((sha: string | null) => {
    applyAction({ type: "select", sha })
  }, [applyAction])

  return { ...state, refresh, toggleExpand, selectSha }
}
