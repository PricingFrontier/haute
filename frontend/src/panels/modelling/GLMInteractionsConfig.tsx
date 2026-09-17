import { useMemo, useState } from "react"
import { Plus, Trash2 } from "lucide-react"

import type { OnUpdateConfig } from "../editors/_shared"
import { configField } from "../../utils/configField"
import { roleColumnReasons, type ModellingColumn } from "./featureSelection"
import {
  JOINT_ENCODING_CLASSES,
  addInteraction,
  addSlot,
  columnContext,
  columnIssue,
  duplicateInteractionIndexes,
  filledFactors,
  glmDtypeClass,
  interactionEncodingOptions,
  interactionEntryIssue,
  mainEffectKeys,
  pickSlotColumn,
  removeInteraction,
  removeSlot,
  resolvedSlotSpec,
  setIncludeMain,
  setInteractionEncoding,
  setInteractionField,
  setSlotOverride,
  setTermField,
  simulateInteractionDesign,
  slotFitOptions,
  slotOverride,
  withSplineMode,
  type ColumnContext,
  type InteractionDesign,
  type InteractionSpec,
  type SlotFit,
  type TermSpec,
  type Terms,
} from "./glmTerms"
import { GLM_FIELD_CLASS, GLM_ROW_CLASS, GLM_SELECT_CLASS, MODELLING_INPUT_STYLE, toggleButtonStyle } from "./styles"
import { EncodingControls, TermCard } from "./TermCard"

type Props = {
  config: Record<string, unknown>
  onUpdate: OnUpdateConfig
  columns: ModellingColumn[]
}

const INCLUDE_MAIN_HELP = "Adds a main effect for features that have none; features with a term keep it."
const UNUSED_ENTRY: InteractionSpec = { factors: [], include_main: false }
const CARD_STYLE = { background: "var(--bg-elevated)", border: "1px solid var(--border)" } as const
const SLOT_ROW_CLASS = `${GLM_ROW_CLASS} rounded-lg border bg-[var(--bg-input)] px-2 py-1.5`

/**
 * Keys that stay with an item for its lifetime in the editor. Removing an item
 * through `remove` drops its key, so its neighbours keep their drafts and
 * disclosure state; a length change from outside appends or truncates keys.
 */
function useStableKeys(count: number): { keys: number[]; remove: (index: number) => void } {
  const [state, setState] = useState(() => ({ keys: Array.from({ length: count }, (_, index) => index), next: count }))
  let current = state
  if (state.keys.length !== count) {
    const keys = state.keys.length > count
      ? state.keys.slice(0, count)
      : [...state.keys, ...Array.from({ length: count - state.keys.length }, (_, offset) => state.next + offset)]
    current = { keys, next: state.next + Math.max(0, count - state.keys.length) }
    setState(current)
  }
  return {
    keys: current.keys,
    remove: (index) => setState((previous) => ({ ...previous, keys: previous.keys.filter((_, position) => position !== index) })),
  }
}

function Alert({ children }: { children: React.ReactNode }) {
  return <p role="alert" className="text-[11px]" style={{ color: "var(--danger)" }}>{children}</p>
}

function RemoveIconButton({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button type="button" aria-label={label} className="focus-ring flex h-7 shrink-0 items-center rounded px-1 hover:bg-[var(--danger-soft)]" style={{ color: "var(--text-muted)" }} onClick={onClick}>
      <Trash2 size={12} aria-hidden="true" />
    </button>
  )
}

function sharedTargetEncoding(terms: Terms, column: string): { key: string; spec: TermSpec } | undefined {
  const key = mainEffectKeys(terms, column).find((candidate) => terms[candidate].type === "target_encoding")
  return key === undefined ? undefined : { key, spec: terms[key] }
}

type CardProps = {
  number: number
  index: number
  interaction: InteractionSpec
  analysed: readonly InteractionSpec[]
  stored: readonly unknown[]
  terms: Terms
  context: ColumnContext
  design: InteractionDesign
  duplicate: boolean
  write: (next: InteractionSpec[]) => void
  onRemove: () => void
}

