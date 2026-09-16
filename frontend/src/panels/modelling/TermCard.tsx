import { useState } from "react"
import { Trash2 } from "lucide-react"

import { NODE_GROUP_COLORS } from "../../theme/colors"
import { withAlpha } from "../../utils/color"
import { safeParseFloat, safeParseInt } from "../../utils/configField"
import {
  EXPRESSION_GRAMMAR,
  MONOTONE_SLOT_REASON,
  NATIVE_TERM_TYPES,
  effectiveSlotSpec,
  slotFitOptions,
  slotNeedsExplicitFit,
  type EditResult,
  type NativeTermType,
  type SlotFit,
  type TermSpec,
} from "./glmTerms"
import { MODELLING_INPUT_STYLE } from "./styles"

type Direction = {
  value: "decreasing" | "increasing" | undefined
  label: string
  glyph: string
  color: string
  activeBackground: string
}

/** Mirrors `CommonFeatureConfig`'s `MONOTONIC_DIRECTIONS` styling, over GLM's string values. */
const DIRECTIONS: readonly Direction[] = [
  { value: "decreasing", label: "decreasing", glyph: "↓", color: "var(--danger)", activeBackground: "var(--danger-soft)" },
  { value: undefined, label: "no constraint", glyph: "−", color: "var(--warning-strong)", activeBackground: "var(--warning-soft)" },
  { value: "increasing", label: "increasing", glyph: "↑", color: NODE_GROUP_COLORS.data, activeBackground: withAlpha(NODE_GROUP_COLORS.data, 0.1) },
]

const MONOTONIC_TYPES = new Set(["linear", "bs", "expression"])

export type TermCardProps =
  | {
      kind: "native"
      column: string
      spec: TermSpec
      onChangeType: (nextType: NativeTermType) => void
      onChangeField: (field: string, value: unknown) => void
      onRemove: () => void
    }
  | {
      kind: "expression"
      termKey: string
      spec: TermSpec
      onRename: (nextKey: string) => EditResult
      onChangeExpr: (expr: string) => EditResult
      onChangeField: (field: string, value: unknown) => void
      onRemove: () => void
    }
  | {
      kind: "slot"
      column: string
      dtype: string
      mainSpec: TermSpec | null
      override: TermSpec | undefined
      onChangeFit: (fit: SlotFit) => void
      onChangeField: (field: string, value: unknown) => void
    }

const CARD_STYLE = { background: "var(--bg-input)", border: "1px solid var(--border)" } as const
const NUMBER_CLASS = "w-12 rounded px-1 py-0.5 text-center font-mono text-[10px]"
const SELECT_CLASS = "rounded px-1.5 py-0.5 font-mono text-[10px]"

function NumberField({
  label,
  value,
  min,
  step,
  integer,
  placeholder,
  fallback,
  onChange,
}: {
  label: string
  value: number | undefined
  min: number
  step: number | "any"
  integer: boolean
  placeholder?: string
  fallback?: number
  onChange: (value: number | undefined) => void
}) {
  return (
    <input
      type="number"
      aria-label={label}
      className={NUMBER_CLASS}
      style={MODELLING_INPUT_STYLE}
      value={value ?? fallback ?? ""}
      placeholder={placeholder}
      min={min}
      step={step}
      onChange={(event) => {
        const raw = event.target.value
        if (raw === "") onChange(undefined)
        else onChange(integer ? safeParseInt(raw, min) : safeParseFloat(raw, fallback ?? min))
      }}
    />
  )
}

