import { useEffect, useRef, useState } from "react"

import { apiErrorMessage } from "../../../api/errors"
import type { GraphPayload } from "../../../api/types"
import useNodeDataCache, { type NodeDataCache } from "../../../hooks/useNodeDataCache"
import {
  captureDocumentExecutionFence,
  isDocumentExecutionFenceCurrent,
} from "../../../stores/useDocumentStatusStore"
import useSettingsStore from "../../../stores/useSettingsStore"
import { buildGraph } from "../../../utils/buildGraph"
import type { SimpleEdge, SimpleNode } from "../_shared"

/** How long an editor waits after the last edit before asking about it. */
export const WHOLE_DATA_DEBOUNCE_MS = 250

/** Where the numbers on screen came from. */
export type WholeDataBasis = "all" | "sample" | "stale"

/** What every whole-dataset answer says about the data it read. */
export interface WholeDataResponse {
  status: "ok" | "cache_required"
  data_version?: string | null
}

/** One question, as the editor asks it of the server. */
export interface WholeDataQuestion {
  /** The editor's `askedFor`, for the request to read back. */
  asked: string
  graph: GraphPayload
  nodeId: string
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
  loading: boolean
  basis: WholeDataBasis
  error: string | null
}

interface Tagged<T> {
  identity: string
  value: T
}

interface InFlightQuestion {
  controller: AbortController | null
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
  ask,
  failureMessage,
}: UseWholeDataAnswerInput<TResponse>): WholeDataAnswer<TResponse> {
  const activeSource = useSettingsStore((s) => s.activeSource)
  const cache = useNodeDataCache({ node, allNodes, edges, submodels, preamble })
  const [answer, setAnswer] = useState<Tagged<TResponse | null> | null>(null)
  const [loading, setLoading] = useState<Tagged<boolean> | null>(null)
  const [error, setError] = useState<Tagged<string | null> | null>(null)
  const inFlight = useRef<InFlightQuestion | null>(null)
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

  useEffect(() => {
    if (!nodeId || !askedFor || available !== "current") {
      return
    }
    const fence = captureDocumentExecutionFence()
    const question: InFlightQuestion = { controller: null }
    const timer = setTimeout(() => {
      const controller = new AbortController()
      question.controller = controller
      inFlight.current = question
      setLoading({ identity: requestIdentity, value: true })
      askRef.current({
        asked: askedFor,
        graph: buildGraph(allNodes, edges, submodels, preamble),
        nodeId,
        source: activeSource,
        signal: controller.signal,
      })
        .then((response) => {
          if (
            controller.signal.aborted ||
            inFlight.current !== question ||
            !isDocumentExecutionFenceCurrent(fence)
          ) return
          setAnswer({ identity: requestIdentity, value: response })
          setError({ identity: requestIdentity, value: null })
        })
        .catch((err: unknown) => {
          if (
            controller.signal.aborted ||
            inFlight.current !== question ||
            !isDocumentExecutionFenceCurrent(fence)
          ) return
          // The editor keeps working from the preview, and says why it had to:
          // the server's own message — which for bad rules is execution's —
          // rather than the bare "HTTP 422" the client builds as the message.
          setAnswer({ identity: requestIdentity, value: null })
          setError({ identity: requestIdentity, value: apiErrorMessage(err, failureMessageRef.current) })
        })
        .finally(() => {
          if (inFlight.current !== question) return
          inFlight.current = null
          setLoading({ identity: requestIdentity, value: false })
        })
    }, WHOLE_DATA_DEBOUNCE_MS)
    return () => {
      clearTimeout(timer)
      question.controller?.abort()
      if (inFlight.current === question) {
        inFlight.current = null
        setLoading({ identity: requestIdentity, value: false })
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodeId, askedFor, available, dataVersion, activeSource])

  // A whole-data answer is shown only while the point it describes is the one
  // this node reads *now*: a stale point shows the sample instead, however
  // recently its answer arrived.
  const matching = answer?.identity === requestIdentity ? answer.value : null
  const current =
    available === "current" &&
    matching?.status === "ok" &&
    matching.data_version === dataVersion
  return {
    cache,
    answer: current ? matching : null,
    loading: loading?.identity === requestIdentity ? loading.value : false,
    error: error?.identity === requestIdentity ? error.value : null,
    basis: current ? "all" : available === "stale" ? "stale" : "sample",
  }
}