function InteractionCard({ number, index, interaction, analysed, stored, terms, context, design, duplicate, write, onRemove }: CardProps) {
  const slotKeys = useStableKeys(interaction.factors.length)
  const dtypeOf = (name: string) => context.byName.get(name)?.dtype ?? ""
  // Transitions index into the stored list, so malformed neighbours stay put.
  const entries = stored as InteractionSpec[]
  const picked = filledFactors(interaction)
  const joint = interaction.encoding === "target_encoding" || interaction.encoding === "frequency_encoding"
  const anyWithoutMain = picked.some((factor) => mainEffectKeys(terms, factor).length === 0)
  const partnerSpecs = (slot: number): (TermSpec | null)[] =>
    interaction.factors
      .filter((factor, other) => other !== slot && factor !== "")
      .map((factor) => resolvedSlotSpec(terms, factor, dtypeOf(factor), slotOverride(interaction, factor)))
  const encodingOptions = interactionEncodingOptions(analysed, index, terms, dtypeOf)
  const issues = design.cardIssues.get(index) ?? []

  return (
    <div role="group" aria-label={`Interaction ${number}`} className="rounded-lg px-3 py-2" style={CARD_STYLE}>
      <div className="flex min-w-0 items-center gap-2">
        <span className="min-w-0 break-words font-mono text-xs font-semibold" style={{ color: "var(--text-primary)", overflowWrap: "anywhere" }}>
          {picked.length > 0 ? picked.join(" × ") : "New interaction"}
        </span>
        <span className="flex-1" />
        <RemoveIconButton label={`Remove interaction ${number}`} onClick={onRemove} />
      </div>
      {duplicate && <Alert>Duplicate interaction: another card fits these features the same way.</Alert>}
      {picked.length < 2 && <p className="mt-1 text-[11px]" style={{ color: "var(--text-secondary)" }}>Pick at least two features</p>}
      {issues.map((issue) => <Alert key={issue}>{issue}</Alert>)}

      <div className="ml-2 mt-1.5 grid min-w-0 gap-1.5 border-l pl-3" style={{ borderColor: "var(--border)" }}>
        <div className={SLOT_ROW_CLASS} style={{ borderColor: "var(--border)" }}>
          <label className={`${GLM_FIELD_CLASS} w-32`} style={{ color: "var(--text-secondary)" }}>
            <span>Fit type</span>
            <select
              aria-label={`Interaction ${number} encoding`}
              className={GLM_SELECT_CLASS}
              style={MODELLING_INPUT_STYLE}
              value={interaction.encoding ?? "product"}
              onChange={(event) => {
                const next = encodingOptions.find((option) => option.value === event.target.value)
                if (next) write(setInteractionEncoding(entries, index, next.value))
              }}
            >
              {encodingOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
            </select>
          </label>
          {interaction.encoding === "target_encoding" && (
            <EncodingControls
              inline
              label={`Interaction ${number}`}
              spec={{ type: "target_encoding", prior_weight: interaction.prior_weight, n_permutations: interaction.n_permutations }}
              onChangeField={(field, value) => write(setInteractionField(entries, index, field, value))}
            />
          )}
        </div>
        {joint && <p className="text-[11px]" style={{ color: "var(--text-secondary)" }}>Encodes the combination of raw feature values.</p>}
        {interaction.factors.map((factor, slot) => {
          const others = new Set(interaction.factors.filter((_, other) => other !== slot))
          const partners = partnerSpecs(slot)
          const fitsSlot = (column: ModellingColumn) => joint
            ? JOINT_ENCODING_CLASSES.has(glmDtypeClass(column.dtype))
            : slotFitOptions(terms, column.name, column.dtype, partners).length > 0
          const saved = factor === "" ? undefined : context.byName.get(factor)
          const ineligible = factor === "" ? null : columnIssue(factor, context)
          const incompatible = ineligible === null && saved !== undefined && !fitsSlot(saved)
          const override = slotOverride(interaction, factor)
          return (
            <div key={slotKeys.keys[slot] ?? `slot-${slot}`} className={SLOT_ROW_CLASS} style={{ borderColor: "var(--border)" }}>
              <label className={`${GLM_FIELD_CLASS} w-32`} style={{ color: "var(--text-secondary)" }}>
                <span>Feature {slot + 1}</span>
                <select
                  aria-label={`Interaction ${number} feature ${slot + 1}`}
                  aria-invalid={ineligible !== null || incompatible}
                  className={`${GLM_SELECT_CLASS} font-mono`}
                  style={{ ...MODELLING_INPUT_STYLE, borderColor: ineligible !== null || incompatible ? "var(--danger)" : "var(--border)" }}
                  value={factor}
                  onChange={(event) => write(pickSlotColumn(entries, index, slot, event.target.value))}
                >
                  <option value="">Select…</option>
                  {(ineligible !== null || incompatible) && <option value={factor} disabled>{factor} (unavailable)</option>}
                  {context.eligible
                    .filter((column) => !others.has(column.name) && fitsSlot(column))
                    .map((column) => <option key={column.name} value={column.name}>{column.name}</option>)}
                </select>
              </label>
              {ineligible !== null && <Alert>{ineligible}; choose another feature.</Alert>}
              {incompatible && (
                <Alert>
                  {joint
                    ? "Joint encodings need integer, boolean, or categorical features."
                    : "This feature has no fit compatible with the other features in this interaction."}
                </Alert>
              )}
              {factor !== "" && !joint && ineligible === null && saved !== undefined && (
                <TermCard
                  kind="slot"
                  column={factor}
                  dtype={saved.dtype}
                  terms={terms}
                  override={override}
                  partnerSpecs={partners}
                  sharedTargetEncoding={sharedTargetEncoding(terms, factor)}
                  onChangeFit={(fit: SlotFit) => write(setSlotOverride(entries, index, factor, fit === "main" ? null : { type: fit }))}
                  onChangeField={(field, value) => {
                    const next = setTermField(interaction.specs ?? {}, factor, field, value)
                    write(setSlotOverride(entries, index, factor, next[factor]))
                  }}
                  onChangeSplineMode={(mode) => {
                    if (override !== undefined) write(setSlotOverride(entries, index, factor, withSplineMode(override, mode)))
                  }}
                />
              )}
              {interaction.factors.length > 2 && (
                <span className="mt-[18px]">
                  <RemoveIconButton
                    label={`Remove feature ${slot + 1}`}
                    onClick={() => {
                      slotKeys.remove(slot)
                      write(removeSlot(entries, index, slot))
                    }}
                  />
                </span>
              )}
            </div>
          )
        })}
      </div>

      <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
        <button type="button" className="focus-ring rounded px-2 py-0.5 text-[11px] font-medium" style={toggleButtonStyle(false)} onClick={() => write(addSlot(entries, index))}>
          + feature
        </button>
        {anyWithoutMain && (
          <label className="flex flex-wrap items-center gap-1 text-[11px]" style={{ color: "var(--text-secondary)" }}>
            <input
              type="checkbox"
              aria-label="Include main effects"
              checked={interaction.include_main !== false}
              onChange={(event) => write(setIncludeMain(entries, index, event.target.checked))}
              className="accent-purple-500"
            />
            Include main effects
            <span className="basis-full text-[10px] sm:basis-auto" style={{ color: "var(--text-muted)" }}>{INCLUDE_MAIN_HELP}</span>
          </label>
        )}
      </div>
    </div>
  )
}

export function GLMInteractionsConfig({ config, onUpdate, columns }: Props) {
  const terms = configField<Terms>(config, "terms", {})
  const rawInteractions = configField<unknown>(config, "interactions", [])
  const stored = useMemo(() => (Array.isArray(rawInteractions) ? rawInteractions : []), [rawInteractions])
  const roles = useMemo(() => roleColumnReasons(config), [config])
  const context = useMemo(() => columnContext(columns, roles), [columns, roles])
  // Malformed entries become empty placeholders so card indexes stay aligned.
  const analysed = useMemo(
    () => stored.map((entry) => (interactionEntryIssue(entry) === null ? (entry as InteractionSpec) : UNUSED_ENTRY)),
    [stored],
  )
  const design = useMemo(
    () => simulateInteractionDesign(terms, analysed, (name) => context.byName.get(name)?.dtype ?? ""),
    [terms, analysed, context],
  )
  const duplicates = useMemo(() => duplicateInteractionIndexes(analysed), [analysed])
  const cardKeys = useStableKeys(stored.length)
  const write = (next: InteractionSpec[]) => onUpdate("interactions", next)
  const remove = (index: number) => {
    cardKeys.remove(index)
    write(removeInteraction(stored as InteractionSpec[], index))
  }

  return (
    <section aria-labelledby="model-interactions-heading" className="mt-4">
      <div className="flex items-center justify-between">
        <h3 id="model-interactions-heading" className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: "var(--text-muted)" }}>
          Interactions
        </h3>
        <button type="button" className="focus-ring flex items-center gap-1 rounded-md px-2.5 py-1.5 text-[11px] font-semibold" style={toggleButtonStyle(true)} onClick={() => write(addInteraction(stored as InteractionSpec[]))}>
          <Plus size={12} aria-hidden="true" /> Add interaction
        </button>
      </div>

      <div className="mt-2 space-y-1.5">
        {stored.length === 0 && <p className="rounded-lg border border-dashed px-3 py-2 text-[11px]" style={{ color: "var(--text-secondary)", borderColor: "var(--border)" }}>No interactions yet. Add one to combine features.</p>}
        {stored.map((entry, index) => {
          const number = index + 1
          const key = cardKeys.keys[index] ?? `card-${index}`
          const issue = interactionEntryIssue(entry)
          if (issue !== null) {
            return (
              <div key={key} role="group" aria-label={`Interaction ${number}`} className="flex items-start gap-2 rounded-lg px-3 py-2" style={{ background: "var(--danger-soft-subtle)", border: "1px solid var(--danger-border)" }}>
                <div className="min-w-0 flex-1">
                  <p className="text-xs font-semibold" style={{ color: "var(--danger-text-soft)" }}>Interaction {number} cannot be edited</p>
                  <Alert>{issue} Remove it, or fix it in the node's JSON.</Alert>
                </div>
                <RemoveIconButton label={`Remove interaction ${number}`} onClick={() => remove(index)} />
              </div>
            )
          }
          return (
            <InteractionCard
              key={key}
              number={number}
              index={index}
              interaction={entry as InteractionSpec}
              analysed={analysed}
              stored={stored}
              terms={terms}
              context={context}
              design={design}
              duplicate={duplicates.has(index)}
              write={write}
              onRemove={() => remove(index)}
            />
          )
        })}
      </div>
    </section>
  )
}