function ParameterFields({
  label,
  spec,
  onChangeField,
}: {
  label: string
  spec: TermSpec
  onChangeField: (field: string, value: unknown) => void
}) {
  const type = spec.type
  return (
    <>
      {(type === "bs" || type === "ns" || type === "ms") && (
        <NumberField label={`${label} df`} value={spec.df as number | undefined} min={2} step={1} integer placeholder="df" onChange={(v) => onChangeField("df", v)} />
      )}
      {(type === "bs" || type === "ms") && (
        <NumberField label={`${label} degree`} value={spec.degree as number | undefined} min={1} step={1} integer placeholder="deg" onChange={(v) => onChangeField("degree", v)} />
      )}
      {type === "target_encoding" && (
        <NumberField label={`${label} prior weight`} value={spec.prior_weight as number | undefined} min={0} step={0.5} integer={false} fallback={1} onChange={(v) => onChangeField("prior_weight", v)} />
      )}
    </>
  )
}

function MonotonicityArrows({
  label,
  spec,
  onChangeField,
}: {
  label: string
  spec: TermSpec
  onChangeField: (field: string, value: unknown) => void
}) {
  const type = spec.type
  if (!MONOTONIC_TYPES.has(type) && type !== "ms") return null
  const directions = type === "ms" ? DIRECTIONS.filter((d) => d.value !== undefined) : DIRECTIONS
  const current = spec.monotonicity as string | undefined
  return (
    <div role="group" aria-label={`${label} monotonicity`} className="flex items-center gap-1">
      {directions.map((direction) => {
        const active = current === direction.value
        return (
          <button
            type="button"
            key={direction.label}
            aria-label={`${label}: ${direction.label}`}
            aria-pressed={active}
            className="flex h-6 w-6 items-center justify-center rounded-lg text-xs font-semibold transition-[filter] hover:brightness-125"
            style={{
              background: active ? direction.activeBackground : "var(--bg-input)",
              border: `1px solid ${active ? direction.color : "var(--border)"}`,
              color: direction.color,
            }}
            title={`${label}: ${direction.label}`}
            onClick={() => onChangeField("monotonicity", direction.value)}
          >
            <span aria-hidden="true">{direction.glyph}</span>
          </button>
        )
      })}
    </div>
  )
}

function RemoveButton({ label, onRemove }: { label: string; onRemove: () => void }) {
  return (
    <button type="button" aria-label={label} className="rounded p-1 hover:bg-[var(--danger-soft)]" style={{ color: "var(--text-muted)" }} onClick={onRemove}>
      <Trash2 size={12} aria-hidden="true" />
    </button>
  )
}

function CommittedText({
  label,
  value,
  title,
  mono,
  invalid,
  onCommit,
}: {
  label: string
  value: string
  title?: string
  mono?: boolean
  invalid?: boolean
  /** Commits on blur; returns whether the edit was accepted. A refused edit
   *  keeps the draft on screen so the user can correct it in place. */
  onCommit: (next: string) => boolean
}) {
  // Raw edit buffer; null = not editing, render the committed value.
  const [draft, setDraft] = useState<string | null>(null)
  // React's adjust-state-on-render pattern, as in `CommittedTextField`: drop a
  // stale draft the moment the committed value changes under an open edit.
  const [lastValue, setLastValue] = useState(value)
  if (lastValue !== value) {
    setLastValue(value)
    setDraft(null)
  }
  return (
    <input
      type="text"
      aria-label={label}
      title={title}
      className={`min-w-0 rounded px-1.5 py-0.5 text-[10px] ${mono ? "font-mono" : ""}`}
      style={{ ...MODELLING_INPUT_STYLE, borderColor: invalid ? "var(--danger)" : undefined }}
      value={draft ?? value}
      spellCheck={false}
      onChange={(event) => setDraft(event.target.value)}
      onBlur={() => {
        if (draft === null || draft === value) return
        if (onCommit(draft)) setDraft(null)
      }}
    />
  )
}

type RefusedField = "name" | "expr"

