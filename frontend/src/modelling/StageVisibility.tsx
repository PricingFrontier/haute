import { useModelResultKey } from "./modelRuns"
import { STAGES, STAGE_LABELS } from "./stageSettings"
import { defaultVisibleStages } from "./stageResults"
import { useStagedViewKey, useStagedViews } from "./useStagedTraining"

export function StageVisibility({ nodeId, modelId }: { nodeId: string; modelId: string }) {
  const key = useStagedViewKey(useModelResultKey(nodeId, modelId))
  const view = useStagedViews((state) => state.models[key])
  const result = view?.state?.current?.result.response
  const visible = view?.visible ?? defaultVisibleStages(result)
  if (!result?.staged) return null
  return <fieldset className="space-y-1.5 border-b pb-2" style={{ borderColor: "var(--model-accent-border)" }}>
    <legend className="mb-2 text-[10px]" style={{ color: "var(--text-muted)" }}>Predictions</legend>
    {STAGES.map((stage) => <label key={stage} className="flex items-center gap-2 py-0.5 text-[11px]">
      <input type="checkbox" aria-label={`Show ${STAGE_LABELS[stage]} predictions`} disabled={!result.staged?.stages[stage]}
        checked={Boolean(result.staged?.stages[stage]) && visible.includes(stage)} style={{ accentColor: "var(--model-accent)" }}
        onChange={(event) => useStagedViews.getState().update(key, { visible: event.target.checked
          ? [...visible.filter((entry) => entry !== stage), stage] : visible.filter((entry) => entry !== stage) })} />
      <span style={{ opacity: result.staged?.stages[stage] ? 1 : .4 }}>{STAGE_LABELS[stage]}</span>
    </label>)}
  </fieldset>
}
