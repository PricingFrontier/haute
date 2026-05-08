import type { ScenarioExpanderNodeDetail, TraceNodeDetail } from "../types/trace"

export function asScenarioExpanderDetail(detail: TraceNodeDetail): ScenarioExpanderNodeDetail {
  return detail as ScenarioExpanderNodeDetail
}
