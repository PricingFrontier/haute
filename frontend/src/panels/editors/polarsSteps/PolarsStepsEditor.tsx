import { AlertTriangle, Code } from "lucide-react"
import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react"

import { NODE_GROUP_COLORS } from "../../../theme/colors"
import type { StepStart } from "../../../utils/polarsStepInputs"
import { InputSourcesBar, INPUT_STYLE } from "../_shared"
import type { InputSource, OnReplaceConfig, OnUpdateConfig } from "../_shared"
import AddStepMenu from "./AddStepMenu"
import { STEP_CATALOGUE, createStep, kindLabel, stepDisplayLabel, stepProblem, variablesBefore } from "./catalogue"
import { columnChange, columnNames, columnsAtEachStep, unknownColumnsOf, type ColumnSource } from "./derivedColumns"
import { StepForm } from "./forms"
import GeneratedCodePanel from "./GeneratedCodePanel"
import StepCard, { type StepBadge, type StepDrag } from "./StepCard"
import { STEP_ICONS } from "./stepIcons"
import { schemaFor } from "./stepSchema"
import { summaryParts, unfinishedPart } from "./summary"
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

/** The kinds a node with no steps yet offers as a first step. */
const FIRST_STEP_KINDS: Array<Exclude<StepKind, "source">> = ["filter", "with_column", "group_by"]

/** Canvas shortcuts (with Ctrl/Cmd) that act on the graph and must not fire from the step editor. */
const GRAPH_SHORTCUT_KEYS: ReadonlySet<string> = new Set(["a", "c", "g", "v"])

/** Parse the backend's "Step k: message" text into a zero-based index. */
function parseStepsError(text: unknown): { stepIndex: number | null; message: string } | null {
  if (typeof text !== "string" || text.length === 0) return null
  const match = /^Step (\d+): (.*)$/s.exec(text)
  if (!match) return { stepIndex: null, message: text }
  return { stepIndex: Number.parseInt(match[1], 10) - 1, message: match[2] }
}

function isTextEntry(element: HTMLElement): boolean {
  return element.tagName === "INPUT" || element.tagName === "TEXTAREA" || element.isContentEditable || element.closest(".cm-editor") !== null
}

/**
 * Keys the canvas would act on stop at the step editor: Delete and Backspace
 * outside a text box, and the graph's copy, paste, select-all and group
 * shortcuts. Save, undo and redo, search, fit view, help and Escape stay
 * global.
 */
