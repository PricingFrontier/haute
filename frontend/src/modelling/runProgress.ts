/** Approximate work fractions for one action, including its automatic next path. */
export type RunProgress = { id?: string; progress: number; active: boolean }
export const EMPTY_RUN_PROGRESS: RunProgress = { progress: 0, active: false }
export function advanceRunProgress(previous: RunProgress, input: {
  id?: string; progress?: number; automatic?: boolean; queued?: boolean; running?: boolean; failed?: boolean
}): RunProgress {
  const active = Boolean(input.queued || input.running)
  const fresh = active && !input.automatic && !input.queued && input.id !== previous.id
  const floor = fresh || (active && !previous.active) ? 0 : previous.progress
  const raw = Math.max(0, Math.min(1, input.progress ?? 0))
  const fraction = input.queued ? 0.5 : input.automatic ? 0.5 + raw * 0.5 : raw * 0.5
  return { id: input.id ?? previous.id, active,
    progress: active ? Math.min(0.99, Math.max(floor, fraction)) : input.failed ? floor : 1 }
}
