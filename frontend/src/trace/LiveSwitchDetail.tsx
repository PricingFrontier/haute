import type { LiveSwitchNodeDetail } from "../types/trace"
import {
  TraceDetailAlert,
  TraceDetailChip,
  TraceDetailPanel,
} from "./TraceDetail"

const traceDetailValueStyle = {
  color: "var(--text-secondary)",
  fontSize: 11,
  fontFamily: "var(--font-data)",
}

export function LiveSwitchDetailBlock({ detail }: {
  detail: LiveSwitchNodeDetail
}) {
  const valueStyle = traceDetailValueStyle
  const liveSwitch = detail
  const activeBranch = liveSwitch.active_branch
  const activeScenario = liveSwitch.active_scenario
  const prunedBranches = Array.isArray(liveSwitch.pruned_branches) ? liveSwitch.pruned_branches : []
  return (
    <TraceDetailPanel
      title="Live Switch"
      summary={(
        <>
        {activeBranch && (
          <TraceDetailChip>active branch: {activeBranch}</TraceDetailChip>
        )}
        {activeScenario && (
          <TraceDetailChip tone="muted">scenario: {activeScenario}</TraceDetailChip>
        )}
        </>
      )}
    >
      {prunedBranches.length > 0 && (
        <div style={valueStyle}>Pruned branches: {prunedBranches.join(", ")}</div>
      )}
      {liveSwitch.error && (
        <TraceDetailAlert>
          Trace detail failed: {liveSwitch.error}
        </TraceDetailAlert>
      )}
    </TraceDetailPanel>
  )
}
