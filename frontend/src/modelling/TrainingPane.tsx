import { useCallback, useId, useState, type ReactNode } from "react"
import { Play, Square } from "lucide-react"
import { NumberField, type NumberBounds } from "../panels/modelling/NumberField"
import { MODELLING_INPUT_STYLE } from "../panels/modelling/styles"
import { STAGE_LABELS, resolveStageParents, stageMatchesDraft, type ModelStage, type PostFitStage, type StageSettings } from "./stageSettings"
import type { StagePathPayload } from "../generated/api-contracts.generated"
import type { TrainResponse } from "../api/types"
import { StageDecision } from "./StageDecision"
import { isAppliedPathPoint } from "./pathSelection"
import { appliedChain, evidenceLabel } from "./stageEvidence"
import { formatMetricValue, metricLabel } from "../panels/modelling/metricOptions"
import "./disclosures.css"

function StageHeading({ name, number }: { name: string; number: number }) {
  return <>
    <span className="flex h-6 w-6 items-center justify-center rounded-full text-[10px] font-medium" style={{ background: "var(--model-accent-soft)", color: "var(--model-accent)" }}>{String(number).padStart(2, "0")}</span>
    <span className="text-[12px] font-semibold">{name}</span>
  </>
}

function StageCard({ name, number, children, enabled = true, onEnabled, busy = false }: {
  name: string; number: number; children: ReactNode; enabled?: boolean
  onEnabled?: (enabled: boolean) => void; busy?: boolean
}) {
  const [open, setOpen] = useState(number === 1)
  const id = useId()
  return <section className="overflow-hidden rounded-xl border" style={{ borderColor: "var(--border)", background: "var(--bg-panel)" }}>
    <div className="flex items-center pr-3.5">
      <button type="button" className="modelling-disclosure-name focus-ring flex w-full items-center gap-3 px-3.5 py-3 text-left" aria-label={`${name} settings`} aria-expanded={open} aria-controls={id} onClick={() => setOpen(!open)}>
        <StageHeading name={name} number={number} />
      </button>
      <input type="checkbox" aria-label={onEnabled ? `Enable ${name}` : "GBM required"}
        title={onEnabled ? `Enable ${name}` : "The GBM is the required starting model"}
        checked={enabled} disabled={!onEnabled || busy} onChange={(event) => onEnabled?.(event.target.checked)}
        className="h-3.5 w-3.5 shrink-0" style={{ accentColor: "var(--model-accent)" }} />
    </div>
    <div id={id} className="modelling-disclosure-body" data-expanded={open} aria-hidden={!open} inert={!open ? true : undefined}>
      <div className="min-h-0 overflow-hidden"><fieldset disabled={!enabled} className="min-w-0 space-y-3 px-3.5 pb-3 disabled:opacity-45">{children}</fieldset></div>
    </div>
  </section>
}

function StageAction({ name, number, children, action, disabled, running, busy, onAction, status }: {
  name: string; number: number; children?: ReactNode; action: string; disabled: boolean
  running: boolean; busy: boolean; onAction: () => void; status?: string
}) {
  const Icon = running ? Square : Play
  return <section className="overflow-hidden rounded-xl border" style={{ borderColor: "var(--border)", background: "var(--bg-panel)" }}>
    <div className="flex items-center gap-3 px-3.5 py-3">
      <StageHeading name={name} number={number} />
      {(running || status) && <span className="ml-auto text-[10px]" style={{ color: "var(--model-accent)" }}>{running ? "Running" : status}</span>}
    </div>
    {children && <div className="space-y-3 px-3.5 pb-3">{children}</div>}
    <div className="border-t px-3.5 py-2.5" style={{ borderColor: "var(--border)" }}>
      <button type="button" disabled={running ? busy : disabled || busy} onClick={onAction}
        className="focus-ring flex w-full items-center justify-center gap-2 rounded-lg px-3 py-2 text-[11px] font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-35"
        style={{ background: "var(--model-accent-soft)", color: "var(--model-accent)" }}>
        <Icon size={12} />{running ? "Cancel" : action}
      </button>
    </div>
  </section>
}

