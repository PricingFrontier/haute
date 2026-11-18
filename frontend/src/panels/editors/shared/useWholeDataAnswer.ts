import type { NodeAddressPayload } from "../../../generated/api-contracts.generated"
import { useRequestScope } from "../../useGraph"
import { useEffect, useRef, useState } from "react"

import { apiErrorMessage } from "../../../api/errors"
import type { PreparedGraph } from "../../../api/types"
import useNodeDataCache, { nodeDataProblemText, type NodeDataCache } from "../../../hooks/useNodeDataCache"
import { useRequestKind } from "../../../hooks/useRequestKind"
import { executionPart } from "../../../stores/useDocumentStatusStore"
import useNodeDataStore from "../../../stores/useNodeDataStore"
import useSettingsStore from "../../../stores/useSettingsStore"
import { answer as answerOf, newest } from "../../../utils/answers"
import { canvasTarget, prepareNodeRequest } from "../../../utils/requestGateway"
import type { SimpleEdge, SimpleNode } from "../_shared"

/** How long an editor waits after the last edit before asking about it. */
export const WHOLE_DATA_DEBOUNCE_MS = 250

/** Where the numbers on screen came from. */
export type WholeDataBasis = "all" | "sample" | "stale"

/**
 * What a whole-data editor says about the data it reads, as one value: the
 * answer is `ready`, it is `counting` (no answer yet), or the data is being
 * cached, is not cached, is out of date (with the reason a refresh failed),
 * must be cached again, or could not be read (`refused`: the server finds
 * nothing for the node to read; `failed`: a failed read, build or question).
 */
export type WholeDataStatus =
  | { kind: "ready" }
  | { kind: "counting" }
  | { kind: "building" }
  | { kind: "missing" }
  | { kind: "stale"; failure: string | null }
  | { kind: "cacheRequired" }
  | { kind: "refused"; reason: string }
  | { kind: "failed"; reason: string }

export interface WholeDataStatusInput {
  availability: NodeDataCache["availability"]
  problem: NodeDataCache["problem"]
  /** The editor's answer describes all rows of the data it reads now. */
  answered: boolean
  cacheRequired: boolean
  /** The whole-data question failed, with the server's reason. */
  error: string | null
}

/** The one status a whole-data editor shows, from the cache's facts and its own answer. */
export function wholeDataStatus({
  availability,
  problem,
  answered,
  cacheRequired,
  error,
}: WholeDataStatusInput): WholeDataStatus {
  if (problem?.kind === "refused") return { kind: "refused", reason: problem.reason }
  if (availability === "building") return { kind: "building" }
  if (availability === "stale") return { kind: "stale", failure: problem?.reason ?? null }
  if (problem) return { kind: "failed", reason: problem.reason }
  if (availability === "checking") return { kind: "counting" }
  if (availability !== "current") return { kind: "missing" }
  if (error) return { kind: "failed", reason: error }
  if (cacheRequired) return { kind: "cacheRequired" }
  return answered ? { kind: "ready" } : { kind: "counting" }
}

const REFRESH_HINT = "Refresh this node to read all rows"

/**
 * The words every whole-data editor shows for the states that describe the
 * data. Null for `ready` and `counting`, which each editor words itself.
 */
export function wholeDataStatusText(status: WholeDataStatus): string | null {
  switch (status.kind) {
    case "ready":
    case "counting":
      return null
    case "building":
      return "Caching the data…"
    case "missing":
    case "cacheRequired":
      return `Not cached · ${REFRESH_HINT}`
    case "stale":
      return status.failure
        ? `Cached data is out of date · Refreshing it failed: ${status.failure}`
        : `Cached data is out of date · ${REFRESH_HINT}`
    case "refused":
    case "failed":
      return nodeDataProblemText(status)
  }
}

/** What every whole-dataset answer says about the data it read. */
export interface WholeDataResponse {
  status: "ok" | "cache_required"
  data_version?: string | null
}

/** One question, as the editor asks it of the server. */
export interface WholeDataQuestion {
  /** The editor's `askedFor`, for the request to read back. */
  asked: string
  /** The graph to send: inside an open submodel, the whole pipeline. */
  graph: PreparedGraph
  /** The node, by address. */
  target: NodeAddressPayload
  source: string
  signal: AbortSignal
}

export interface UseWholeDataAnswerInput<TResponse extends WholeDataResponse> {
  node: SimpleNode | null
  allNodes: SimpleNode[]
  edges: SimpleEdge[]
  submodels?: Record<string, unknown>
  preamble?: string
  /**
   * A stable serialisation of what the editor asks, or null to ask nothing.
   * An edit that leaves it unchanged does not ask again.
   */
  askedFor: string | null
  /**
   * The part of `askedFor` the answer's question-independent facts depend on,
   * or null. While a newer question with the same subject is outstanding, the
   * last answer stays published with `answerIsCurrent` false, so an edit that
   * refines the question does not drop the editor back to the sample.
   */
  subject?: string | null
  /** Sends the question; the latest render's function is used. */
  ask: (question: WholeDataQuestion) => Promise<TResponse>
  /** Shown when a failure carries no message of its own. */
  failureMessage: string
}

