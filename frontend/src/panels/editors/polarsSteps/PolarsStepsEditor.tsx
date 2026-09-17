import { Code } from "lucide-react"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"

import { NODE_GROUP_COLORS } from "../../../theme/colors"
import type { StepStart } from "../../../utils/polarsStepInputs"
import { InputSourcesBar, INPUT_STYLE } from "../_shared"
import type { InputSource, OnReplaceConfig, OnUpdateConfig } from "../_shared"
import AddStepMenu from "./AddStepMenu"
import { createStep, kindLabel, stepDisplayLabel, stepProblem, variablesBefore } from "./catalogue"
import { columnsBeforeStep } from "./derivedColumns"
import { StepForm } from "./forms"
import GeneratedCodePanel from "./GeneratedCodePanel"
import StepCard, { type StepBadge, type StepDrag } from "./StepCard"
import { summarizeStep } from "./summary"
import { readSteps, type Step, type StepKind } from "./types"
import { useRenderedSteps } from "./useRenderedSteps"

let stepCounter = 0
function newStepId(): string {
  stepCounter += 1
  return `s${Date.now().toString(36)}${stepCounter.toString(36)}`
}

const SWITCH_CONFIRMATION =
  "Switch this node to code? The steps are removed and the generated code becomes editable. This cannot be undone."

/** Join and concat need an input name to reference; withheld while none is eligible. */
const INPUT_REFERENCING_KINDS: ReadonlySet<Exclude<StepKind, "source">> = new Set(["join", "concat"])

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
 * Low-code authoring: a fixed start card, numbered step cards, the grouped
 * `Add step` menu, the locked generated-code panel and the one-way switch to
 * code. In `input` mode (a Transform) the start card chooses the input and
 * the first step is the `source` step; in `frame` mode (a surface whose `df`
 * is already bound) there is no start card, every card is a numbered step,
 * and a `source` step is invalid.
 */