function keepGraphKeysInEditor(event: KeyboardEvent<HTMLDivElement>) {
  const target = event.target as HTMLElement
  if ((event.key === "Delete" || event.key === "Backspace") && !isTextEntry(target)) {
    event.stopPropagation()
  } else if ((event.ctrlKey || event.metaKey) && GRAPH_SHORTCUT_KEYS.has(event.key.toLowerCase())) {
    event.stopPropagation()
  }
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

/** The note under a collapsed card whose step names columns that are not in the data there. */
function UnknownColumnsNote({ names }: { names: string[] }) {
  return (
    <p role="status" className="m-0 flex items-start gap-1 text-[11px] leading-snug" style={{ color: "var(--warning)" }}>
      <AlertTriangle size={11} aria-hidden="true" className="mt-0.5 shrink-0" />
      <span>
        Not in the data at this step:{" "}
        {names.map((name, index) => (
          <span key={name}>
            {index > 0 && ", "}
            <code className="font-mono">{name}</code>
          </span>
        ))}
      </span>
    </p>
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
  /** The input chips on display, each with its columns as the last preview recorded them. */
  inputSources: InputSource[]
  /** The input names the steps may reference (the surface's eligibility, not the chips). */
  inputNames: string[]
  onDeleteInput?: (edgeId: string) => void
  errorLine?: number | null
  /** The last run's error message for this node; render errors stay quiet until a run has failed. */
  runError?: string | null
  /** The frame a frame-mode surface starts from, as far as the preview knows it. */
  upstreamColumns?: { name: string; dtype: string }[]
  start: StepStart
}) {
  const isFrame = start === "frame"
  const steps = useMemo(() => readSteps(config) ?? [], [config])
  const [openIndex, setOpenIndex] = useState<number | null>(null)
  // The step whose generated lines are tinted: the card or code line under the pointer or focus.
  const [pointedStep, setPointedStep] = useState<number | null>(null)
  // A step just added, whose first field takes focus once its card has rendered.
  const focusStepId = useRef<string | null>(null)
  // The start card's select and every step card's disclosure button, by schema index.
  const disclosures = useRef(new Map<number, HTMLElement>())
  const addButton = useRef<HTMLButtonElement | null>(null)

  const source: ColumnSource = useMemo(
    () => ({
      inputs: Object.fromEntries(inputSources.flatMap((s) => (s.columns?.length ? [[s.name, s.columns]] : []))),
      frame: isFrame ? (upstreamColumns ?? []) : [],
    }),
    [inputSources, isFrame, upstreamColumns],
  )
  const columnStates = useMemo(() => columnsAtEachStep(source, steps), [source, steps])

  const setSteps = useCallback((next: Step[]) => onUpdate("steps", next), [onUpdate])

  const rendered = useRenderedSteps(steps, inputNames, start, (code) => {
    if (config.code !== code) onUpdate("code", code)
  })
  const initialError = useMemo(() => parseStepsError(config._steps_error), [config._steps_error])
  const renderError = rendered.error ?? (rendered.status === "ok" || rendered.status === "empty" ? null : initialError)
  // A step being built is not an error yet: render problems are reported as
  // errors only once the pipeline has run and failed on this node; until then
  // they are neutral notes of what a step still needs.
  const showErrors = runError != null || errorLine != null
  const shownError = showErrors ? renderError : null
  const stale = rendered.status === "error"

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
    const sourceStep: Step = { id: startStep?.id ?? newStepId(), kind: "source", input }
    setSteps(steps[0]?.kind === "source" ? [sourceStep, ...steps.slice(1)] : [sourceStep, ...steps])
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

  // A new card's first field takes focus once the card has rendered open.
  useEffect(() => {
    const id = focusStepId.current
    if (id === null) return
    const index = steps.findIndex((s) => s.id === id)
    if (index < 0) return
    focusStepId.current = null
    ;(document.getElementById(`${id}-first`) ?? disclosures.current.get(index))?.focus()
  })

  const addStep = (kind: Exclude<StepKind, "source">) => {
    if (!canAdd) return
    const base = isFrame || steps[0]?.kind === "source"
      ? steps
      : [{ id: newStepId(), kind: "source", input: effectiveStart } as Step, ...steps]
    const step = createStep(kind, newStepId())
    // With one input there is nothing to join; the card says to connect one.
    if (step.kind === "join") step.input = inputNames.find((name) => name !== effectiveStart) ?? ""
    setSteps([...base, step])
    setOpenIndex(base.length)
    focusStepId.current = step.id
  }

  const updateStep = (index: number, next: Step) => setSteps(steps.map((s, i) => (i === index ? next : s)))
  const deleteStep = (index: number) => {
    const next = steps.filter((_, i) => i !== index)
    setSteps(next)
    setOpenIndex((current) => (current === index ? null : current !== null && current > index ? current - 1 : current))
    setPointedStep(null)
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
    setPointedStep(null)
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
    const [first, last] = range
    return errorLine >= first && errorLine <= last ? errorLine - first + 1 : null
  }
  const runMessage = typeof runError === "string" && runError.trim() ? runError.trim().split("\n")[0] : "Failed when the pipeline ran"
  const badgeFor = (index: number): StepBadge | null => {
    if (shownError?.stepIndex === index) return { tone: "danger", text: shownError.message }
    if (runtimeErrorLineFor(index) != null) return { tone: "warning", text: runMessage }
    return null
  }
  // A run that failed on a step's lines names that step in the code panel, with Go to error.
  const runtimeStep = errorLine == null ? -1 : currentStepLines.findIndex(([first, last]) => errorLine >= first && errorLine <= last)
  const panelError = shownError ?? (runtimeStep >= 0 ? { stepIndex: runtimeStep, message: runMessage } : null)
  /**
   * What the step the render names still needs, while no run has failed: in
   * plain words for the common half-built states ("Needs a formula"), else the
   * renderer's own message.
   */
  const needFor = (index: number): string | null => {
    if (showErrors || renderError?.stepIndex !== index) return null
    const part = stepProblem(steps[index]) === null ? unfinishedPart(steps[index]) : null
    return part ? `Needs ${part}.` : `Needs: ${renderError.message}`
  }
  const note = !showErrors && stale && renderError
    ? renderError.stepIndex !== null && stepProblem(steps[renderError.stepIndex]) === null && unfinishedPart(steps[renderError.stepIndex])
      ? { stepIndex: renderError.stepIndex, message: `it needs ${unfinishedPart(steps[renderError.stepIndex])}.` }
      : renderError
    : null

  const goToError = (index: number) => {
    const opens = !isFrame && index === 0 ? null : index
    setOpenIndex(opens)
    requestAnimationFrame(() => focusDisclosure(index))
  }
  const openFromCode = (index: number) => {
    if (!isFrame && index === 0) {
      focusDisclosure(0)
      return
    }
    setOpenIndex(index)
    requestAnimationFrame(() => focusDisclosure(index))
  }

  const cardOffset = startStep === null ? 0 : 1
  const stepCards = steps.slice(cardOffset)
  const withheldKinds = inputNames.length === 0 ? INPUT_REFERENCING_KINDS : undefined
  const firstKinds = FIRST_STEP_KINDS.filter((kind) => !withheldKinds?.has(kind))

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
    <div className="flex-1 flex flex-col min-h-0 overflow-y-auto px-3 py-2 gap-2" data-testid="polars-steps-editor" onKeyDown={keepGraphKeysInEditor}>
      <InputSourcesBar inputSources={inputSources} onDeleteInput={onDeleteInput} />

      <div className="text-[11px] font-bold uppercase tracking-[0.08em] shrink-0" style={{ color: "var(--text-muted)" }}>
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
              className="rounded-lg pl-3 pr-2 py-2 flex flex-wrap items-center gap-2"
              style={{ background: "var(--bg-elevated)", border: "1px solid var(--border)", borderLeft: `3px dashed ${NODE_GROUP_COLORS.transform}` }}
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
                const before = columnStates[index]
                const unknown = open ? [] : unknownColumnsOf(step, before, source)
                const need = needFor(index)
                const notes = unknown.length > 0 || need ? (
                  <>
                    {unknown.length > 0 && <UnknownColumnsNote names={unknown} />}
                    {need && (
                      <p className="m-0 text-[11px] leading-snug" style={{ color: "var(--text-muted)" }}>
                        {need}
                      </p>
                    )}
                  </>
                ) : undefined
                return (
                  <div key={key} role="listitem">
                    <StepCard
                      label={kindLabel(step.kind)}
                      icon={STEP_ICONS[step.kind]}
                      number={displayNumber}
                      summary={summaryParts(step)}
                      change={columnChange(step, before, columnStates[index + 1])}
                      notes={notes}
                      open={open}
                      onToggle={() => setOpenIndex(open ? null : index)}
                      onEscape={() => {
                        setOpenIndex(null)
                        focusDisclosure(index)
                      }}
                      movable
                      onMoveUp={index > firstMovable ? () => move(index, -1) : undefined}
                      onMoveDown={index < steps.length - 1 ? () => move(index, 1) : undefined}
                      onDelete={() => deleteStep(index)}
                      drag={dragFor(index)}
                      badge={badgeFor(index)}
                      highlighted={pointedStep === index}
                      onHoverChange={(hovering) => setPointedStep(hovering ? index : null)}
                      disclosureRef={(el) => {
                        if (el) disclosures.current.set(index, el)
                        else disclosures.current.delete(index)
                      }}
                    >
                      {open && <StepForm
                        step={step}
                        onChange={(next) => updateStep(index, next)}
                        ctx={{
                          columns: columnNames(before),
                          schema: schemaFor(before),
                          inputColumns: source.inputs,
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

          {stepCards.length === 0 && canAdd && (
            <div data-testid="polars-steps-first-run" className="grid gap-2 rounded-lg px-3 py-2.5" style={{ border: "1px dashed var(--border)" }}>
              <p className="m-0 text-[11px] leading-snug" style={{ color: "var(--text-secondary)" }}>
                Steps run top to bottom on {isFrame ? "this node's data" : <code className="font-mono" style={{ color: "var(--text-primary)" }}>{effectiveStart}</code>}. Start with:
              </p>
              <div className="flex flex-wrap gap-1.5">
                {firstKinds.map((kind) => {
                  const info = STEP_CATALOGUE.find((entry) => entry.kind === kind)
                  const Icon = STEP_ICONS[kind]
                  return (
                    <button
                      key={kind}
                      type="button"
                      onClick={() => addStep(kind)}
                      title={info?.description}
                      className="add-row-btn focus-ring inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium"
                      style={{ color: "var(--text-primary)", background: "var(--bg-elevated)", border: "1px solid var(--border)" }}
                    >
                      <Icon size={12} aria-hidden="true" style={{ color: NODE_GROUP_COLORS.transform }} />
                      {info?.label ?? kind}
                    </button>
                  )
                })}
              </div>
            </div>
          )}

          <AddStepMenu onAdd={addStep} disabled={!canAdd} withhold={withheldKinds} buttonRef={(el) => (addButton.current = el)} />

          <GeneratedCodePanel
            start={start}
            code={rendered.code}
            pending={rendered.status === "pending"}
            stale={stale}
            note={note}
            error={panelError}
            errorLine={currentStepLines.length > 0 ? errorLine : null}
            onGoToError={goToError}
            stepLines={currentStepLines}
            activeStep={pointedStep}
            onPointStep={setPointedStep}
            onOpenStep={openFromCode}
            switchEnabled={switchEnabled}
            switchDisabledReason={switchDisabledReason}
            onSwitchToCode={switchToCode}
          />
        </>
      )}
    </div>
  )
}
