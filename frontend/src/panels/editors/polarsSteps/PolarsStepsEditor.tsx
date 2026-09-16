import { Code } from "lucide-react"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"

import { NODE_GROUP_COLORS } from "../../../theme/colors"
import { InputSourcesBar, INPUT_STYLE } from "../_shared"
import type { InputSource, OnReplaceConfig, OnUpdateConfig } from "../_shared"
import AddStepMenu from "./AddStepMenu"
import { createStep, kindLabel, stepDisplayLabel, stepProblem, summarizeStep, variablesBefore } from "./catalogue"
import { columnsBeforeStep } from "./derivedColumns"
import { StepForm } from "./forms"
import GeneratedCodePanel from "./GeneratedCodePanel"
import StepCard, { type StepBadge } from "./StepCard"
import { readSteps, type Step, type StepKind } from "./types"
import { useRenderedSteps } from "./useRenderedSteps"

let stepCounter = 0
function newStepId(): string {
  stepCounter += 1
  return `s${Date.now().toString(36)}${stepCounter.toString(36)}`
}

const SWITCH_CONFIRMATION =
  "Switch this transform to code? The steps are removed and the generated code becomes editable. This cannot be undone."

/** Parse the backend's "Step k: message" text into a zero-based index. */
function parseStepsError(text: unknown): { stepIndex: number | null; message: string } | null {
  if (typeof text !== "string" || text.length === 0) return null
  const match = /^Step (\d+): (.*)$/s.exec(text)
  if (!match) return { stepIndex: null, message: text }
  return { stepIndex: Number.parseInt(match[1], 10) - 1, message: match[2] }
}

function EmptyBox({ children }: { children: React.ReactNode }) {
  return (
    <div
      className="rounded-lg px-3 py-4 text-center text-xs grid gap-2 justify-items-center"
      style={{ color: "var(--text-muted)", background: "var(--bg-input)", border: "1px dashed var(--border)" }}
    >
      {children}
    </div>
  )
}

/**
 * Low-code authoring for a Transform node: a fixed start card, numbered step
 * cards, the grouped `Add step` menu, the locked generated-code panel and the
 * one-way switch to code.
 */