export default function PolarsStepsEditor({
  config,
  onUpdate,
  onReplaceConfig,
  inputSources,
  inputNames,
  onDeleteInput,
  errorLine,
  runError,
  upstreamColumns,
  start,
}: {
  config: Record<string, unknown>
  onUpdate: OnUpdateConfig
  onReplaceConfig?: OnReplaceConfig
  /** The input chips on display. */
  inputSources: InputSource[]
  /** The input names the steps may reference (the surface's eligibility, not the chips). */
  inputNames: string[]
  onDeleteInput?: (edgeId: string) => void
  errorLine?: number | null
  /** The last run's error message for this node; render errors stay quiet until a run has failed. */
  runError?: string | null
  upstreamColumns?: { name: string; dtype: string }[]
  start: StepStart
}) {
  const isFrame = start === "frame"
  const steps = useMemo(() => readSteps(config) ?? [], [config])
  const upstream = useMemo(() => (upstreamColumns ?? []).map((c) => c.name), [upstreamColumns])
  const [openIndex, setOpenIndex] = useState<number | null>(null)
  // The start card's select and every step card's disclosure button, by schema index.
  const disclosures = useRef(new Map<number, HTMLElement>())
  const addButton = useRef<HTMLButtonElement | null>(null)

  const setSteps = useCallback((next: Step[]) => onUpdate("steps", next), [onUpdate])

  const rendered = useRenderedSteps(steps, inputNames, start, (code) => {
    if (config.code !== code) onUpdate("code", code)
  })
  const initialError = useMemo(() => parseStepsError(config._steps_error), [config._steps_error])
  const renderError = rendered.error ?? (rendered.status === "ok" || rendered.status === "empty" ? null : initialError)
  // A step being built is not an error yet: render problems are reported
  // only once the pipeline has run and failed on this node.
  const showErrors = runError != null || errorLine != null
  const shownError = showErrors ? renderError : null

  const startStep = !isFrame && steps[0]?.kind === "source" && stepProblem(steps[0]) === null ? steps[0] : null
  const startInput = startStep?.input ?? ""
  const effectiveStart = isFrame ? "" : startInput || (steps.length === 0 && inputNames.length === 1 ? inputNames[0] : "")
  const hasInputs = inputNames.length > 0
  const canAdd = isFrame || inputNames.includes(effectiveStart)
  // In input mode index 0 is the start step and never a movable card.
  const firstMovable = isFrame ? 0 : 1

  const focusDisclosure = (index: number | null) => {
    const target = index === null ? addButton.current : disclosures.current.get(index) ?? addButton.current
    target?.focus()
  }

  const setStart = (input: string) => {
    const source: Step = { id: startStep?.id ?? newStepId(), kind: "source", input }
    setSteps(steps[0]?.kind === "source" ? [source, ...steps.slice(1)] : [source, ...steps])
    if (steps.length > 0 && steps[0]?.kind !== "source") {
      setOpenIndex((current) => current === null ? null : current + 1)
    }
  }

  // As soon as the start input is known (chosen, or the node's only input),
  // the start step is written to the config so the node renders
  // `df = <input>` and can be previewed before any step is added.
  useEffect(() => {
    if (!isFrame && steps.length === 0 && effectiveStart) {
      setSteps([{ id: newStepId(), kind: "source", input: effectiveStart }])
    }
  }, [isFrame, steps.length, effectiveStart, setSteps])

  const addStep = (kind: Exclude<StepKind, "source">) => {
    if (!canAdd) return
    const base = isFrame || steps[0]?.kind === "source"
      ? steps
      : [{ id: newStepId(), kind: "source", input: effectiveStart } as Step, ...steps]
    const step = createStep(kind, newStepId())
    if (step.kind === "join" && inputNames.length > 0) step.input = inputNames.find((name) => name !== effectiveStart) ?? effectiveStart
    setSteps([...base, step])
    setOpenIndex(base.length)
  }

  const updateStep = (index: number, next: Step) => setSteps(steps.map((s, i) => (i === index ? next : s)))
  const deleteStep = (index: number) => {
    const next = steps.filter((_, i) => i !== index)
    setSteps(next)
    setOpenIndex((current) => (current === index ? null : current !== null && current > index ? current - 1 : current))
    const nextIndex = index < next.length ? index : next.length > firstMovable ? next.length - 1 : null
    requestAnimationFrame(() => focusDisclosure(nextIndex))
  }
  const move = (index: number, delta: number) => {
    const target = index + delta
    if (target < firstMovable || target >= steps.length) return
    const next = [...steps]
    ;[next[index], next[target]] = [next[target], next[index]]
    setSteps(next)
    setOpenIndex((current) => (current === index ? target : current === target ? index : current))
  }

  // Drag a card by its header and drop it on another card to put it there.
  const [dragIndex, setDragIndex] = useState<number | null>(null)
  const [dropIndex, setDropIndex] = useState<number | null>(null)
  const reorder = (from: number, to: number) => {
    if (from === to || from < firstMovable || to < firstMovable || from >= steps.length || to >= steps.length) return
    const next = [...steps]
    const [moved] = next.splice(from, 1)
    next.splice(to, 0, moved)
    setSteps(next)
    setOpenIndex((current) => {
      if (current === null) return null
      if (current === from) return to
      if (from < current && current <= to) return current - 1
      if (to <= current && current < from) return current + 1
      return current
    })
  }
  const dragFor = (index: number): StepDrag => ({
    onStart: (event) => {
      event.dataTransfer?.setData("text/plain", String(index))
      if (event.dataTransfer) event.dataTransfer.effectAllowed = "move"
      setDragIndex(index)
    },
    onOver: (event) => {
      if (dragIndex === null) return
      event.preventDefault()
      if (event.dataTransfer) event.dataTransfer.dropEffect = "move"
      if (dropIndex !== index) setDropIndex(index)
    },
    onDrop: (event) => {
      event.preventDefault()
      if (dragIndex !== null) reorder(dragIndex, index)
      setDragIndex(null)
      setDropIndex(null)
    },
    onEnd: () => {
      setDragIndex(null)
      setDropIndex(null)
    },
    target: dropIndex === index && dragIndex !== null && dragIndex !== index,
    dragging: dragIndex === index,
  })

  const switchAllowed =
    steps.length === 0 || (rendered.status === "ok" && rendered.revisionRendered === rendered.revision)
  const switchDisabledReason =
    rendered.status === "pending"
      ? "Rendering…"
      : renderError
        ? `Fix ${renderError.stepIndex != null ? stepDisplayLabel(renderError.stepIndex, start) : "the steps"} first`
        : "Rendering…"
  const switchEnabled = switchAllowed && onReplaceConfig !== undefined
  const switchToCode = () => {
    if (!switchAllowed || !onReplaceConfig) return
    if (!window.confirm(SWITCH_CONFIRMATION)) return
    const rest: Record<string, unknown> = {}
    for (const [key, value] of Object.entries(config)) {
      if (key !== "steps" && key !== "_steps_error") rest[key] = value
    }
    onReplaceConfig({ ...rest, code: steps.length === 0 ? "" : rendered.code })
  }

  const currentStepLines = rendered.status === "ok" && rendered.revisionRendered === rendered.revision ? rendered.stepLines : []
  const runtimeErrorLineFor = (index: number): number | null => {
    const range = currentStepLines[index]
    if (errorLine == null || range === undefined) return null
    const [start, end] = range
    return errorLine >= start && errorLine <= end ? errorLine - start + 1 : null
  }
  const badgeFor = (index: number): StepBadge | null => {
    if (shownError?.stepIndex === index) return { tone: "danger", text: shownError.message }
    if (runtimeErrorLineFor(index) != null) return { tone: "warning", text: "Failed when the pipeline ran" }
    return null
  }

  const goToError = (index: number) => {
    const opens = !isFrame && index === 0 ? null : index
    setOpenIndex(opens)
    requestAnimationFrame(() => focusDisclosure(index))
  }

  const cardOffset = startStep === null ? 0 : 1
  const stepCards = steps.slice(cardOffset)
  const withheldKinds = inputNames.length === 0 ? INPUT_REFERENCING_KINDS : undefined

  const switchButton = (
    <button
      type="button"
      onClick={switchToCode}
      disabled={!switchEnabled}
      title={switchEnabled ? "Replace the steps with editable code" : switchDisabledReason}
      className="focus-ring inline-flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs font-medium disabled:opacity-50 disabled:cursor-not-allowed"
      style={{ color: "var(--text-secondary)", border: "1px solid var(--border)" }}
    >
      <Code size={12} aria-hidden="true" />
      Switch to code
    </button>
  )

  return (
    <div className="flex-1 flex flex-col min-h-0 overflow-y-auto px-3 py-2 gap-2" data-testid="polars-steps-editor">
      <InputSourcesBar inputSources={inputSources} onDeleteInput={onDeleteInput} />

      <div className="text-[11px] font-bold uppercase tracking-[0.08em] shrink-0" style={{ color: "var(--text-secondary)" }}>
        Steps
      </div>

      {!isFrame && !hasInputs ? (
        <EmptyBox>
          <span>Connect an input to start building steps, or switch to code.</span>
          {switchButton}
        </EmptyBox>
      ) : (
        <>
          {/* In frame mode df is the node's own frame, so there is nothing to choose and no start card. */}
          {!isFrame && (
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
                  if (el) disclosures.current.set(0, el)
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
                {effectiveStart !== "" && !inputNames.includes(effectiveStart) && (
                  <option value={effectiveStart} disabled>
                    {effectiveStart} (not connected)
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
          )}

          {stepCards.length > 0 && (
            <div className="grid gap-1.5" role="list" aria-label="Steps">
              {stepCards.map((step, offset) => {
                const index = offset + cardOffset
                const displayNumber = isFrame ? index + 1 : index
                const open = openIndex === index
                const problem =
                  stepProblem(step)
                  ?? (isFrame && step.kind === "source"
                    ? "This node starts from df; delete this step."
                    : !isFrame && index === 0
                      ? "Choose the input to start from above."
                      : null)
                const key = typeof step?.id === "string" && step.id ? step.id : `invalid-${index}`
                if (problem !== null) {
                  const invalidStart = !isFrame && index === 0
                  return (
                    <div key={key} role="listitem">
                      <StepCard
                        label={invalidStart ? "Invalid start step" : "Invalid step"}
                        number={invalidStart ? null : displayNumber}
                        summary={problem}
                        open={false}
                        onToggle={() => undefined}
                        onDelete={() => deleteStep(index)}
                        badge={{ tone: "danger", text: "This step cannot be edited here. Delete it to repair the step list." }}
                      />
                    </div>
                  )
                }
                return (
                  <div key={key} role="listitem">
                    <StepCard
                      label={kindLabel(step.kind)}
                      number={displayNumber}
                      summary={summarizeStep(step)}
                      open={open}
                      onToggle={() => setOpenIndex(open ? null : index)}
                      onEscape={() => {
                        setOpenIndex(null)
                        focusDisclosure(index)
                      }}
                      onMoveUp={index > firstMovable ? () => move(index, -1) : undefined}
                      onMoveDown={index < steps.length - 1 ? () => move(index, 1) : undefined}
                      onDelete={() => deleteStep(index)}
                      drag={dragFor(index)}
                      badge={badgeFor(index)}
                      disclosureRef={(el) => {
                        if (el) disclosures.current.set(index, el)
                        else disclosures.current.delete(index)
                      }}
                    >
                      {open && <StepForm
                        step={step}
                        onChange={(next) => updateStep(index, next)}
                        ctx={{
                          columns: columnsBeforeStep(upstream, steps, index),
                          variables: variablesBefore(steps, index),
                          inputNames,
                          firstFieldId: `${step.id}-first`,
                          errorLine: step.kind === "free_code" ? runtimeErrorLineFor(index) : null,
                        }}
                      />}
                    </StepCard>
                  </div>
                )
              })}
            </div>
          )}

          <AddStepMenu onAdd={addStep} disabled={!canAdd} withhold={withheldKinds} buttonRef={(el) => (addButton.current = el)} />

          <GeneratedCodePanel
            start={start}
            code={rendered.code}
            pending={rendered.status === "pending"}
            error={shownError}
            errorLine={currentStepLines.length > 0 ? errorLine : null}
            onGoToError={goToError}
            switchEnabled={switchEnabled}
            switchDisabledReason={switchDisabledReason}
            onSwitchToCode={switchToCode}
          />
        </>
      )}
    </div>
  )
}
