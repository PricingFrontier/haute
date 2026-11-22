/** Lightweight view projections; no chart parsers or validators enter the initial bundle. */
import type { TrainResponse } from "../api/types"
import type { StagedView } from "./useStagedTraining"
import type { StagePathPayload } from "../generated/api-contracts.generated"
import type { PostFitStage } from "./stageSettings"

export interface AvailablePath { path: StagePathPayload; artifactRevision: string }
export function isAppliedPathPoint(current: TrainResponse | undefined, path: StagePathPayload | undefined, pointId: string | undefined): boolean {
  const point = path?.points.find((entry) => entry.id === pointId)
  if (!path || !point || !current?.staged) return false
  const enabled = current.staged.configuration.enabled as Partial<Record<PostFitStage, boolean>> | undefined
  if (point.identity && (enabled?.[path.stage] !== false
    || current.staged.paths?.[path.stage]?.id !== path.id
    || current.staged.paths?.[path.stage]?.selected_point !== pointId)) return false
  const stage = point.identity ? path.parent : path.stage
  return current.staged.selected_stage === stage && current.staged.stages[stage]?.revision === point.revision
}
export function availablePaths(view: StagedView | undefined): Partial<Record<PostFitStage, AvailablePath>> {
  const result: Partial<Record<PostFitStage, AvailablePath>> = {}
  for (const owner of [view?.state?.current, view?.state?.preview]) {
    if (!owner) continue
    for (const [stage, path] of Object.entries(owner.result.response.staged?.paths ?? {})) {
      result[stage as PostFitStage] = { path, artifactRevision: owner.revision }
    }
  }
  return result
}
export function preparedPath(view: StagedView | undefined): AvailablePath | undefined {
  const response = view?.state?.preview?.result.response
  return Object.values(availablePaths(view)).find((entry) => entry.path.id === response?.staged?.prepared_path)
}
/** Every linked view resolves exactly the same point, including an unchanged parent. */
export function selectedCandidate(view: StagedView | undefined): TrainResponse | undefined {
  const inspection = view?.inspection
  if (inspection) {
    const valid = inspection.currentRevision === view?.state?.current?.revision
      && Object.values(availablePaths(view)).some((entry) => entry.path.id === inspection.pathId
        && entry.artifactRevision === inspection.artifactRevision
        && (!view?.pathStage || entry.path.stage === view.pathStage))
    if (valid) return inspection.response
  }
  const prepared = preparedPath(view)
  return !view?.pathStage || prepared?.path.stage === view.pathStage ? view?.state?.preview?.result.response : undefined
}
export function selectedPathPoint(view: StagedView | undefined): { available: AvailablePath; pointId: string } | undefined {
  const inspection = view?.inspection
  if (inspection) {
    const available = Object.values(availablePaths(view)).find((entry) => entry.path.id === inspection.pathId
      && entry.artifactRevision === inspection.artifactRevision
        && (!view?.pathStage || entry.path.stage === view.pathStage))
    if (available && inspection.currentRevision === view?.state?.current?.revision) {
      return { available, pointId: inspection.pointId }
    }
  }
  const available = preparedPath(view)
  return available && (!view?.pathStage || available.path.stage === view.pathStage)
    ? { available, pointId: available.path.selected_point } : undefined
}