export default function PolarsStepsEditor({
  config,
  onUpdate,
  onReplaceConfig,
  inputSources,
  onDeleteInput,
  errorLine,
  runError,
  upstreamColumns,
}: {
  config: Record<string, unknown>
  onUpdate: OnUpdateConfig
  onReplaceConfig?: OnReplaceConfig
  inputSources: InputSource[]
  onDeleteInput?: (edgeId: string) => void
  errorLine?: number | null
  /** The last run's error message for this node; render errors stay quiet until a run has failed. */
  runError?: string | null
  upstreamColumns?: { name: string; dtype: string }[]
}) {
  const steps = useMemo(() => readSteps(config) ?? [], [config])
  const inputNames = useMemo(() => inputSources.map((source) => source.name), [inputSources])
  const upstream = useMemo(() => (upstreamColumns ?? []).map((c) => c.name), [upstreamColumns])
  const [openIndex, setOpenIndex] = useState<number | null>(null)
  const disclosures = useRef(new Map<number, HTMLButtonElement>())
  const addButton = useRef<HTMLButtonElement | null>(null)

  const setSteps = useCallback((next: Step[]) => onUpdate("steps", next), [onUpdate])

  const rendered = useRenderedSteps(steps, inputNames, (code) => {
    if (config.code !== code) onUpdate("code", code)
  })
  const initialError = useMemo(() => parseStepsError(config._steps_error), [config._steps_error])
  const renderError = rendered.error ?? (rendered.status === "ok" || rendered.status === "empty" ? null : initialError)
  // A step being built is not an error yet: render problems are reported
  // only once the pipeline has run and failed on this node.
  const showErrors = runError != null || errorLine != null
  const shownError = showErrors ? renderError : null

  const startInput = steps[0]?.kind === "source" ? steps[0].input : ""
  const effectiveStart = startInput || (inputNames.length === 1 ? inputNames[0] : "")
  const hasInputs = inputNames.length > 0
  const canAdd = hasInputs && effectiveStart.length > 0

  const focusDisclosure = (index: number | null) => {
    const target = index === null ? addButton.current : disclosures.current.get(index) ?? addButton.current
    target?.focus()
  }

  const setStart = (input: string) => {
    const source: Step = { id: steps[0]?.kind === "source" ? steps[0].id : newStepId(), kind: "source", input }
    setSteps(steps[0]?.kind === "source" ? [source, ...steps.slice(1)] : [source, ...steps])
  }

  // As soon as the start input is known (chosen, or the node's only input),
  // the start step is written to the config so the node renders
  // `df = <input>` and can be previewed before any step is added.
  useEffect(() => {
    if (steps.length === 0 && effectiveStart) setSteps([{ id: newStepId(), kind: "source", input: effectiveStart }])
  }, [steps.length, effectiveStart, setSteps])

  const addStep = (kind: Exclude<StepKind, "source">) => {
    if (!canAdd) return
    const base = steps[0]?.kind === "source" ? steps : [{ id: newStepId(), kind: "source", input: effectiveStart } as Step, ...steps]
    const firstColumn = columnsBeforeStep(upstream, base, base.length)[0] ?? ""
    const step = createStep(kind, newStepId(), firstColumn)
    if (step.kind === "join" && inputNames.length > 0) step.input = inputNames.find((name) => name !== effectiveStart) ?? effectiveStart
    setSteps([...base, step])
    setOpenIndex(base.length)
  }

  const updateStep = (index: number, next: Step) => setSteps(steps.map((s, i) => (i === index ? next : s)))
  const deleteStep = (index: number) => {
    const next = steps.filter((_, i) => i !== index)
    setSteps(next)
    setOpenIndex((current) => (current === index ? null : current !== null && current > index ? current - 1 : current))
    const nextIndex = index < next.length ? index : next.length > 1 ? next.length - 1 : null
    requestAnimationFrame(() => focusDisclosure(nextIndex))
  }
  const move = (index: number, delta: number) => {
    const target = index + delta
    if (target < 1 || target >= steps.length) return
    const next = [...steps]
    ;[next[index], next[target]] = [next[target], next[index]]
    setSteps(next)
    setOpenIndex((current) => (current === index ? target : current === target ? index : current))
  }

  const switchAllowed =
    steps.length === 0 || (rendered.status === "ok" && rendered.revisionRendered === rendered.revision)
  const switchDisabledReason =
    rendered.status === "pending"
      ? "Rendering…"
      : renderError
        ? `Fix ${renderError.stepIndex != null ? stepDisplayLabel(renderError.stepIndex) : "the steps"} first`
        : "Rendering…"
  const switchToCode = () => {
    if (!switchAllowed || !onReplaceConfig) return
    if (!window.confirm(SWITCH_CONFIRMATION)) return
    const rest: Record<string, unknown> = {}
    for (const [key, value] of Object.entries(config)) {
      if (key !== "steps" && key !== "_steps_error") rest[key] = value
    }
    onReplaceConfig({ ...rest, code: steps.length === 0 ? "" : rendered.code })
  }

  const badgeFor = (index: number): StepBadge | null => {
    if (shownError?.stepIndex === index) return { tone: "danger", text: shownError.message }
    if (errorLine != null && errorLine - 1 === index) return { tone: "warning", text: "Failed when the pipeline ran" }
    return null
  }

  const goToError = (index: number) => {
    setOpenIndex(index === 0 ? null : index)
    requestAnimationFrame(() => focusDisclosure(index === 0 ? 0 : index))
  }

  const stepCards = steps.slice(1)

  return (
    <div className="flex-1 flex flex-col min-h-0 overflow-y-auto px-3 py-2 gap-2" data-testid="polars-steps-editor">
      <InputSourcesBar inputSources={inputSources} onDeleteInput={onDeleteInput} />

      <div className="text-[11px] font-bold uppercase tracking-[0.08em] shrink-0" style={{ color: "var(--text-secondary)" }}>
        Steps
      </div>

      {!hasInputs ? (
        <EmptyBox>
          <span>Connect an input to start building steps, or switch to code.</span>
          <button
            type="button"
            onClick={switchToCode}
            className="focus-ring inline-flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs font-medium"
            style={{ color: "var(--text-secondary)", border: "1px solid var(--border)" }}
          >
            <Code size={12} aria-hidden="true" />
            Switch to code
          </button>
        </EmptyBox>
      ) : (
        <>
          <div
            className="rounded-lg px-3 py-2 flex flex-wrap items-center gap-2"
            style={{ background: "var(--bg-input)", border: "1px solid var(--border)", borderLeft: `3px dashed ${NODE_GROUP_COLORS.transform}` }}
            role="group"
            aria-label="Start from"
          >
            <span className="text-xs font-semibold" style={{ color: "var(--text-primary)" }}>
              Start from
            </span>
            <select
              ref={(el) => {
                if (el) disclosures.current.set(0, el as unknown as HTMLButtonElement)
              }}
              aria-label="Start from input"
              value={effectiveStart}
              onChange={(e) => setStart(e.target.value)}
              className="focus-ring flex-1 basis-32 min-w-0 px-2 py-1.5 text-xs font-mono rounded-md"
              style={INPUT_STYLE}
            >
              {effectiveStart === "" && (
                <option value="" disabled>
                  Choose an input
                </option>
              )}
              {inputNames.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
            {badgeFor(0) && (
              <div role="status" className="basis-full text-[11px]" style={{ color: `var(--${badgeFor(0)?.tone})` }}>
                {badgeFor(0)?.text}
              </div>
            )}
          </div>

          {stepCards.length > 0 && (
            <div className="grid gap-1.5" role="list" aria-label="Steps">
              {stepCards.map((step, offset) => {
                const index = offset + 1
                const open = openIndex === index
                const problem = stepProblem(step)
                const key = typeof step?.id === "string" && step.id ? step.id : `invalid-${index}`
                if (problem !== null) {
                  return (
                    <div key={key} role="listitem">
                      <StepCard
                        label="Invalid step"
                        number={index}
                        summary={problem}
                        open={false}
                        onToggle={() => undefined}
                        onDelete={() => deleteStep(index)}
                        badge={{ tone: "danger", text: "This step cannot be edited here. Delete it, or switch to code to keep it." }}
                      />
                    </div>
                  )
                }
                return (
                  <div key={key} role="listitem">
                    <StepCard
                      label={kindLabel(step.kind)}
                      number={index}
                      summary={summarizeStep(step)}
                      open={open}
                      onToggle={() => setOpenIndex(open ? null : index)}
                      onEscape={() => {
                        setOpenIndex(null)
                        focusDisclosure(index)
                      }}
                      onMoveUp={index > 1 ? () => move(index, -1) : undefined}
                      onMoveDown={index < steps.length - 1 ? () => move(index, 1) : undefined}
                      onDelete={() => deleteStep(index)}
                      badge={badgeFor(index)}
                      disclosureRef={(el) => {
                        if (el) disclosures.current.set(index, el)
                        else disclosures.current.delete(index)
                      }}
                    >
                      <StepForm
                        step={step}
                        onChange={(next) => updateStep(index, next)}
                        ctx={{
                          columns: columnsBeforeStep(upstream, steps, index),
                          variables: variablesBefore(steps, index),
                          inputNames,
                          firstFieldId: `${step.id}-first`,
                        }}
                      />
                    </StepCard>
                  </div>
                )
              })}
            </div>
          )}

          <AddStepMenu onAdd={addStep} disabled={!canAdd} buttonRef={(el) => (addButton.current = el)} />

          <GeneratedCodePanel
            code={rendered.code}
            pending={rendered.status === "pending"}
            error={shownError}
            errorLine={errorLine}
            onGoToError={goToError}
            switchEnabled={switchAllowed && onReplaceConfig !== undefined}
            switchDisabledReason={switchDisabledReason}
            onSwitchToCode={switchToCode}
          />
        </>
      )}
    </div>
  )
}