function ExpressionCard({
  termKey,
  spec,
  onRename,
  onChangeExpr,
  onChangeField,
  onRemove,
}: Extract<TermCardProps, { kind: "expression" }>) {
  const [refusal, setRefusal] = useState<{ field: RefusedField; reason: string } | null>(null)
  const commit = (field: RefusedField, edit: (next: string) => EditResult) => (next: string) => {
    const result = edit(next)
    setRefusal(result.ok ? null : { field, reason: result.reason })
    return result.ok
  }
  return (
    <div className="flex flex-col gap-0.5 rounded-lg px-2 py-1" style={CARD_STYLE}>
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="rounded px-1.5 py-0.5 font-mono text-[10px]" style={{ background: "var(--chrome-hover)", color: "var(--text-secondary)" }}>
          Expression
        </span>
        <CommittedText label={`${termKey} name`} value={termKey} mono invalid={refusal?.field === "name"} onCommit={commit("name", onRename)} />
        <CommittedText
          label={`${termKey} expression`}
          value={String(spec.expr ?? "")}
          title={EXPRESSION_GRAMMAR}
          mono
          invalid={refusal?.field === "expr"}
          onCommit={commit("expr", onChangeExpr)}
        />
        <MonotonicityArrows label={termKey} spec={spec} onChangeField={onChangeField} />
        <span className="flex-1" />
        <RemoveButton label={`Remove ${termKey} term`} onRemove={onRemove} />
      </div>
      {refusal && (
        <span role="alert" className="text-[10px]" style={{ color: "var(--danger)" }}>
          {refusal.reason}
        </span>
      )}
    </div>
  )
}

export function TermCard(props: TermCardProps) {
  if (props.kind === "native") {
    const { column, spec, onChangeType, onChangeField, onRemove } = props
    return (
      <div className="flex flex-wrap items-center gap-1.5 rounded-lg px-2 py-1" style={CARD_STYLE}>
        <select
          aria-label={`${column} term type`}
          className={SELECT_CLASS}
          style={{ ...MODELLING_INPUT_STYLE, minWidth: "110px" }}
          value={spec.type}
          onChange={(event) => onChangeType(event.target.value as NativeTermType)}
        >
          {NATIVE_TERM_TYPES.map((type) => (
            <option key={type.value} value={type.value}>{type.label}</option>
          ))}
        </select>
        <ParameterFields label={column} spec={spec} onChangeField={onChangeField} />
        <MonotonicityArrows label={column} spec={spec} onChangeField={onChangeField} />
        <span className="flex-1" />
        <RemoveButton label={`Remove ${column} term`} onRemove={onRemove} />
      </div>
    )
  }
  if (props.kind === "expression") {
    return <ExpressionCard {...props} />
  }
  const { column, dtype, mainSpec, override, onChangeFit, onChangeField } = props
  const options = slotFitOptions(dtype, mainSpec)
  const needsExplicit = override === undefined && slotNeedsExplicitFit(mainSpec)
  const current: SlotFit | "" = override ? (override.type as SlotFit) : needsExplicit ? "" : "main"
  const effective = effectiveSlotSpec(dtype, mainSpec, override)
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <select
        aria-label={`${column} fit in interaction`}
        className={SELECT_CLASS}
        style={{ ...MODELLING_INPUT_STYLE, minWidth: "120px", borderColor: needsExplicit ? "var(--danger)" : undefined }}
        value={current}
        onChange={(event) => onChangeFit(event.target.value as SlotFit)}
      >
        {needsExplicit && <option value="">Choose a fit…</option>}
        {options.map((option) => (
          <option key={option.value} value={option.value} disabled={Boolean(option.disabledReason)} title={option.disabledReason}>
            {option.label}
          </option>
        ))}
      </select>
      {override && <ParameterFields label={column} spec={override} onChangeField={onChangeField} />}
      {!override && !needsExplicit && (
        <span className="font-mono text-[10px]" style={{ color: "var(--text-muted)" }} title="Effective fit">
          {effective.type}
        </span>
      )}
      {needsExplicit && (
        <span className="text-[10px]" style={{ color: "var(--danger)" }}>{MONOTONE_SLOT_REASON}</span>
      )}
    </div>
  )
}
