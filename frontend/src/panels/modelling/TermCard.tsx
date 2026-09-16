import { useId, useState, type ReactNode } from "react"
import { ChevronRight, Trash2 } from "lucide-react"

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
  specType,
  type EditResult,
  type AdditionalTermType,
  type NativeTermType,
  type SlotFit,
  type TermSpec,
} from "./glmTerms"
import {
  GLM_FIELD_CLASS as FIELD_CLASS,
  GLM_ROW_CLASS as ROW_CLASS,
  GLM_SELECT_CLASS as SELECT_CLASS,
  MODELLING_INPUT_STYLE,
} from "./styles"

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
      typeOptions?: readonly { value: NativeTermType; label: string; disabled: boolean }[]
      onChangeType: (nextType: NativeTermType) => void
      onChangeField: (field: string, value: unknown) => void
      onRemove: () => void
    }
  | {
      kind: "additional"
      termKey: string
      column: string
      dtype: string
      spec: TermSpec
      typeOptions: readonly { value: AdditionalTermType; label: string; disabled: boolean }[]
      onChangeType: (nextType: AdditionalTermType) => EditResult
      onRename: (nextKey: string) => EditResult
      onChangeExpr: (expr: string) => EditResult
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
      otherSpecs?: readonly TermSpec[]
      sharedTargetEncoding?: { key: string; spec: TermSpec }
      inline?: boolean
      onChangeFit: (fit: SlotFit) => void
      onChangeField: (field: string, value: unknown) => void
    }

const CARD_STYLE = { background: "var(--bg-input)", border: "1px solid var(--border)" } as const
const NUMBER_CLASS = "h-7 w-12 min-w-0 rounded px-1.5 py-1 font-mono text-xs"

function Field({ label, children, className = "" }: { label: string; children: ReactNode; className?: string }) {
  return <label className={`${FIELD_CLASS} ${className}`}><span>{label}</span>{children}</label>
}

function AdvancedDisclosure({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false)
  const panelId = useId()
  return (
    <>
      <button type="button" aria-expanded={open} aria-controls={panelId} className="focus-ring flex h-7 shrink-0 items-center gap-0.5 rounded text-[11px]" style={{ color: "var(--text-secondary)" }} onClick={() => setOpen(!open)}>
        <ChevronRight size={12} className={open ? "rotate-90" : ""} aria-hidden="true" />
        Advanced
      </button>
      <div id={panelId} hidden={!open} className="order-last grid min-w-0 basis-full grid-cols-[repeat(auto-fit,minmax(min(100%,8rem),1fr))] gap-2 border-t pt-2" style={{ borderColor: "var(--border)" }}>{children}</div>
    </>
  )
}

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
  allowLevels = false,
  region = "main",
}: {
  label: string
  spec: TermSpec
  onChangeField: (field: string, value: unknown) => void
  allowLevels?: boolean
  region?: "main" | "advanced"
}) {
  const type = spec.type
  const spline = type === "bs" || type === "ns" || type === "ms"
  const fixed = spec.df !== undefined || spec.knots !== undefined
  const dfDefault = Math.max(5, Number(spec.degree ?? 1) + 1)
  return (
    <>
      {region === "main" && spline && (
        <Field label="df mode" className="w-[4.5rem]">
          <select aria-label={`${label} df mode`} title="Degrees of freedom" className={SELECT_CLASS} style={MODELLING_INPUT_STYLE} value={fixed ? "fixed" : "auto"} onChange={(event) => onChangeField("df", event.target.value === "auto" ? undefined : dfDefault)}>
            <option value="auto">Auto</option>
            <option value="fixed">Fixed</option>
          </select>
        </Field>
      )}
      {region === "main" && (spline && spec.knots !== undefined ? (
        <div className={FIELD_CLASS}><span>df</span><span className="flex h-7 items-center text-xs" style={{ color: "var(--text-muted)" }}>Custom knots</span></div>
      ) : spline && fixed ? (
        <Field label="df"><NumberField label={`${label} df`} value={spec.df as number | undefined} min={2} step={1} integer placeholder="df" onChange={(value) => onChangeField("df", value)} /></Field>
      ) : null)}
      {region === "main" && (type === "bs" || type === "ms") && (
        <Field label="Degree"><NumberField label={`${label} degree`} value={spec.degree as number | undefined} min={1} step={1} integer placeholder="deg" onChange={(v) => onChangeField("degree", v)} /></Field>
      )}
      {region === "advanced" && spline && (
        <AdvancedDisclosure>
          {!fixed && (
            <LabeledNumberField
              label="Basis size (k)"
              inputLabel={`${label} k`}
              value={spec.k as number | undefined}
              min={1}
              fallback={10}
              onChange={(value) => onChangeField("k", value)}
            />
          )}
          {fixed && (
            <JsonArrayField
              label="Interior knots"
              inputLabel={`${label} knots`}
              value={spec.knots}
              placeholder="[1, 2, 3]"
              help="Finite, strictly increasing numbers."
              validate={validateKnots}
              onChange={(value) => onChangeField("knots", value)}
            />
          )}
          <JsonArrayField
            label="Boundary knots"
            inputLabel={`${label} boundary knots`}
            value={spec.boundary_knots}
            placeholder="[0, 10]"
            help="Exactly two finite, increasing numbers."
            validate={validateBoundaryKnots}
            onChange={(value) => onChangeField("boundary_knots", value)}
          />
        </AdvancedDisclosure>
      )}
      {region === "main" && type === "target_encoding" && (
        <EncodingControls label={label} spec={spec} onChangeField={onChangeField} inline />
      )}
      {region === "advanced" && type === "categorical" && allowLevels && (
        <AdvancedDisclosure>
          <JsonArrayField
            label="Levels"
            inputLabel={`${label} levels`}
            value={spec.levels}
            placeholder='["basic", "premium"]'
            help="Unique strings. Quote numeric category labels."
            validate={validateLevels}
            onChange={(value) => onChangeField("levels", value)}
          />
        </AdvancedDisclosure>
      )}
    </>
  )
}

