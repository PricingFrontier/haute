import { create } from "zustand"

import type {
  NodeDataColumns,
  NodeDataGeneration,
  NodeDataPointKind,
  NodeDataPointResponse,
  NodeDataPointState,
  NodeDataRetention,
  NodeDataStatusResponse,
} from "../api/types"

/**
 * What every consumer of one data point shares, keyed by its slot
 * (`producerNodeId|portLabel|source`).
 *
 * A slot entry holds only point-wide facts: the generation the producer's
 * current signature has, any running build, and how the point is built or
 * cleared. A consumer's own availability depends on its column demand, so it is
 * derived per consumer from this entry rather than stored here — one narrow
 * generation is `current` for a Banding editor demanding that column and
 * `partial` for an Explore preview demanding every column.
 */
export interface NodeDataSlot {
  slotKey: string
  producerNodeId: string
  portLabel: string | null
  source: string
  kind: NodeDataPointKind
  /** The backend's state for the demand of the consumer that last reported it. */
  reportedState: NodeDataPointState
  /** The demand that `reportedState` was reported for. */
  reportedDemand: NodeDataColumns
  dataVersion: string | null
  rowCount: number | null
  sizeBytes: number | null
  retention: NodeDataRetention | null
  generation: NodeDataGeneration | null
  readsDirectly: boolean
  buildEndpoint: string | null
  clearEndpoint: string | null
  job: NodeDataSlotJob | null
  delegatedBuild: NodeDataDelegatedBuild | null
}

/**
 * A build that belongs to another route — an input snapshot or a JSON cache —
 * running on behalf of this point. It has no node-data job to poll, so the
 * consumer that started it holds the canceller every consumer of the slot uses.
 */
export interface NodeDataDelegatedBuild {
  /**
   * Identifies this build among every pass that has used the slot, so a
   * callback from an abandoned pass cannot report progress for, or clear, the
   * build that replaced it.
   */
  token: string
  message: string
  startedByLabel: string
  cancel: () => void
}

export interface NodeDataSlotJob {
  jobId: string
  progress: number
  message: string
  /** The consumer that started the build, for the toast a completion raises. */
  startedByLabel: string
}

export interface NodeDataStore {
  slots: Record<string, NodeDataSlot>
  /**
   * The running build of each slot, keyed by slot, for the application's one
   * background poller. A job any consumer started — or that a `point` response
   * reported from another client — is polled exactly once.
   */
  jobs: Record<string, NodeDataSlotJob>
  /**
   * A monotonic counter that changes whenever what a consumer reads may have
   * changed: a generation published, widened, evicted, or cleared, a build that
   * reached any terminal outcome, a slot forgotten, or the whole store reset.
   * Consumers re-ask the backend on every change; it never goes backwards, so a
   * reset is always observed.
   */
  epoch: number
  /** Record an authoritative `point` payload, whichever consumer asked for it. */
  observePoint: (point: NodeDataPointResponse, source: string) => void
  startJob: (slotKey: string, job: { jobId: string; message: string; startedByLabel: string }) => void
  /** Record a delegated build (an input snapshot or JSON cache) with its canceller. */
  startDelegatedBuild: (slotKey: string, build: NodeDataDelegatedBuild) => void
  reportDelegatedProgress: (slotKey: string, token: string, message: string | null) => void
  /**
   * Replace the message and canceller of a delegated build this caller still
   * owns, for a cancellation that did not take. A pass that no longer owns the
   * slot changes nothing.
   */
  retainDelegatedBuild: (
    slotKey: string,
    token: string,
    update: { message: string; cancel: () => void },
  ) => void
  finishDelegatedBuild: (slotKey: string, token: string) => void
  updateJobProgress: (slotKey: string, status: NodeDataStatusResponse) => void
  finishJob: (slotKey: string, status?: NodeDataStatusResponse | null) => void
  forgetSlot: (slotKey: string) => void
  reset: () => void
}

function pointRefLabel(point: NodeDataPointResponse): { producerNodeId: string; portLabel: string | null } {
  return {
    producerNodeId: point.point.producer_node_id,
    portLabel: point.point.port_label ?? null,
  }
}

function generationChanged(before: NodeDataSlot | undefined, after: NodeDataGeneration | null): boolean {
  // Nobody held this slot before, so nothing it holds has changed for anyone.
  if (before === undefined) return false
  const previous = before.generation
  if (previous === null || after === null) return previous !== after
  return (
    previous.generation_id !== after.generation_id ||
    previous.row_count !== after.row_count ||
    previous.retention !== after.retention ||
    JSON.stringify(previous.columns) !== JSON.stringify(after.columns)
  )
}

function withJob(
  jobs: Record<string, NodeDataSlotJob>,
  slotKey: string,
  job: NodeDataSlotJob | null,
): Record<string, NodeDataSlotJob> {
  if (!job) {
    if (!jobs[slotKey]) return jobs
    const { [slotKey]: _removed, ...remaining } = jobs
    void _removed
    return remaining
  }
  return { ...jobs, [slotKey]: job }
}