export interface WholeDataAnswer<TResponse> {
  /** The shared cache of the data this node reads, for the header control. */
  cache: NodeDataCache
  /** The answer, only while it describes the data this node reads now. */
  answer: TResponse | null
  /**
   * Whether `answer` answers the question as it is now. False while a newer
   * question with the same subject is outstanding and `answer` is the last one.
   */
  answerIsCurrent: boolean
  /**
   * The server answered that the data is not cached for this question, even
   * though this editor's point looked current: the point needs caching again.
   * The answer also has the point read again, which then reports its state.
   */
  cacheRequired: boolean
  loading: boolean
  basis: WholeDataBasis
  error: string | null
  /** What the editor says about the data it reads ({@link wholeDataStatus}). */
  status: WholeDataStatus
}

interface Tagged<T> {
  identity: string
  value: T
}

interface TaggedAnswer<T> extends Tagged<T> {
  /** The subject and point it was asked about, when the editor names one. */
  subjectIdentity: string | null
}

/**
 * Ask the server about the whole dataset a node reads, for an editor that
 * otherwise works from preview rows.
 *
 * The editor asks only when the point it reads is current, so what it shows is
 * either the whole dataset's answer or plainly labelled as a sample — never a
 * whole-data answer drawn from data that has moved on. An edit supersedes the
 * question before it: that request is aborted, and its answer could not be
 * published anyway, because every answer is checked against the identity and
 * the document it was asked under.
 */
export default function useWholeDataAnswer<TResponse extends WholeDataResponse>({
  node,
  allNodes,
  edges,
  submodels,
  preamble,
  askedFor,
  subject = null,
  ask,
  failureMessage,
}: UseWholeDataAnswerInput<TResponse>): WholeDataAnswer<TResponse> {
  const requestScope = useRequestScope()
  const activeSource = useSettingsStore((s) => s.activeSource)
  const cache = useNodeDataCache({ node, allNodes, edges, submodels, preamble })
  const [answer, setAnswer] = useState<TaggedAnswer<TResponse | null> | null>(null)
  const [loading, setLoading] = useState<Tagged<boolean> | null>(null)
  const [error, setError] = useState<Tagged<string | null> | null>(null)
  const questions = useRequestKind()
  const askRef = useRef(ask)
  const failureMessageRef = useRef(failureMessage)
  useEffect(() => {
    askRef.current = ask
    failureMessageRef.current = failureMessage
  })
  const nodeId = node?.id ?? null
  const available = cache.availability
  const dataVersion = cache.dataVersion
  const requestIdentity = JSON.stringify({ nodeId, askedFor, activeSource, available, dataVersion })
  const subjectIdentity =
    subject === null ? null : JSON.stringify({ nodeId, subject, activeSource, available, dataVersion })

  useEffect(() => {
    // A saved version (the comparison view) runs nothing, so nothing is asked.
    if (!nodeId || !askedFor || available !== "current" || requestScope === null) {
      return
    }
    // The execution fence as the edit left it: an answer once it has moved is no one's.
    const fence = executionPart()
    const timer = setTimeout(() => {
      void answerOf([newest(questions), fence], async (signal) => {
        // Prepared in the request, so a request that cannot be built fails like one refused.
        const target = prepareNodeRequest(requestScope, canvasTarget(nodeId))
        return askRef.current({
          asked: askedFor,
          graph: target.graph,
          target: target.node,
          source: target.source,
          signal,
        })
      }, {
        ok: (response) => {
          setAnswer({ identity: requestIdentity, subjectIdentity, value: response })
          setError({ identity: requestIdentity, value: null })
          // The point looked current, but its data has gone or changed since it
          // was read. Every consumer re-reads its point, so this one's state,
          // and what its node's Refresh does, catch up with the server's.
          if (response.status === "cache_required") useNodeDataStore.getState().bumpEpoch()
        },
        failed: (err) => {
          // The editor keeps working from the preview, and says why it had to:
          // the server's own message — which for bad rules is execution's —
          // rather than the bare "HTTP 422" the client builds as the message.
          setAnswer({ identity: requestIdentity, subjectIdentity, value: null })
          setError({ identity: requestIdentity, value: apiErrorMessage(err, failureMessageRef.current) })
        },
      }, { pending: (waiting) => setLoading({ identity: requestIdentity, value: waiting }) })
    }, WHOLE_DATA_DEBOUNCE_MS)
    // An edit, or the editor's unmount, supersedes the question: it is aborted.
    return () => {
      clearTimeout(timer)
      questions.bump()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodeId, askedFor, available, dataVersion, activeSource])

  // A whole-data answer is shown only while the point it describes is the one
  // this node reads *now*: a stale point shows the sample instead, however
  // recently its answer arrived. The last answer about the same subject and
  // point stands in while a refined question is outstanding.
  const answered = answer?.identity === requestIdentity
  const kept = !answered && subjectIdentity !== null && answer?.subjectIdentity === subjectIdentity
  const matching = answered || kept ? (answer?.value ?? null) : null
  const current =
    available === "current" &&
    matching?.status === "ok" &&
    matching.data_version === dataVersion
  const cacheRequired = available === "current" && answered && matching?.status === "cache_required"
  const questionError = error?.identity === requestIdentity ? error.value : null
  return {
    cache,
    answer: current ? matching : null,
    answerIsCurrent: current && answered,
    cacheRequired,
    loading: loading?.identity === requestIdentity ? loading.value : false,
    error: questionError,
    basis: current ? "all" : available === "stale" ? "stale" : "sample",
    status: wholeDataStatus({
      availability: available,
      problem: cache.problem,
      answered: current,
      cacheRequired,
      error: questionError,
    }),
  }
}
