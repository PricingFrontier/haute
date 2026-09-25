import { create } from "zustand"

import type {
  ExecutionMetrics,
  NodeDataColumns,
  NodeDataGeneration,
  NodeDataPointKind,
  NodeDataPointResponse,
  NodeDataPointState,
  NodeDataProfile,
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

/**
 * A running profile job, which belongs to the data version it was started for.
 * A rebuild can publish a newer version while it runs, so its own version —
 * not the slot's current one — is what its outcome describes.
 */
export interface NodeDataProfileJob extends NodeDataSlotJob {
  dataVersion: string
  /**
   * This tab started the job rather than joining one running elsewhere: only
   * such a job is its consumers' to stop.
   */
  startedHere?: boolean
}

/**
 * The profile of one data version of a point, with the metrics of the job that
 * computed it. Keyed by slot, so every consumer of the point reads the profile
 * once it exists, whichever of them asked for it.
 */
export interface NodeDataProfileEntry {
  dataVersion: string
  profile: NodeDataProfile
  executionMetrics: ExecutionMetrics | null
}

export interface NodeDataStore {
  slots: Record<string, NodeDataSlot>
  profiles: Record<string, NodeDataProfileEntry>
  /**
   * The slot each consumer node was last told it reads, with the identity that
   * answer was given for, so a panel that only wants the columns of its data
   * can read the shared profile without asking the backend again — and never
   * reads the previous point's after the consumer's graph or source changed.
   */
  consumerSlots: Record<string, { slotKey: string; identity: string }>
  /** The running profile job of each slot, for the one background poller. */
  profileJobs: Record<string, NodeDataProfileJob>
  /**
   * Why the last profile of a slot failed, and the data version it was asked
   * for. Every consumer of the point sees the same failure, so one of them can
   * retry it for all of them instead of each asking again in a loop.
   */
  profileFailures: Record<string, { dataVersion: string | null; message: string }>
  /**
   * The running build of each slot, keyed by slot, for the application's one
   * background poller. A job any consumer started — or that a `point` response
   * reported from another client — is polled exactly once.
   */
  jobs: Record<string, NodeDataSlotJob>
  /**
   * A monotonic counter that changes whenever what a consumer reads may have
   * changed: a generation published, widened, evicted, or cleared — including
   * one a preview captured — a build that reached any terminal outcome, a slot
   * forgotten, or the whole store reset.
   * Consumers re-ask the backend on every change; it never goes backwards, so a
   * reset is always observed.
   */
  epoch: number
  /**
   * Bounded record of snapshot generation ids announced to consumers, so a
   * duplicated announcement is a no-op.
   */
  announcedCaptures: string[]
  /**
   * Record an authoritative `point` payload, whichever consumer asked for it.
   * *identity* is the asking consumer's data identity, which its own reads of
   * `consumerSlots` are then gated on.
   */
  observePoint: (point: NodeDataPointResponse, source: string, identity: string) => void
  /** Record a profile a request or a completed job produced. */
  observeProfile: (
    slotKey: string,
    profile: NodeDataProfile,
    executionMetrics?: ExecutionMetrics | null,
  ) => void
  startProfileJob: (
    slotKey: string,
    job: {
      jobId: string
      message: string
      startedByLabel: string
      dataVersion: string
      startedHere?: boolean
    },
  ) => void
  /** Record that asking for a profile failed, so a consumer can offer a retry. */
  reportProfileFailure: (slotKey: string, dataVersion: string | null, message: string) => void
  /** Forget a recorded failure, so the profile is asked for again. */
  clearProfileFailure: (slotKey: string) => void
  updateProfileProgress: (slotKey: string, status: NodeDataStatusResponse) => void
  finishProfileJob: (slotKey: string, status?: NodeDataStatusResponse | null) => void
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
  /**
   * Record that a snapshot was published that no slot reported — a preview's
   * own capture — so every consumer asks the backend again.
   */
  bumpEpoch: () => void
  /**
   * Record snapshot generation ids announced to consumers and answer whether
   * at least one was not already recorded, so a duplicated announcement is a
   * no-op. Does not raise the epoch itself.
   */
  noteAnnouncedCaptures: (generationIds: string[]) => boolean
  reset: () => void
}

function withoutKey<V>(record: Record<string, V>, key: string): Record<string, V> {
  if (!(key in record)) return record
  const { [key]: _removed, ...remaining } = record
  void _removed
  return remaining
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

export const ANNOUNCED_CAPTURES_CAP = 256

const useNodeDataStore = create<NodeDataStore>((set, get) => ({
  slots: {},
  jobs: {},
  profiles: {},
  profileJobs: {},
  profileFailures: {},
  consumerSlots: {},
  announcedCaptures: [],
  epoch: 0,

  observePoint: (point, source, identity) =>
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
        consumerSlots: {
          ...state.consumerSlots,
          [point.consumer_node_id]: { slotKey, identity },
        },
        epoch:
          generationChanged(previous, generation) || versionChanged ? state.epoch + 1 : state.epoch,
      }
    }),

  observeProfile: (slotKey, profile, executionMetrics = null) =>
    set((state) => ({
      profiles: {
        ...state.profiles,
        [slotKey]: { dataVersion: profile.data_version, profile, executionMetrics },
      },
      profileFailures: withoutKey(state.profileFailures, slotKey),
    })),

  startProfileJob: (slotKey, job) =>
    set((state) => ({
      profileJobs: {
        ...state.profileJobs,
        [slotKey]: {
          jobId: job.jobId,
          progress: 0.03,
          message: job.message,
          startedByLabel: job.startedByLabel,
          dataVersion: job.dataVersion,
          startedHere: job.startedHere,
        },
      },
      profileFailures: withoutKey(state.profileFailures, slotKey),
    })),

  reportProfileFailure: (slotKey, dataVersion, message) =>
    set((state) => ({
      profileFailures: { ...state.profileFailures, [slotKey]: { dataVersion, message } },
    })),

  clearProfileFailure: (slotKey) =>
    set((state) => ({ profileFailures: withoutKey(state.profileFailures, slotKey) })),

  updateProfileProgress: (slotKey, status) =>
    set((state) => {
      const running = state.profileJobs[slotKey]
      if (!running) return {}
      return {
        profileJobs: {
          ...state.profileJobs,
          [slotKey]: {
            ...running,
            progress: status.progress,
            message: status.message || running.message,
          },
        },
      }
    }),

  finishProfileJob: (slotKey, status) =>
    set((state) => {
      const finished = state.profileJobs[slotKey]
      if (!finished) return {}
      const { [slotKey]: _finished, ...remaining } = state.profileJobs
      void _finished
      const profile = status?.profile ?? null
      return {
        profileJobs: remaining,
        // A completed job carries the profile it computed, so no consumer has
        // to ask for it again; any other outcome leaves the profile absent and
        // states why, so a consumer can offer to run it again.
        profiles: profile
          ? {
              ...state.profiles,
              [slotKey]: {
                dataVersion: profile.data_version,
                profile,
                executionMetrics: status?.execution_metrics ?? null,
              },
            }
          : state.profiles,
        // The failure belongs to the version this job profiled, not to
        // whatever the point holds now: a rebuild that published while it ran
        // must still be profiled.
        profileFailures: profile
          ? withoutKey(state.profileFailures, slotKey)
          : {
              ...state.profileFailures,
              [slotKey]: {
                dataVersion: finished.dataVersion,
                message:
                  status?.error || status?.message || "Profiling this data did not finish.",
              },
            },
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
      const { [slotKey]: _profile, ...remainingProfiles } = state.profiles
      void _profile
      const { [slotKey]: _profileJob, ...remainingProfileJobs } = state.profileJobs
      void _profileJob
      return {
        slots: remaining,
        jobs: withJob(state.jobs, slotKey, null),
        profiles: remainingProfiles,
        profileJobs: remainingProfileJobs,
        profileFailures: withoutKey(state.profileFailures, slotKey),
        epoch: state.epoch + 1,
      }
    }),

  bumpEpoch: () => set((state) => ({ epoch: state.epoch + 1 })),

  noteAnnouncedCaptures: (generationIds) => {
    if (generationIds.length === 0) return false
    const current = get().announcedCaptures
    const existing = new Set(current)
    const toAdd: string[] = []
    for (const id of generationIds) {
      if (!existing.has(id)) {
        existing.add(id)
        toAdd.push(id)
      }
    }
    if (toAdd.length === 0) return false
    const updated = [...current, ...toAdd]
    set({
      announcedCaptures:
        updated.length > ANNOUNCED_CAPTURES_CAP
          ? updated.slice(updated.length - ANNOUNCED_CAPTURES_CAP)
          : updated,
    })
    return true
  },

  // The epoch keeps counting through a reset, so a consumer whose graph did
  // not change still observes that its answer no longer belongs to this
  // document and asks again.
  reset: () =>
    set((state) => ({
      slots: {},
      jobs: {},
      profiles: {},
      profileJobs: {},
      profileFailures: {},
      consumerSlots: {},
      announcedCaptures: [],
      epoch: state.epoch + 1,
    })),
}))

