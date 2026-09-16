import { useMemo } from "react"
import { Plus, Trash2 } from "lucide-react"

import type { OnUpdateConfig } from "../editors/_shared"
import { configField } from "../../utils/configField"
import { isNumericDtype } from "../../utils/polarsDtypes"
import { roleColumns, type ModellingColumn } from "./featureSelection"
import {
  addInteraction,
  addSlot,
  duplicateInteractionIndexes,
  effectiveSlotSpec,
  filledFactors,
  interactionEncodingOptions,
  nativeTermOf,
  pickSlotColumn,
  removeInteraction,
  removeSlot,
  setIncludeMain,
  setInteractionEncoding,
  setInteractionField,
  setSlotOverride,
  setTermField,
  specType,
  slotFitOptions,
  type InteractionSpec,
  type SlotFit,
  type Terms,
} from "./glmTerms"
import { GLM_FIELD_CLASS, GLM_ROW_CLASS, GLM_SELECT_CLASS, MODELLING_INPUT_STYLE, toggleButtonStyle } from "./styles"
import { EncodingControls, TermCard } from "./TermCard"

type Props = {
  config: Record<string, unknown>
  onUpdate: OnUpdateConfig
  columns: ModellingColumn[]
}

const INCLUDE_MAIN_HELP = "Adds a main effect for factors that have none; factors with a term already keep it"