function Setting({ phase, label, value, onChange, onValidity, ...bounds }: NumberBounds & {
  phase: PostFitStage; label: string; value: number | null; onChange: (value: number | null) => void
  onValidity: (label: string, valid: boolean) => void
}) {
  const validityChanged = useCallback((valid: boolean) => onValidity(`${phase}:${label}`, valid), [phase, label, onValidity])
  return <label className="flex items-center justify-between gap-3 text-[11px]" style={{ color: "var(--text-secondary)" }}>
    <span>{label}</span><NumberField label={label} value={value ?? undefined} step={bounds.integer ? 1 : "any"} {...bounds}
      className="h-7 w-24 min-w-0 rounded-md px-2 font-mono text-[11px]" onCommit={(next) => onChange(next ?? null)} onValidityChange={validityChanged} />
  </label>
}

export function TrainingPane({ view = "train", settings, gbm, stages, pathAvailable, pathChanged, active, busy, blocked, postfitBlocked = false, preview, onRun, onCancel, onUpdate, onApply, onDiscard, finalAvailable = false, onExport, exportPanel, diagnosticsNote, pending = [], currentResult, previewResult, finalResult, previewStage = "pruned", candidatePath, candidatePoint, inspectionPending = false, primaryMetric, evaluationMode, evaluationKey = "", setupSummary, onSetup, onSplit, onInspect, onCompare, onFactor, checkpoints, jobMessage, blockingReasons = [] }: {
  currentResult?: TrainResponse; previewResult?: TrainResponse; finalResult?: TrainResponse
  candidatePath?: StagePathPayload; candidatePoint?: string; inspectionPending?: boolean
  previewStage?: ModelStage; primaryMetric?: string; evaluationMode?: string; evaluationKey?: string; setupSummary?: string
  onSetup?: () => void; onSplit?: () => void; onInspect?: (pane: "paths" | "curve" | "tables", stage?: PostFitStage) => void
  onCompare?: (stage: ModelStage, parent: ModelStage) => void; onFactor?: (factor: string) => void
  checkpoints?: ReactNode; jobMessage?: string; blockingReasons?: readonly string[]
  view?: "params" | "train"
  settings: StageSettings; gbm: ReactNode; stages: readonly ModelStage[]; pathAvailable: boolean; pathChanged: boolean
  active: ModelStage | "finalise" | null; busy: boolean; blocked: boolean; postfitBlocked?: boolean; preview: boolean
  onRun: (stage: ModelStage | "finalise") => void; onCancel: () => void; onUpdate: (settings: StageSettings) => void
  onApply: () => void; onDiscard: () => void
  pending?: readonly ModelStage[]; finalAvailable?: boolean; onExport?: () => void; exportPanel?: ReactNode; diagnosticsNote?: string
}) {
  const [acknowledged, setAcknowledged] = useState<string | null>(null)
  const [invalidFields, setInvalidFields] = useState<ReadonlySet<string>>(new Set())
  const onValidity = useCallback((label: string, valid: boolean) => setInvalidFields((previous) => {
    if (previous.has(label) === !valid) return previous
    const next = new Set(previous)
    if (valid) next.delete(label)
    else next.add(label)
    return next
  }), [])
  const update = <K extends keyof StageSettings>(section: K, values: Partial<StageSettings[K]>) =>
    onUpdate({ ...settings, [section]: { ...settings[section] as object, ...values } })
  const action = (stage: ModelStage | "finalise") => active === stage ? onCancel : () => onRun(stage)
  const invalidPhase = (stage: PostFitStage) => [...invalidFields].some((field) => field.startsWith(`${stage}:`))
  const invalidPath = settings.enabled.pruned && (invalidFields.has("pruned:Path points") || invalidFields.has("pruned:Minimum OOB rows"))
  const enabledPending = pending.filter((stage) => stage === "full" || settings.enabled[stage])
  const disabled = blocked || active !== null
  const toggle = (stage: PostFitStage, enabled: boolean) => onUpdate(resolveStageParents({
    ...settings, enabled: { ...settings.enabled, [stage]: enabled },
  }))
  const showDevelopmentChoice = evaluationMode === "Development" && currentResult?.diagnostics_set !== "development"
  const needsAcknowledgement = showDevelopmentChoice && acknowledged !== evaluationKey
  const appliedPoint = isAppliedPathPoint(currentResult, candidatePath, candidatePoint)
  const linkClass = "hover-chrome focus-ring rounded px-1 py-1 text-[11px] underline underline-offset-4"
  const stageState = (stage: ModelStage, parent?: ModelStage) => {
    if (preview && previewStage === stage && !appliedPoint) return "Candidate ready"
    if (stage !== "full" && !settings.enabled[stage]) return "Disabled"
    if (stage === "full" && needsAcknowledgement) return "Choose evaluation"
    if (postfitBlocked) return stage === "full" ? "Rebuild needed" : "Needs GBM rebuild"
    if (pending.includes(stage)) return "Changes pending"
    if (currentResult?.staged?.selected_stage === stage) return "Applied"
    if (stages.includes(stage)) return "Built"
    if (parent && (!stages.includes(parent) || pending.includes(parent))) return `Needs ${STAGE_LABELS[parent]}`
    return "Ready"
  }
  const blockers = [...blockingReasons]
  if (blocked && blockers.length === 0) blockers.push("Resolve the model configuration before training.")
  if (active !== null) blockers.push("A model run is in progress. Wait for it to finish or use Cancel before starting another stage.")
  if (busy && active === null) blockers.push("A model action is in progress. Wait for it to finish before training.")
  if (needsAcknowledgement) blockers.push("To enable Train GBM, choose validation in Review split or tick the development exploration option above.")
  for (const field of invalidFields) {
    const [phase, label] = field.split(":")
    if (settings.enabled[phase as PostFitStage]) blockers.push(`Correct ${label} in Parameters before running ${label === "Path points" || label === "Minimum OOB rows" ? "GBM or pruning" : STAGE_LABELS[phase as PostFitStage]}.`)
  }
  for (const [stage, name, parent] of [["pruned", "Pruning", "full"], ["banded", "Banding", settings.banding.parent], ["smoothed", "Smoothing", settings.smoothing.parent]] as const) {
    if (!settings.enabled[stage]) blockers.push(`${name}: disabled in Parameters.`)
    else if (postfitBlocked) blockers.push(`${name}: rebuild the GBM with the current settings and data first.`)
    else if (!stages.includes(parent) || pending.includes(parent)) blockers.push(`${name}: build and apply ${STAGE_LABELS[parent]} first.`)
    else if (stage === "pruned" && !pathAvailable && !pathChanged && !(preview && previewStage === stage)) blockers.push("Pruning: no pruning path is available. Adjust the path settings in Parameters and try again.")
  }
  if (!stages.includes("full")) blockers.push("Final model: train the GBM first.")
  else if (postfitBlocked) blockers.push("Final model: rebuild the GBM with the current settings and data first.")
  return <div className="space-y-3 p-3">
    <div hidden={view !== "params"} className="space-y-3">
      <StageCard name="GBM" number={1}>{gbm}</StageCard>
      <StageCard name="Pruning" number={2} enabled={settings.enabled.pruned} onEnabled={(enabled) => toggle("pruned", enabled)} busy={busy || active !== null}>
        <Setting phase="pruned" onValidity={onValidity} label="Path points" value={settings.path.steps} min={2} integer required onChange={(steps) => update("path", { steps: steps! })} />
        <Setting phase="pruned" onValidity={onValidity} label="Minimum OOB rows" value={settings.path.min_rows} min={1} integer required onChange={(min_rows) => update("path", { min_rows: min_rows! })} />
        <label className="flex items-center gap-2 text-[11px]"><input type="checkbox" checked={settings.path.main_effects} onChange={(event) => update("path", { main_effects: event.target.checked })} style={{ accentColor: "var(--model-accent)" }} />Allow pruning main effects</label>
        <label className="flex items-center justify-between text-[11px]">Cutoff<select aria-label="Pruning cutoff" value={settings.pruning.max_tables === null ? "auto" : "manual"} style={MODELLING_INPUT_STYLE} className="h-7 rounded-md px-2" onChange={(event) => update("pruning", { max_tables: event.target.value === "auto" ? null : 1, fraction: .995, tolerance: .001 })}><option value="auto">Automatic</option><option value="manual">Table budget</option></select></label>
        {settings.pruning.max_tables === null ? <>
          <Setting phase="pruned" onValidity={onValidity} label="Retained improvement" value={settings.pruning.fraction} min={0} max={1} exclusiveMin integer={false} required onChange={(fraction) => update("pruning", { fraction: fraction! })} />
          <Setting phase="pruned" onValidity={onValidity} label="Loss tolerance" value={settings.pruning.tolerance} min={0} integer={false} required onChange={(tolerance) => update("pruning", { tolerance: tolerance! })} />
        </> : <Setting phase="pruned" onValidity={onValidity} label="Maximum tables" value={settings.pruning.max_tables} min={0} integer required onChange={(max_tables) => update("pruning", { max_tables })} />}

      </StageCard>
      <StageCard name="Banding" number={3} enabled={settings.enabled.banded} onEnabled={(enabled) => toggle("banded", enabled)} busy={busy || active !== null}>
        <p className="text-[11px]" style={{ color: "var(--text-muted)" }}>Uses {STAGE_LABELS[settings.banding.parent]}</p>
        <Setting phase="banded" onValidity={onValidity} label="Band tolerance" value={settings.banding.tolerance} min={0} exclusiveMin integer={false} required onChange={(tolerance) => update("banding", { tolerance: tolerance! })} />
        <Setting phase="banded" onValidity={onValidity} label="Deviance cap" value={settings.banding.deviance_cap} min={0} exclusiveMin integer={false} required onChange={(deviance_cap) => update("banding", { deviance_cap: deviance_cap! })} />
      </StageCard>
      <StageCard name="Smoothing" number={4} enabled={settings.enabled.smoothed} onEnabled={(enabled) => toggle("smoothed", enabled)} busy={busy || active !== null}>
        <p className="text-[11px]" style={{ color: "var(--text-muted)" }}>Uses {STAGE_LABELS[settings.smoothing.parent]}</p>
        <label className="flex items-center justify-between text-[11px]">Method<select aria-label="Smoothing method" value={settings.smoothing.alpha === null ? "auto" : "manual"} className="h-7 rounded-md px-2" style={MODELLING_INPUT_STYLE}
          onChange={(event) => update("smoothing", { alpha: event.target.value === "auto" ? null : 1, strength: 1 })}><option value="auto">Auto-relative</option><option value="manual">Manual alpha</option></select></label>
        {settings.smoothing.alpha === null ? <>
          <Setting phase="smoothed" onValidity={onValidity} label="Auto multiplier" value={settings.smoothing.strength ?? 1} min={0} exclusiveMin integer={false} required onChange={(strength) => update("smoothing", { strength: strength! })} />
          <p className="text-[10px] leading-relaxed" style={{ color: "var(--text-muted)" }}>Each table chooses its own Auto strength. A multiplier of 1 keeps Auto; zero proposals stay unchanged.</p>
        </> : <Setting phase="smoothed" onValidity={onValidity} label="Manual alpha" value={settings.smoothing.alpha} min={0} integer={false} required onChange={(alpha) => update("smoothing", { alpha })} />}
        <Setting phase="smoothed" onValidity={onValidity} label="Interaction smoothing" value={settings.smoothing.high_order_alpha} min={0} max={1} integer={false} required onChange={(high_order_alpha) => update("smoothing", { high_order_alpha: high_order_alpha! })} />
      </StageCard>
      <section className="space-y-3 rounded-xl border p-3" style={{ borderColor: "var(--border)" }}>
        <p className="text-[11px] font-medium">Path comparison</p>
        <Setting phase="banded" onValidity={onValidity} label="Practical loss tolerance" value={settings.selection?.relative_tolerance ?? .001} min={0} integer={false} required
          onChange={(relative_tolerance) => update("selection", { relative_tolerance: relative_tolerance!, absolute_tolerance: settings.selection?.absolute_tolerance ?? 1e-10 })} />
        <p className="text-[10px] leading-relaxed" style={{ color: "var(--text-muted)" }}>Fraction of loss used to suggest a simpler candidate. 0.001 means 0.1%. Banding’s budgets remain hard limits.</p>
      </section>
    </div>
    <div hidden={view !== "train"} className="space-y-3">
      {diagnosticsNote && <p className="text-[11px] leading-relaxed" style={{ color: "var(--text-muted)" }}>{diagnosticsNote}</p>}
      <StageAction name="GBM" number={1} action="Train GBM" status={stageState("full")} disabled={disabled || needsAcknowledgement || invalidPath} running={active === "full"} busy={busy} onAction={action("full")}>
        {setupSummary && <p className="text-[11px]" style={{ color: "var(--text-secondary)" }}>{setupSummary}</p>}
        {evaluationMode && <p className="text-[11px]" style={{ color: "var(--text-muted)" }}>Next fit · {evaluationMode}</p>}
        {currentResult && <p className="text-[11px]" style={{ color: "var(--text-muted)" }}>Saved evidence · {evidenceLabel(currentResult)}</p>}
        <div className="flex flex-wrap gap-x-3">
          {onSetup && <button type="button" className={linkClass} onClick={onSetup}>Model setup</button>}
          {onSplit && <button type="button" className={linkClass} onClick={onSplit}>Review split</button>}
          {stages.includes("full") && onInspect && <button type="button" className={linkClass} onClick={() => onInspect("paths")}>Inspect paths</button>}
        </div>
        {postfitBlocked && !needsAcknowledgement && <p className="text-[11px] leading-relaxed" style={{ color: "var(--text-muted)" }}>The saved model uses earlier settings or data. Train GBM rebuilds it with your current setup.</p>}
        {showDevelopmentChoice && <label className="flex items-start gap-2 text-[11px] leading-relaxed" style={{ color: "var(--text-muted)" }}>
          <input type="checkbox" aria-label="Use development rows for exploration" className="mt-0.5" checked={acknowledged === evaluationKey} onChange={(event) => setAcknowledged(event.target.checked ? evaluationKey : null)} style={{ accentColor: "var(--model-accent)" }} />
          <span>Use development rows for exploration<span className="block">These rows were used to fit the model; they do not measure unseen-data performance.</span></span>
        </label>}
      </StageAction>
      {([['pruned', 'Pruning', 'full'], ['banded', 'Banding', settings.banding.parent], ['smoothed', 'Smoothing', settings.smoothing.parent]] as const).map(([stage, name, parent], index) => {
        const candidate = preview && previewStage === stage && !appliedPoint
        const snapshot = previewResult?.staged?.stages[stage]
        const changed = candidate && !candidatePath && (pending.includes(parent) || (snapshot !== undefined && !stageMatchesDraft(settings, stage, snapshot, previewResult?.staged?.configuration.applied_path ?? previewResult?.staged?.configuration.path)))
        const point = candidatePath?.points.find((entry) => entry.id === candidatePoint)
        const ready = candidate && !changed && !inspectionPending && point?.status !== "rejected"
        const missingParent = !stages.includes(parent) || pending.includes(parent)
        const available = !postfitBlocked && !missingParent && (stage !== "pruned" || pathAvailable || pathChanged)
        return <StageAction key={stage} name={name} number={index + 2} action={ready ? point?.identity ? "Keep unchanged" : `Apply ${name.toLowerCase()}` : `Build ${name.toLowerCase()}`} status={changed ? "Path needs refresh" : stageState(stage, parent)}
          disabled={disabled || (candidate && (inspectionPending || point?.status === "rejected")) || invalidPhase(stage) || !settings.enabled[stage] || (ready ? postfitBlocked || missingParent : !available)} running={active === stage} busy={busy} onAction={active === stage ? onCancel : ready ? onApply : action(stage)}>
          <p className="text-[11px] leading-relaxed" style={{ color: "var(--text-muted)" }}>{!settings.enabled[stage] ? `${name} disabled in Parameters` : candidate ? changed ? pending.includes(parent) ? `Parent settings changed. Rebuild ${STAGE_LABELS[parent]} first.` : "Settings changed. Build this path again." : "Candidate ready · inspect the path and shapes, then apply" : postfitBlocked ? "Rebuild the GBM to use the changed configuration" : missingParent ? `Build and apply ${STAGE_LABELS[parent]} first, or disable that phase in Parameters` : `Uses ${STAGE_LABELS[parent]}.`}</p>
          {pending.includes(stage) && currentResult?.staged?.stages[stage] && <details className="text-[10px]" style={{ color: "var(--text-muted)" }}><summary className="cursor-pointer">Saved settings</summary><pre className="overflow-x-auto pt-2">{JSON.stringify(currentResult.staged.stages[stage].settings, null, 2)}</pre></details>}
          {candidate && onInspect && <button type="button" className={linkClass} onClick={() => onInspect("paths", stage)}>Inspect {name.toLowerCase()} path</button>}
          {point?.status === "rejected" && <p className="text-[11px]">The training guard rejected this candidate. Select another point in Paths.</p>}
          {candidate && currentResult && previewResult && <StageDecision current={currentResult} candidate={previewResult} stage={stage} metric={primaryMetric} onCompare={onCompare} onFactor={onFactor} />}
          {candidate && <div className="space-y-2 border-t pt-3" style={{ borderColor: "var(--border)" }}>
            <button type="button" disabled={active !== null || busy} onClick={onDiscard} className="hover-chrome focus-ring rounded-lg px-2 py-1 text-[11px] disabled:opacity-35">Clear prepared path</button>
          </div>}
        </StageAction>
      })}
      <StageAction name="Final model" number={5} action="Finalise applied model" status={finalAvailable ? "Ready to export" : undefined} disabled={disabled || postfitBlocked || !stages.includes("full")} running={active === "finalise"} busy={busy} onAction={action("finalise")}>
        <p className="text-[11px] font-medium">Applied output · {appliedChain(currentResult)}</p>
        <p className="text-[11px] leading-relaxed" style={{ color: "var(--text-muted)" }}>Refit this recipe on development rows and evaluate reserved test rows. Visible curves and pending phase settings do not select the final output.</p>
        {finalResult && <div className="space-y-1 text-[11px]">
          <p>Export output · {appliedChain(finalResult)}</p>
          {Object.keys(finalResult.final_test_metrics).length > 0 ? <details><summary className="cursor-pointer">Final-test metrics</summary>{Object.entries(finalResult.final_test_metrics).map(([key, value]) => <p key={key}>{metricLabel(key)} · {formatMetricValue(value)}</p>)}</details> : <p style={{ color: "var(--text-muted)" }}>No reserved test metrics available.</p>}
        </div>}
      </StageAction>
      {checkpoints}
      {jobMessage && <p className="text-[11px] leading-relaxed" style={{ color: "var(--text-muted)" }}>{jobMessage} Previous applied results remain available.</p>}
      {enabledPending.length > 0 && <p className="text-[11px] leading-relaxed" role="status" style={{ color: "var(--text-muted)" }}>{enabledPending.map((name) => STAGE_LABELS[name]).join(", ")}: settings changed. Run that stage to apply them; existing curves retain their saved settings.</p>}
      {finalAvailable && <section className="space-y-3 rounded-xl border p-3" style={{ borderColor: "var(--border)" }}>
        {exportPanel}
        <button type="button" disabled={disabled || busy || postfitBlocked} className="hover-chrome focus-ring w-full rounded-lg px-3 py-2 text-[11px] disabled:opacity-35" onClick={onExport}>{exportPanel ? "Refresh export" : "Export final model"}</button>
      </section>}
      {blockers.length > 0 && <div data-testid="training-blockers" role="status" className="space-y-2 text-[11px] leading-relaxed" style={{ color: "var(--text-muted)" }}>
        {[...new Set(blockers)].map((message) => <p key={message}>{message}</p>)}
      </div>}
    </div>
  </div>
}