function strictlyIncreasing(values: unknown[], exact?: number): string | null {
  if ((exact !== undefined && values.length !== exact) || (exact === undefined && values.length === 0)) {
    return exact ? `Must contain exactly ${exact} values` : "Must not be empty"
  }
  if (!values.every((value) => typeof value === "number" && Number.isFinite(value))) {
    return "Values must be finite numbers"
  }
  return values.every((value, index) => index === 0 || (value as number) > (values[index - 1] as number))
    ? null
    : "Values must be strictly increasing"
}
const validateKnots = (values: unknown[]) => strictlyIncreasing(values)
const validateBoundaryKnots = (values: unknown[]) => strictlyIncreasing(values, 2)
const validateLevels = (values: unknown[]) => {
  if (values.length === 0) return "Must not be empty"
  if (!values.every((value) => typeof value === "string")) {
    return "Levels must be strings"
  }
  return new Set(values.map((value) => `${typeof value}:${value}`)).size === values.length ? null : "Levels must be unique"
}

function LabeledNumberField({
  label,
  inputLabel,
  value,
  min,
  fallback,
  onChange,
}: {
  label: string
  inputLabel: string
  value: number | undefined
  min: number
  fallback?: number
  onChange: (value: number | undefined) => void
}) {
  return (
    <Field label={label}>
      <NumberField label={inputLabel} value={value} min={min} step={1} integer fallback={fallback} onChange={onChange} />
    </Field>
  )
}

