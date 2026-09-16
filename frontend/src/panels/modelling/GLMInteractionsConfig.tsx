import { useMemo } from "react"
import { Plus, Trash2 } from "lucide-react"

import type { OnUpdateConfig } from "../editors/_shared"
import { configField } from "../../utils/configField"
import { roleColumns, type ModellingColumn } from "./featureSelection"
import {
  addInteraction,
  addSlot,
  duplicateInteractionIndexes,
  filledFactors,
  nativeTermOf,
  pickSlotColumn,
  removeInteraction,
  removeSlot,
  setIncludeMain,
  setSlotOverride,
  setTermField,
  slotColumnBlockedReason,
  TARGET_ENCODED_SLOT_REASON,
  type InteractionSpec,
  type SlotFit,
  type Terms,
} from "./glmTerms"
import { MODELLING_INPUT_STYLE, toggleButtonStyle } from "./styles"
import { TermCard } from "./TermCard"

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
        <button type="button" className="flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-medium" style={{ color: "var(--chart-above)" }} onClick={() => write(addInteraction(interactions))}>
          <Plus size={10} aria-hidden="true" /> Add interaction
        </button>
      </div>

      <div className="mt-1.5 grid gap-1.5">
        {interactions.map((interaction, index) => {
          const number = index + 1
          const picked = filledFactors(interaction)
          const anyWithoutMain = picked.some((factor) => nativeTermOf(terms, factor) === null)
          return (
            <div key={index} role="group" aria-label={`Interaction ${number}`} className="rounded-lg px-2 py-1.5" style={{ background: "var(--bg-elevated)", border: "1px solid var(--border)" }}>
              <div className="flex items-center gap-1.5">
                <span className="min-w-0 flex-1 truncate font-mono text-[11px] font-semibold" style={{ color: "var(--text-primary)" }}>
                  {picked.length > 0 ? picked.join(" × ") : "New interaction"}
                </span>
                {duplicates.has(index) && (
                  <span className="text-[10px]" style={{ color: "var(--danger)" }}>Duplicate interaction</span>
                )}
                {picked.length < 2 && (
                  <span className="text-[10px]" style={{ color: "var(--text-muted)" }}>Pick at least two features</span>
                )}
                <button type="button" aria-label={`Remove interaction ${number}`} className="rounded p-1 hover:bg-[var(--danger-soft)]" style={{ color: "var(--text-muted)" }} onClick={() => write(removeInteraction(interactions, index))}>
                  <Trash2 size={12} aria-hidden="true" />
                </button>
              </div>

              <div className="mt-1.5 grid gap-1">
                {interaction.factors.map((factor, slot) => {
                  const others = new Set(interaction.factors.filter((_, i) => i !== slot))
                  const blockedNames = eligible
                    .filter((column) => slotColumnBlockedReason(nativeTermOf(terms, column.name)) !== null)
                    .map((column) => column.name)
                  const mainSpec = factor ? nativeTermOf(terms, factor) : null
                  return (
                    <div key={slot} className="flex flex-wrap items-center gap-1.5 rounded-lg px-2 py-1" style={{ background: "var(--bg-input)", border: "1px solid var(--border)" }}>
                      <select
                        aria-label={`Interaction ${number} feature ${slot + 1}`}
                        className="rounded px-1.5 py-1 font-mono text-[10px]"
                        style={{ ...MODELLING_INPUT_STYLE, minWidth: "120px" }}
                        title={blockedNames.length > 0 ? `${TARGET_ENCODED_SLOT_REASON}: ${blockedNames.join(", ")}` : undefined}
                        value={factor}
                        onChange={(event) => write(pickSlotColumn(interactions, index, slot, event.target.value))}
                      >
                        <option value="">Select…</option>
                        {eligible
                          .filter((column) => !others.has(column.name) && !blockedNames.includes(column.name))
                          .map((column) => (
                            <option key={column.name} value={column.name}>{column.name}</option>
                          ))}
                      </select>
                      {factor && (
                        <TermCard
                          kind="slot"
                          column={factor}
                          dtype={dtypeOf(factor)}
                          mainSpec={mainSpec}
                          override={interaction.specs?.[factor]}
                          onChangeFit={(fit: SlotFit) =>
                            write(setSlotOverride(interactions, index, factor, fit === "main" ? null : { type: fit }))
                          }
                          onChangeField={(field, value) => {
                            const specs = setTermField(interaction.specs ?? {}, factor, field, value)
                            write(setSlotOverride(interactions, index, factor, specs[factor]))
                          }}
                        />
                      )}
                      <span className="flex-1" />
                      {interaction.factors.length > 2 && (
                        <button type="button" aria-label={`Remove feature ${slot + 1}`} className="rounded p-1 hover:bg-[var(--danger-soft)]" style={{ color: "var(--text-muted)" }} onClick={() => write(removeSlot(interactions, index, slot))}>
                          <Trash2 size={11} aria-hidden="true" />
                        </button>
                      )}
                    </div>
                  )
                })}
              </div>

              <div className="mt-1.5 flex items-center gap-3">
                <button type="button" className="rounded px-2 py-0.5 text-[10px] font-medium" style={toggleButtonStyle(false)} onClick={() => write(addSlot(interactions, index))}>
                  + feature
                </button>
                {anyWithoutMain && (
                  <label className="flex items-center gap-1 text-[10px]" style={{ color: "var(--text-muted)" }} title={INCLUDE_MAIN_HELP}>
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