export default useNodeDataStore

/**
 * The slot entry one consumer node reads, exactly as the store holds it, so a
 * selector returns a stable reference.
 */
export function slotForConsumer(
  state: NodeDataStore,
  consumerNodeId: string,
  identity: string | null,
): NodeDataSlot | null {
  const slotKey = consumerSlotKey(state, consumerNodeId, identity)
  return (slotKey ? state.slots[slotKey] : undefined) ?? null
}

/**
 * The slot a consumer reads *for this identity*. A mapping recorded under
 * another identity — the point before the consumer's source switched or its
 * graph was rewired — is not this consumer's data and is never returned.
 */
function consumerSlotKey(
  state: NodeDataStore,
  consumerNodeId: string,
  identity: string | null,
): string | null {
  const mapping = state.consumerSlots[consumerNodeId]
  if (!mapping || identity === null || mapping.identity !== identity) return null
  return mapping.slotKey
}

/**
 * The profile of the data one consumer node reads, if the shared store holds
 * one. This is a read of what other consumers have already established; it
 * never asks the backend, so a panel that only labels columns can use it.
 */
export function profileForConsumer(
  state: NodeDataStore,
  consumerNodeId: string,
  identity: string | null,
): NodeDataProfileEntry | null {
  const slotKey = consumerSlotKey(state, consumerNodeId, identity)
  if (!slotKey) return null
  const entry = state.profiles[slotKey]
  const slot = state.slots[slotKey]
  // Only the profile of the data the point currently holds is shown.
  if (!entry || !slot || slot.dataVersion !== entry.dataVersion) return null
  return entry
}

/** True when *columns* covers every column in *demand*. */
export function columnsCoverDemand(columns: NodeDataColumns, demand: NodeDataColumns): boolean {
  if (columns === "all") return true
  if (demand === "all") return false
  const held = new Set(columns)
  return demand.every((name) => held.has(name))
}