export function GLMInteractionsConfig({ config, onUpdate, columns }: Props) {
  const terms = configField<Terms>(config, "terms", {})
  const interactions = configField<InteractionSpec[]>(config, "interactions", [])
  const eligible = useMemo(() => {
    const roles = roleColumns(config)
    return columns.filter((column) => !roles.has(column.name))
  }, [columns, config])
  const dtypeOf = (name: string) => eligible.find((column) => column.name === name)?.dtype ?? ""
  const duplicates = useMemo(() => duplicateInteractionIndexes(interactions), [interactions])
  const write = (next: InteractionSpec[]) => onUpdate("interactions", next)

  return (
    <section aria-labelledby="model-interactions-heading" className="mt-4">
      <div className="flex items-center justify-between">
        <h3 id="model-interactions-heading" className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: "var(--text-muted)" }}>
          Interactions
        </h3>
        <button type="button" className="focus-ring flex items-center gap-1 rounded-md px-2.5 py-1.5 text-[11px] font-semibold" style={toggleButtonStyle(true)} onClick={() => write(addInteraction(interactions))}>
          <Plus size={12} aria-hidden="true" /> Add interaction
        </button>
      </div>

      <div className="mt-2 space-y-1.5">
        {interactions.length === 0 && <p className="rounded-lg border border-dashed px-3 py-2 text-[11px]" style={{ color: "var(--text-secondary)", borderColor: "var(--border)" }}>No interactions yet. Add one to combine features.</p>}
        {interactions.map((interaction, index) => {
          const number = index + 1
          const picked = filledFactors(interaction)
          const joint = interaction.encoding === "target_encoding" || interaction.encoding === "frequency_encoding"
          const anyWithoutMain = picked.some((factor) => nativeTermOf(terms, factor) === null)
          const title = picked.length > 0 ? picked.join(" × ") : "New interaction"
          const productSpecFor = (factor: string) => effectiveSlotSpec(
            dtypeOf(factor), nativeTermOf(terms, factor), interaction.specs?.[factor],
          )
          const otherSpecsFor = (slot: number) => interaction.factors
            .filter((factor, candidateSlot) => candidateSlot !== slot && factor !== "")
            .map(productSpecFor)
          const sharedTargetEncodingFor = (factor: string) => Object.entries(terms).find(([key, spec]) =>
            specType(spec) === "target_encoding" && (spec.variable ?? key) === factor,
          )
          return (
            <div key={index} role="group" aria-label={`Interaction ${number}`} className="rounded-lg px-3 py-2" style={{ background: "var(--bg-elevated)", border: "1px solid var(--border)" }}>
              <div className="flex min-w-0 items-center gap-2">
                <span className="min-w-0 break-words font-mono text-xs font-semibold" style={{ color: "var(--text-primary)", overflowWrap: "anywhere" }}>{title}</span>
                <span className="flex-1" />
                <button type="button" aria-label={`Remove interaction ${number}`} className="focus-ring flex h-7 shrink-0 items-center rounded px-1 hover:bg-[var(--danger-soft)]" style={{ color: "var(--text-muted)" }} onClick={() => write(removeInteraction(interactions, index))}>
                  <Trash2 size={12} aria-hidden="true" />
                </button>
              </div>
              {duplicates.has(index) && <p className="mt-1 text-[11px]" style={{ color: "var(--danger)" }}>Duplicate interaction</p>}
              {picked.length < 2 && <p className="mt-1 text-[11px]" style={{ color: "var(--text-secondary)" }}>Pick at least two features</p>}

              <div className="ml-2 mt-1.5 grid min-w-0 gap-1.5 border-l pl-3" style={{ borderColor: "var(--border)" }}>
                <div className={`${GLM_ROW_CLASS} rounded-lg border bg-[var(--bg-input)] px-2 py-1.5`} style={{ borderColor: "var(--border)" }}>
                  <label className={`${GLM_FIELD_CLASS} w-32`} style={{ color: "var(--text-secondary)" }}>
                    <span>Fit type</span>
                    <select
                      aria-label={`Interaction ${number} encoding`}
                      className={GLM_SELECT_CLASS}
                      style={MODELLING_INPUT_STYLE}
                      value={interaction.encoding ?? "product"}
                      onChange={(event) => write(setInteractionEncoding(interactions, index, event.target.value as "product" | "target_encoding" | "frequency_encoding"))}
                    >
                      {interactionEncodingOptions(interactions, index, terms, eligible).map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
                    </select>
                  </label>
                  {interaction.encoding === "target_encoding" && (
                    <EncodingControls
                      inline
                      label={`Interaction ${number}`}
                      spec={{ type: "target_encoding", prior_weight: interaction.prior_weight, n_permutations: interaction.n_permutations }}
                      onChangeField={(field, value) => write(setInteractionField(interactions, index, field, value))}
                    />
                  )}
                </div>
                {joint && <p className="text-[11px]" style={{ color: "var(--text-secondary)" }}>Encodes the combination of raw feature values.</p>}
                {interaction.factors.map((factor, slot) => {
                  const others = new Set(interaction.factors.filter((_, i) => i !== slot))
                  const mainSpec = factor ? nativeTermOf(terms, factor) : null
                  const numericUnavailable = joint && factor !== "" && isNumericDtype(dtypeOf(factor))
                  const otherSpecs = otherSpecsFor(slot)
                  const productUnavailable = !joint && factor !== "" && slotFitOptions(dtypeOf(factor), mainSpec, otherSpecs).length === 0
                  const blockedReason = joint
                    ? numericUnavailable ? "Joint encodings require categorical features" : null
                    : productUnavailable ? "This feature has no compatible fit with the other selected features" : null
                  return (
                    <div key={slot} className={`${GLM_ROW_CLASS} rounded-lg border bg-[var(--bg-input)] px-2 py-1.5`} style={{ borderColor: "var(--border)" }}>
                      <label className={`${GLM_FIELD_CLASS} w-32`} style={{ color: "var(--text-secondary)" }}>
                        <span>Feature {slot + 1}</span>
                        <select
                          aria-label={`Interaction ${number} feature ${slot + 1}`}
                          className={`${GLM_SELECT_CLASS} font-mono`}
                          style={MODELLING_INPUT_STYLE}
                          value={factor}
                          onChange={(event) => write(pickSlotColumn(interactions, index, slot, event.target.value))}
                        >
                          <option value="">Select…</option>
                          {eligible
                            .filter((column) => !others.has(column.name) && (!joint || !isNumericDtype(column.dtype) || column.name === factor))
                            .map((column) => {
                              const unavailable = (!joint && slotFitOptions(column.dtype, nativeTermOf(terms, column.name), otherSpecs).length === 0) || (joint && isNumericDtype(column.dtype))
                              if (unavailable && column.name !== factor) return null
                              return (
                                <option key={column.name} value={column.name} disabled={unavailable}>
                                  {column.name}{unavailable ? " (unavailable)" : ""}
                                </option>
                              )
                            })}
                        </select>
                      </label>
                      {blockedReason && (
                        <span role="alert" className="basis-full text-[11px]" style={{ color: "var(--danger)" }}>
                          {blockedReason}
                        </span>
                      )}
                      {factor && !joint && (
                        <TermCard
                          kind="slot"
                          column={factor}
                          dtype={dtypeOf(factor)}
                          mainSpec={mainSpec}
                          override={interaction.specs?.[factor]}
                          otherSpecs={otherSpecs}
                          sharedTargetEncoding={(() => {
                            const shared = sharedTargetEncodingFor(factor)
                            return shared ? { key: shared[0], spec: shared[1] } : undefined
                          })()}
                          inline
                          onChangeFit={(fit: SlotFit) =>
                            write(setSlotOverride(interactions, index, factor, fit === "main" ? null : { type: fit }))
                          }
                          onChangeField={(field, value) => {
                            const specs = setTermField(interaction.specs ?? {}, factor, field, value)
                            write(setSlotOverride(interactions, index, factor, specs[factor]))
                          }}
                        />
                      )}
                      {interaction.factors.length > 2 && (
                        <button type="button" aria-label={`Remove feature ${slot + 1}`} className="focus-ring mt-[18px] flex h-7 shrink-0 items-center rounded px-1 hover:bg-[var(--danger-soft)]" style={{ color: "var(--text-muted)" }} onClick={() => write(removeSlot(interactions, index, slot))}>
                          <Trash2 size={12} aria-hidden="true" />
                        </button>
                      )}
                    </div>
                  )
                })}
              </div>

              <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                <button type="button" className="focus-ring rounded px-2 py-0.5 text-[11px] font-medium" style={toggleButtonStyle(false)} onClick={() => write(addSlot(interactions, index))}>
                  + feature
                </button>
                {anyWithoutMain && (
                  <label className="flex items-center gap-1 text-[11px]" style={{ color: "var(--text-secondary)" }} title={INCLUDE_MAIN_HELP}>
                    <input
                      type="checkbox"
                      aria-label="Include main effects"
                      checked={interaction.include_main}
                      onChange={(event) => write(setIncludeMain(interactions, index, event.target.checked))}
                      className="accent-purple-500"
                    />
                    Include main effects
                  </label>
                )}
              </div>
            </div>
          )
        })}
      </div>
    </section>
  )
}
