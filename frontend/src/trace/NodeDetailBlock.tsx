import type { OptimiserApplyNodeDetail, TraceNodeDetail } from "../types/trace"
import {
  TraceDetailPanel,
} from "./TraceDetail"
import { BandingDetailBlock } from "./BandingDetail"
import { asBandingDetail } from "./bandingRows"
import { LiveSwitchDetailBlock } from "./LiveSwitchDetail"
import { asLiveSwitchDetail } from "./liveSwitchHelpers"
import { ModelScoreDetailBlock } from "./ModelScoreDetail"
import { asModelScoreDetail } from "./modelScoreHelpers"
import {
  OptimiserApplyErrorDetail,
  OptimiserOnlineDetail,
  OptimiserRatebookDetail,
} from "./OptimiserApplyDetail"
import { RatingStepDetailBlock } from "./RatingStepDetail"
import { ScenarioExpanderDetailBlock } from "./ScenarioExpanderDetail"
import { asScenarioExpanderDetail } from "./scenarioExpanderHelpers"
import { isOptimiserApplyErrorDetail } from "../panels/trace/traceStoryView"

function asOptimiserApplyDetail(detail: TraceNodeDetail): OptimiserApplyNodeDetail {
  return detail as OptimiserApplyNodeDetail
}

export function NodeDetailBlock({
  detail,
  tracedColumn,
  showBandingSummary = true,
}: {
  detail: TraceNodeDetail
  tracedColumn?: string | null
  showBandingSummary?: boolean
}) {
  const detailType = detail.detail_type as string | undefined

  if (detailType === "rating_step" && (Array.isArray(detail.tables) || Array.isArray(detail.combined_outputs))) {
    return <RatingStepDetailBlock detail={detail} tracedColumn={tracedColumn} />
  }

  if (detailType === "banding") {
    return <BandingDetailBlock detail={asBandingDetail(detail)} tracedColumn={tracedColumn} showBandingSummary={showBandingSummary} />
  }

  if (detailType === "optimiser_apply") {
    const optimiserDetail = asOptimiserApplyDetail(detail)
    if (isOptimiserApplyErrorDetail(optimiserDetail)) {
      return <OptimiserApplyErrorDetail detail={optimiserDetail} />
    }
    if (optimiserDetail.mode === "online") {
      return <OptimiserOnlineDetail detail={optimiserDetail} />
    }
    if (optimiserDetail.mode === "ratebook") {
      return <OptimiserRatebookDetail detail={optimiserDetail} />
    }
  }

  if (detailType === "model_score") {
    return <ModelScoreDetailBlock detail={asModelScoreDetail(detail)} />
  }

  if (detailType === "scenario_expander") {
    return <ScenarioExpanderDetailBlock detail={asScenarioExpanderDetail(detail)} />
  }

  if (detailType === "live_switch") {
    return <LiveSwitchDetailBlock detail={asLiveSwitchDetail(detail)} />
  }

  // Default: render as JSON
  return (
    <TraceDetailPanel title={detailType ? detailType.replace(/_/g, " ") : "Trace Detail"}>
      <pre className="rounded px-2 py-1.5 text-[10px] font-mono" style={{ color: "var(--text-muted)", background: "rgba(255,255,255,.035)", whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
        {JSON.stringify(detail, null, 2)}
      </pre>
    </TraceDetailPanel>
  )
}