function JsonArrayField({
  label,
  inputLabel,
  value,
  placeholder,
  help,
  validate,
  onChange,
}: {
  label: string
  inputLabel: string
  value: unknown
  placeholder: string
  help: string
  validate: (values: unknown[]) => string | null
  onChange: (values: unknown[] | undefined) => void
}) {
  const committed = value === undefined ? "" : JSON.stringify(value)
  const [draft, setDraft] = useState(committed)
  const [previous, setPrevious] = useState(committed)
  const [error, setError] = useState<string | null>(null)
  if (previous !== committed) {
    setPrevious(committed)
    setDraft(committed)
    setError(null)
  }
  return (
    <Field label={label}>
      <input
        aria-label={inputLabel}
        aria-invalid={error !== null}
        className="h-7 w-full min-w-0 rounded px-1.5 py-1 font-mono text-xs"
        style={{ ...MODELLING_INPUT_STYLE, borderColor: error ? "var(--danger)" : "var(--border)" }}
        value={draft}
        placeholder={placeholder}
        onChange={(event) => setDraft(event.target.value)}
        onBlur={() => {
          if (draft.trim() === "") {
            setError(null)
            onChange(undefined)
            return
          }
          try {
            const parsed: unknown = JSON.parse(draft)
            if (!Array.isArray(parsed)) throw new Error("Must be a JSON array")
            const reason = validate(parsed)
            if (reason) {
              setError(reason)
              return
            }
            setError(null)
            onChange(parsed)
          } catch (err) {
            setError((err as Error).message)
          }
        }}
      />
      <span className="text-[10px]">{help}</span>
      {error && <span role="alert" className="text-[10px]" style={{ color: "var(--danger)" }}>{error}</span>}
    </Field>
  )
}

export function EncodingControls({
  label,
  spec,
  onChangeField,
  inline = false,
}: {
  label: string
  spec: TermSpec
  onChangeField: (field: "prior_weight" | "n_permutations", value: number | undefined) => void
  inline?: boolean
}) {
  const priorWeight = typeof spec.prior_weight === "number" ? spec.prior_weight : undefined
  const fixed = priorWeight !== undefined
  return (
    <div className={inline ? "contents" : ROW_CLASS}>
      <Field label="Prior weight">
        <select aria-label={`${label} prior weight mode`} className={SELECT_CLASS} style={MODELLING_INPUT_STYLE} value={fixed ? "fixed" : "auto"} onChange={(event) => onChangeField("prior_weight", event.target.value === "auto" ? undefined : 1)}>
          <option value="auto">Auto</option>
          <option value="fixed">Fixed</option>
        </select>
      </Field>
      {fixed && (
        <Field label="Prior weight"><NumberField label={`${label} prior weight`} value={priorWeight} min={0} step={0.5} integer={false} onChange={(value) => onChangeField("prior_weight", value)} /></Field>
      )}
      <AdvancedDisclosure>
        <LabeledNumberField
          label="Permutations"
          inputLabel={`${label} n permutations`}
          value={spec.n_permutations as number | undefined}
          min={1}
          fallback={4}
          onChange={(value) => onChangeField("n_permutations", value)}
        />
      </AdvancedDisclosure>
    </div>
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
    <div className={FIELD_CLASS}>
      <span>Monotonicity</span>
      <div role="group" aria-label={`${label} monotonicity`} className="flex h-7 items-center gap-1">
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
    </div>
  )
}

