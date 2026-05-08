import type { LiveSwitchNodeDetail, TraceNodeDetail } from "../types/trace"

export function asLiveSwitchDetail(detail: TraceNodeDetail): LiveSwitchNodeDetail {
  return detail as LiveSwitchNodeDetail
}