const useNodeDataStore = create<NodeDataStore>((set) => ({
  slots: {},
  jobs: {},
  epoch: 0,

  observePoint: (point, source) =>
    set((state) => {
      const slotKey = point.slot_key
      const previous = state.slots[slotKey]
      const generation = point.generation ?? null
      const job = point.job
        ? {
            jobId: point.job.job_id,
            progress: point.job.progress,
            message: point.job.message,
            // A job this client did not start is still shown; the label of the
            // consumer that started it is unknown, so the point names itself.
            startedByLabel: previous?.job?.startedByLabel ?? point.point.producer_node_id,
          }
        : previous?.job?.jobId && point.state === "building"
          ? previous.job
          : null
      const slot: NodeDataSlot = {
        slotKey,
        ...pointRefLabel(point),
        source,
        kind: point.kind,
        reportedState: point.state,
        reportedDemand: point.demand,
        dataVersion: point.data_version ?? null,
        rowCount: point.row_count ?? null,
        sizeBytes: point.size_bytes ?? null,
        retention: point.retention ?? null,
        generation,
        readsDirectly: point.reads_directly,
        buildEndpoint: point.build_endpoint ?? null,
        clearEndpoint: point.clear_endpoint ?? null,
        job,
        delegatedBuild: previous?.delegatedBuild ?? null,
      }
      const versionChanged =
        previous !== undefined && previous.dataVersion !== slot.dataVersion
      return {
        slots: { ...state.slots, [slotKey]: slot },
        jobs: withJob(state.jobs, slotKey, job),
        epoch:
          generationChanged(previous, generation) || versionChanged ? state.epoch + 1 : state.epoch,
      }
    }),

  startJob: (slotKey, job) =>
    set((state) => {
      const slot = state.slots[slotKey]
      if (!slot) return {}
      const running: NodeDataSlotJob = {
        jobId: job.jobId,
        progress: 0.03,
        message: job.message,
        startedByLabel: job.startedByLabel,
      }
      return {
        slots: {
          ...state.slots,
          [slotKey]: { ...slot, reportedState: "building", job: running },
        },
        jobs: withJob(state.jobs, slotKey, running),
      }
    }),

  updateJobProgress: (slotKey, status) =>
    set((state) => {
      const slot = state.slots[slotKey]
      if (!slot?.job) return {}
      const running: NodeDataSlotJob = {
        ...slot.job,
        progress: status.progress,
        message: status.message || slot.job.message,
      }
      return {
        slots: { ...state.slots, [slotKey]: { ...slot, job: running } },
        jobs: withJob(state.jobs, slotKey, running),
      }
    }),

  finishJob: (slotKey, status) =>
    set((state) => {
      const slot = state.slots[slotKey]
      if (!slot) return {}
      // The job only stops here; what it left behind — a new generation, or
      // nothing after a failure or cancellation — is read back from a fresh
      // `point` request, so every terminal outcome raises the epoch and every
      // consumer of the slot asks again.
      void status
      return {
        slots: { ...state.slots, [slotKey]: { ...slot, job: null } },
        jobs: withJob(state.jobs, slotKey, null),
        epoch: state.epoch + 1,
      }
    }),

  startDelegatedBuild: (slotKey, build) =>
    set((state) => {
      const slot = state.slots[slotKey]
      if (!slot) return {}
      return {
        slots: {
          ...state.slots,
          [slotKey]: { ...slot, reportedState: "building", delegatedBuild: build },
        },
      }
    }),

  reportDelegatedProgress: (slotKey, token, message) =>
    set((state) => {
      const slot = state.slots[slotKey]
      if (!slot?.delegatedBuild || slot.delegatedBuild.token !== token) return {}
      return {
        slots: {
          ...state.slots,
          [slotKey]: {
            ...slot,
            delegatedBuild: { ...slot.delegatedBuild, message: message ?? slot.delegatedBuild.message },
          },
        },
      }
    }),

  retainDelegatedBuild: (slotKey, token, update) =>
    set((state) => {
      const slot = state.slots[slotKey]
      if (!slot?.delegatedBuild || slot.delegatedBuild.token !== token) return {}
      return {
        slots: {
          ...state.slots,
          [slotKey]: {
            ...slot,
            delegatedBuild: { ...slot.delegatedBuild, message: update.message, cancel: update.cancel },
          },
        },
      }
    }),

  finishDelegatedBuild: (slotKey, token) =>
    set((state) => {
      const slot = state.slots[slotKey]
      if (!slot?.delegatedBuild || slot.delegatedBuild.token !== token) return {}
      return {
        slots: { ...state.slots, [slotKey]: { ...slot, delegatedBuild: null } },
        epoch: state.epoch + 1,
      }
    }),

  forgetSlot: (slotKey) =>
    set((state) => {
      if (!state.slots[slotKey]) return {}
      const { [slotKey]: _removed, ...remaining } = state.slots
      void _removed
      return { slots: remaining, jobs: withJob(state.jobs, slotKey, null), epoch: state.epoch + 1 }
    }),

  // The epoch keeps counting through a reset, so a consumer whose graph did
  // not change still observes that its answer no longer belongs to this
  // document and asks again.
  reset: () => set((state) => ({ slots: {}, jobs: {}, epoch: state.epoch + 1 })),
}))

export default useNodeDataStore

/** True when *columns* covers every column in *demand*. */
export function columnsCoverDemand(columns: NodeDataColumns, demand: NodeDataColumns): boolean {
  if (columns === "all") return true
  if (demand === "all") return false
  const held = new Set(columns)
  return demand.every((name) => held.has(name))
}
