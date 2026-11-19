import { useCallback, useEffect, useState } from "react"
import { apiErrorMessage } from "../api/errors"
import { fetchRepairOptions } from "../api/client"
import type { PipelineRepairOption, PipelineRepairOptionsResponse } from "../api/types"
import useDocumentStatusStore from "../stores/useDocumentStatusStore"
import { answer, newest, signalPart } from "../utils/answers"
import { useRequestKind } from "./useRequestKind"

export interface RepairTarget {
  sourceFile: string
  recoveryId: string
}

/** What the editor may offer for one settings repair of an unavailable node. */
export type RepairOffer =
  /** Not yet answered: the button shows, disabled. */
  | { state: "checking" }
  /** The server built the repair's plan: the button is enabled. */
  | { state: "available" }
  /** The server refused the plan: no button, and the reason shows instead. */
  | { state: "unavailable"; reason: string }
  /** The question failed, so nothing is admitted: the button shows disabled beside why. */
  | { state: "failed"; reason: string }

export interface RepairOffers {
  recover: RepairOffer
  reset: RepairOffer
}

export interface RepairOptions {
  offersFor(target: RepairTarget): RepairOffers
  /** Ask again, after a question failed. */
  retry(): void
}

/** The most targets one question may name: `PipelineRepairOptionsRequest.targets`
 *  bound on the server. A larger inspector asks in several questions. */
export const REPAIR_OPTIONS_BATCH = 256

type NodeOptions = PipelineRepairOptionsResponse["nodes"][number]

type OptionsRead =
  | { identity: string; status: "loading" }
  | { identity: string; status: "ready"; nodes: NodeOptions[] }
  | { identity: string; status: "failed"; reason: string }

const CHECKING: RepairOffers = { recover: { state: "checking" }, reset: { state: "checking" } }

function targetKey(sourceFile: string, recoveryId: string): string {
  return JSON.stringify([sourceFile, recoveryId])
}

function offer(option: PipelineRepairOption): RepairOffer {
  return option.available
    ? { state: "available" }
    : { state: "unavailable", reason: option.reason ?? "This repair cannot be applied to this node." }
}

function failed(reason: string): RepairOffers {
  return { recover: { state: "failed", reason }, reset: { state: "failed", reason } }
}

/**
 * Which of Recover and Reset the server admits for each of *targets*, asked in one
 * question of the document revision on show (split into batches the server accepts), and
 * asked again when that revision changes.
 *
 * The server dry-runs each repair's plan and is the only admission check: a repair is
 * offered only when it answers available, and a failed question admits nothing.
 */
export function useRepairOptions(targets: readonly RepairTarget[]): RepairOptions {
  const documentFile = useDocumentStatusStore((s) => s.sourceFile)
  const revision = useDocumentStatusStore((s) => s.sourceRevision)
  const [read, setRead] = useState<OptionsRead | null>(null)
  const [attempt, setAttempt] = useState(0)
  const reads = useRequestKind()
  const asked = targets.map((target) => targetKey(target.sourceFile, target.recoveryId))
  const asking = asked.length > 0 && revision !== null
  const identity = asking ? JSON.stringify([documentFile, revision, asked, attempt]) : ""

  useEffect(() => {
    if (!asking) return
    const controller = new AbortController()
    const question = identity
    // eslint-disable-next-line react-hooks/set-state-in-effect -- fetch-on-change: records the question it asks
    setRead({ identity: question, status: "loading" })
    const batches: RepairTarget[][] = []
    for (let start = 0; start < targets.length; start += REPAIR_OPTIONS_BATCH) {
      batches.push(targets.slice(start, start + REPAIR_OPTIONS_BATCH))
    }
    // Every batch asks of the same revision; any failure fails the whole question.
    void answer([newest(reads), signalPart(controller.signal)], (own) => Promise.all(batches.map((batch) => (
      fetchRepairOptions({
        sourceFile: documentFile,
        sourceRevision: revision,
        targets: batch.map(({ sourceFile, recoveryId }) => ({ sourceFile, recoveryId })),
      }, { signal: own })
    ))), {
      ok: (answers) => setRead({ identity: question, status: "ready", nodes: answers.flatMap((data) => data.nodes) }),
      failed: (error) => setRead({
        identity: question,
        status: "failed",
        reason: apiErrorMessage(error, "The server could not be asked."),
      }),
    })
    return () => controller.abort()
    // identity names every value the question reads.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [identity, reads])

  const current = asking && read?.identity === identity ? read : null
  const offersFor = useCallback((target: RepairTarget): RepairOffers => {
    if (current === null || current.status === "loading") return CHECKING
    if (current.status === "failed") return failed(current.reason)
    const key = targetKey(target.sourceFile, target.recoveryId)
    const node = current.nodes.find(
      (entry) => targetKey(entry.target_source_file, entry.target_recovery_id) === key,
    )
    if (node === undefined) return failed("The server's answer did not name this node.")
    return { recover: offer(node.recover), reset: offer(node.reset) }
  }, [current])
  const retry = useCallback(() => setAttempt((count) => count + 1), [])
  return { offersFor, retry }
}