function RemoveButton({ label, onRemove }: { label: string; onRemove: () => void }) {
  return (
    <button type="button" aria-label={label} className="focus-ring mt-[18px] flex h-7 shrink-0 items-center rounded px-1 hover:bg-[var(--danger-soft)]" style={{ color: "var(--text-muted)" }} onClick={onRemove}>
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
      className={`h-7 w-full min-w-0 rounded px-1.5 py-1 text-xs ${mono ? "font-mono" : ""}`}
      style={{ ...MODELLING_INPUT_STYLE, borderColor: invalid ? "var(--danger)" : "var(--border)" }}
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
    <div className="rounded-lg px-2 py-1.5" style={CARD_STYLE}>
      <div className="flex items-start gap-1.5">
        <div className={`${ROW_CLASS} flex-1`}>
          <Field label="Fit type" className="w-32">
            <select aria-label={`${termKey} term type`} className={SELECT_CLASS} style={MODELLING_INPUT_STYLE} defaultValue="expression">
              <option value="expression">Expression</option>
            </select>
          </Field>
          <Field label="Name" className="flex-[1_1_7rem]"><CommittedText label={`${termKey} name`} value={termKey} mono invalid={refusal?.field === "name"} onCommit={commit("name", onRename)} /></Field>
          <Field label="Expression" className="flex-[2_1_9rem]"><CommittedText label={`${termKey} expression`} value={String(spec.expr ?? "")} title={EXPRESSION_GRAMMAR} mono invalid={refusal?.field === "expr"} onCommit={commit("expr", onChangeExpr)} /></Field>
          <MonotonicityArrows label={termKey} spec={spec} onChangeField={onChangeField} />
        </div>
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

function AdditionalCard({
  termKey,
  spec,
  typeOptions,
  onChangeType,
  onRename,
  onChangeExpr,
  onChangeField,
  onRemove,
}: Extract<TermCardProps, { kind: "additional" }>) {
  const [refusal, setRefusal] = useState<{ field: RefusedField; reason: string } | null>(null)
  const type = spec.type
  const commit = (field: RefusedField, edit: (next: string) => EditResult) => (next: string) => {
    const result = edit(next)
    setRefusal(result.ok ? null : { field, reason: result.reason })
    return result.ok
  }
  return (
    <div className="rounded-lg px-2 py-1.5" style={CARD_STYLE}>
      <div className="flex items-start gap-1.5">
        <div className={`${ROW_CLASS} flex-1`}>
          <Field label="Fit type" className="w-32">
            <select aria-label={`${termKey} term type`} className={SELECT_CLASS} style={MODELLING_INPUT_STYLE} value={type} onChange={(event) => {
              const result = onChangeType(event.target.value as AdditionalTermType)
              setRefusal(result.ok ? null : { field: "expr", reason: result.reason })
            }}>
              {typeOptions.map((option) => <option key={option.value} value={option.value} disabled={option.disabled && option.value !== type}>{option.label}</option>)}
            </select>
          </Field>
          {type === "expression" ? (
            <>
              <Field label="Name" className="flex-[1_1_7rem]"><CommittedText label={`${termKey} name`} value={termKey} mono invalid={refusal?.field === "name"} onCommit={commit("name", onRename)} /></Field>
              <Field label="Expression" className="flex-[2_1_9rem]"><CommittedText label={`${termKey} expression`} value={String(spec.expr ?? "")} title={EXPRESSION_GRAMMAR} mono invalid={refusal?.field === "expr"} onCommit={commit("expr", onChangeExpr)} /></Field>
              <MonotonicityArrows label={termKey} spec={spec} onChangeField={onChangeField} />
            </>
          ) : (
            <>
              <ParameterFields label={termKey} spec={spec} onChangeField={onChangeField} />
              <ParameterFields label={termKey} spec={spec} onChangeField={onChangeField} region="advanced" />
            </>
          )}
        </div>
        <RemoveButton label={`Remove ${termKey} term`} onRemove={onRemove} />
      </div>
      {refusal && <span role="alert" className="text-[10px]" style={{ color: "var(--danger)" }}>{refusal.reason}</span>}
    </div>
  )
}

export function TermCard(props: TermCardProps) {
  if (props.kind === "native") {
    const { column, onChangeType, onChangeField, onRemove } = props
    // A config saved before the JSON guard can carry an entry with no usable
    // `type`. Render it as a linear card so the pane stays usable — picking a
    // type from the select repairs the entry on the next write.
    const spec: TermSpec = specType(props.spec) === null ? { type: "linear" } : props.spec
    return (
      <div className="flex items-start gap-1.5 rounded-lg px-2 py-1.5" style={CARD_STYLE}>
        <div className={`${ROW_CLASS} flex-1`}>
          <Field label="Fit type" className="w-32">
            <select aria-label={`${column} term type`} className={SELECT_CLASS} style={MODELLING_INPUT_STYLE} value={spec.type} onChange={(event) => onChangeType(event.target.value as NativeTermType)}>
              {(props.typeOptions ?? NATIVE_TERM_TYPES.map((type) => ({ ...type, disabled: false }))).map((type) => <option key={type.value} value={type.value} disabled={type.disabled && type.value !== spec.type}>{type.label}</option>)}
            </select>
          </Field>
          <ParameterFields label={column} spec={spec} onChangeField={onChangeField} allowLevels />
          <MonotonicityArrows label={column} spec={spec} onChangeField={onChangeField} />
          <ParameterFields label={column} spec={spec} onChangeField={onChangeField} allowLevels region="advanced" />
        </div>
        <RemoveButton label={`Remove ${column} term`} onRemove={onRemove} />
      </div>
    )
  }
  if (props.kind === "expression") {
    return <ExpressionCard {...props} />
  }
  if (props.kind === "additional") return <AdditionalCard {...props} />
  const { column, dtype, mainSpec, override, onChangeFit, onChangeField } = props
  const options = slotFitOptions(dtype, mainSpec, props.otherSpecs)
  const needsExplicit = override === undefined && slotNeedsExplicitFit(mainSpec)
  const effective = effectiveSlotSpec(dtype, mainSpec, override)
  const usesSharedTargetEncoding = effective.type === "target_encoding" && props.sharedTargetEncoding !== undefined
  const targetOverrideDiffersFromShared = override?.type === "target_encoding" && props.sharedTargetEncoding !== undefined && (() => {
    const shared = props.sharedTargetEncoding.spec
    const priorDiffers = Object.hasOwn(override, "prior_weight") &&
      (override.prior_weight ?? "auto") !== (shared.prior_weight ?? "auto")
    const permutationsDiffer = Object.hasOwn(override, "n_permutations") &&
      (override.n_permutations ?? 4) !== (shared.n_permutations ?? 4)
    return priorDiffers || permutationsDiffer
  })()
  const current = override ? override.type : needsExplicit ? "" : mainSpec ? "main" : effective.type
  const unavailable = !needsExplicit && !options.some((option) => option.value === current)
  const invalid = needsExplicit || unavailable
  const savedLabel = current === "main" ? `As main (${mainSpec?.type})` : NATIVE_TERM_TYPES.find((option) => option.value === current)?.label ?? current
  return (
    <div className={props.inline ? "contents" : `${ROW_CLASS} w-full`}>
      <Field label="Fit type" className={mainSpec ? "w-40" : "w-32"}>
        <select aria-label={`${column} fit in interaction`} aria-invalid={invalid} className={SELECT_CLASS} style={{ ...MODELLING_INPUT_STYLE, borderColor: invalid ? "var(--danger)" : "var(--border)" }} value={current} onChange={(event) => {
          // The placeholder is `disabled`, which stops a user picking it, but a
          // programmatic change still reaches here — "" is never a SlotFit.
          if (!options.some((option) => option.value === event.target.value)) return
          onChangeFit(event.target.value as SlotFit)
        }}>
          {/* Disabled so re-picking the placeholder can never emit "" as a fit. */}
          {needsExplicit && <option value="" disabled>Choose a fit…</option>}
          {unavailable && <option value={current} disabled>{savedLabel} (unavailable)</option>}
          {options.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      </Field>
      {override && !unavailable && !usesSharedTargetEncoding && <ParameterFields label={column} spec={override} onChangeField={onChangeField} />}
      {override && !unavailable && !usesSharedTargetEncoding && <ParameterFields label={column} spec={override} onChangeField={onChangeField} region="advanced" />}
      {effective.type === "target_encoding" && (
        <>
          <span className="basis-full text-[11px]" style={{ color: "var(--text-secondary)" }}>Target encoding includes its main effect, even when Include main effects is off.</span>
          {usesSharedTargetEncoding && <span className="basis-full text-[11px]" style={{ color: "var(--text-secondary)" }}>Uses target encoding settings from {props.sharedTargetEncoding!.key}.</span>}
          {usesSharedTargetEncoding && targetOverrideDiffersFromShared && (
            <span role="alert" className="basis-full text-[11px]" style={{ color: "var(--warning-strong)" }}>
              This override differs from shared target encoding settings. <button type="button" className="underline" onClick={() => onChangeFit("target_encoding")}>Use shared settings</button>
            </span>
          )}
        </>
      )}
      {needsExplicit && (
        <span className="basis-full text-xs" style={{ color: "var(--danger)" }}>{MONOTONE_SLOT_REASON}</span>
      )}
      {unavailable && <span role="alert" className="basis-full text-xs" style={{ color: "var(--danger)" }}>This fit is unavailable for this feature or its main term</span>}
    </div>
  )
}
