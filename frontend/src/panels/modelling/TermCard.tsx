import { useId, useState, type ReactNode } from "react"
import { ChevronRight, Trash2 } from "lucide-react"

import { NODE_GROUP_COLORS } from "../../theme/colors"
import { withAlpha } from "../../utils/color"
import {
  EXPRESSION_GRAMMAR,
  MAIN_FITS_BY_CLASS,
  MAX_PERMUTATIONS,
  SLOT_FITS_BY_CLASS,
  SPLINE_LIMITS,
  glmDtypeClass,
  minimumBasis,
  resolveSlot,
  slotFitOptions,
  splineMode,
  termSpecIssues,
  typeLabel,
  type AdditionalTermType,
  type EditResult,
  type NativeTermType,
  type SlotFit,
  type SplineMode,
  type TermSpec,
  type TermTypeOption,
  type Terms,
} from "./glmTerms"
import {
  GLM_FIELD_CLASS as FIELD_CLASS,
  GLM_ROW_CLASS as ROW_CLASS,
  GLM_SELECT_CLASS as SELECT_CLASS,
  MODELLING_INPUT_STYLE,
} from "./styles"
import { NumberField } from "./NumberField"

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
const CARD_STYLE = { background: "var(--bg-input)", border: "1px solid var(--border)" } as const

type FieldChange = (field: string, value: unknown) => void

export type TermCardProps =
  | {
      kind: "native"
      column: string
      dtype: string
      spec: TermSpec
      typeOptions: readonly TermTypeOption<NativeTermType>[]
      onChangeType: (nextType: NativeTermType) => void
      onChangeField: FieldChange
      onChangeSplineMode: (mode: SplineMode) => void
      onRemove: () => void
    }
  | {
      kind: "additional"
      termKey: string
      spec: TermSpec
      typeOptions: readonly TermTypeOption<AdditionalTermType>[]
      /** Explains why the term is listed under Unresolved terms. */
      notice?: string
      onChangeType: (nextType: AdditionalTermType) => EditResult
      onRename: (nextKey: string) => EditResult
      onChangeExpr: (expr: string) => EditResult
      onChangeField: FieldChange
      onRemove: () => void
    }
  | {
      kind: "unresolved"
      termKey: string
      spec: unknown
      reason: string
      /** Fits that repair a malformed entry keyed by an eligible column. */
      repairOptions?: readonly TermTypeOption<NativeTermType>[]
      onRepair?: (type: NativeTermType) => void
      onRemove: () => void
    }
  | {
      kind: "slot"
      column: string
      dtype: string
      terms: Terms
      override: TermSpec | undefined
      partnerSpecs: readonly (TermSpec | null)[]
      sharedTargetEncoding?: { key: string; spec: TermSpec }
      onChangeFit: (fit: SlotFit) => void
      onChangeField: FieldChange
      onChangeSplineMode: (mode: SplineMode) => void
    }

function Field({ label, children, className = "" }: { label: string; children: ReactNode; className?: string }) {
  return <label className={`${FIELD_CLASS} ${className}`}><span>{label}</span>{children}</label>
}

function Alert({ children, tone = "danger" }: { children: ReactNode; tone?: "danger" | "muted" }) {
  return (
    <span role={tone === "danger" ? "alert" : undefined} className="basis-full text-[11px]" style={{ color: tone === "danger" ? "var(--danger)" : "var(--text-secondary)" }}>
      {children}
    </span>
  )
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
          // An untouched field never writes: only an edit reaches the config.
          if (draft === committed) {
            setError(null)
            return
          }
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

function strictlyIncreasing(values: unknown[], exact?: number): string | null {
  if ((exact !== undefined && values.length !== exact) || (exact === undefined && values.length === 0)) {
    return exact ? `Must contain exactly ${exact} values` : "Must not be empty"
  }
  if (exact === undefined && values.length > SPLINE_LIMITS.maxKnots) return `At most ${SPLINE_LIMITS.maxKnots} knots`
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
  if (!values.every((value) => typeof value === "string")) return "Levels must be strings"
  return new Set(values).size === values.length ? null : "Levels must be unique"
}

/** A text input that commits on blur; a refused edit keeps its draft on screen. */
function CommittedText({
  label,
  value,
  title,
  mono,
  invalid,
  placeholder,
  onCommit,
}: {
  label: string
  value: string
  title?: string
  mono?: boolean
  invalid?: boolean
  placeholder?: string
  onCommit: (next: string) => boolean
}) {
  const [draft, setDraft] = useState<string | null>(null)
  const [lastValue, setLastValue] = useState(value)
  if (lastValue !== value) {
    setLastValue(value)
    setDraft(null)
  }
  return (
    <input
      type="text"
      aria-label={label}
      aria-invalid={invalid}
      title={title}
      placeholder={placeholder}
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
        <Field label="Weight">
          <NumberField label={`${label} prior weight`} value={priorWeight} min={0} step={0.5} integer={false} required onCommit={(value) => onChangeField("prior_weight", value)} />
        </Field>
      )}
      <AdvancedDisclosure>
        <Field label="Permutations">
          <NumberField label={`${label} n permutations`} value={spec.n_permutations as number | undefined} min={1} max={MAX_PERMUTATIONS} step={1} integer placeholder="4" onCommit={(value) => onChangeField("n_permutations", value)} />
        </Field>
      </AdvancedDisclosure>
    </div>
  )
}

function SplineFields({
  label,
  spec,
  onChangeField,
  onChangeSplineMode,
}: {
  label: string
  spec: TermSpec
  onChangeField: FieldChange
  onChangeSplineMode: (mode: SplineMode) => void
}) {
  const mode = splineMode(spec)
  const minimum = minimumBasis(spec.type, spec.degree)
  const hasDegree = spec.type === "bs" || spec.type === "ms"
  return (
    <>
      <Field label="df mode" className="w-[4.5rem]">
        <select aria-label={`${label} df mode`} title="Degrees of freedom" className={SELECT_CLASS} style={MODELLING_INPUT_STYLE} value={mode} onChange={(event) => onChangeSplineMode(event.target.value as SplineMode)}>
          <option value="auto">Auto</option>
          <option value="fixed">Fixed</option>
        </select>
      </Field>
      {mode === "fixed" && (spec.knots !== undefined ? (
        <div className={FIELD_CLASS}><span>df</span><span className="flex h-7 items-center text-xs" style={{ color: "var(--text-muted)" }}>Custom knots</span></div>
      ) : (
        <Field label="df">
          <NumberField label={`${label} df`} value={spec.df as number | undefined} min={minimum} max={SPLINE_LIMITS.maxBasis} step={1} integer required onCommit={(value) => onChangeField("df", value)} />
        </Field>
      ))}
      {hasDegree && (
        <Field label="Degree">
          <NumberField label={`${label} degree`} value={spec.degree as number | undefined} min={SPLINE_LIMITS.minDegree} max={SPLINE_LIMITS.maxDegree} step={1} integer placeholder={String(SPLINE_LIMITS.defaultDegree)} onCommit={(value) => onChangeField("degree", value)} />
        </Field>
      )}
      <AdvancedDisclosure>
        {mode === "auto" ? (
          <Field label="Basis size (k)">
            <NumberField label={`${label} k`} value={spec.k as number | undefined} min={minimum} max={SPLINE_LIMITS.maxBasis} step={1} integer placeholder="10" onCommit={(value) => onChangeField("k", value)} />
          </Field>
        ) : (
          <JsonArrayField label="Interior knots" inputLabel={`${label} knots`} value={spec.knots} placeholder="[1, 2, 3]" help="Finite, strictly increasing numbers. Replaces df." validate={validateKnots} onChange={(value) => value === undefined ? onChangeSplineMode("fixed") : onChangeField("knots", value)} />
        )}
        <JsonArrayField label="Boundary knots" inputLabel={`${label} boundary knots`} value={spec.boundary_knots} placeholder="[0, 10]" help="Exactly two finite, increasing numbers enclosing every knot." validate={validateBoundaryKnots} onChange={(value) => onChangeField("boundary_knots", value)} />
      </AdvancedDisclosure>
    </>
  )
}

function CategoricalAdvanced({ label, spec, onChangeField }: { label: string; spec: TermSpec; onChangeField: FieldChange }) {
  return (
    <AdvancedDisclosure>
      <Field label="Reference level">
        <CommittedText
          label={`${label} reference level`}
          value={typeof spec.reference === "string" ? spec.reference : ""}
          placeholder="First level"
          mono
          onCommit={(next) => {
            onChangeField("reference", next.trim() === "" ? undefined : next.trim())
            return true
          }}
        />
        <span className="text-[10px]">The baseline level; unseen levels share it. Replaces Levels.</span>
      </Field>
      <JsonArrayField label="Levels" inputLabel={`${label} levels`} value={spec.levels} placeholder='["basic", "premium"]' help="Indicators only for these levels; other and unseen levels share the intercept. Quote numeric labels, e.g. &quot;2&quot;. Replaces Reference level." validate={validateLevels} onChange={(value) => onChangeField("levels", value)} />
    </AdvancedDisclosure>
  )
}

function MonotonicityArrows({ label, spec, onChangeField }: { label: string; spec: TermSpec; onChangeField: FieldChange }) {
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

function TypeSelect<T extends string>({
  label,
  value,
  options,
  invalid,
  placeholder,
  onChange,
}: {
  label: string
  value: string
  options: readonly TermTypeOption<T>[]
  invalid?: boolean
  placeholder?: string
  onChange: (value: T) => void
}) {
  const known = options.some((option) => option.value === value)
  return (
    <Field label="Fit type" className="w-32">
      <select
        aria-label={label}
        aria-invalid={invalid}
        className={SELECT_CLASS}
        style={{ ...MODELLING_INPUT_STYLE, borderColor: invalid ? "var(--danger)" : "var(--border)" }}
        value={value}
        onChange={(event) => {
          // A placeholder option is disabled, but a programmatic change still
          // reaches here; only real options are ever written.
          const next = options.find((option) => option.value === event.target.value)
          if (next) onChange(next.value)
        }}
      >
        {placeholder !== undefined && <option value="" disabled>{placeholder}</option>}
        {!known && value !== "" && <option value={value} disabled>{typeLabel(value)} (unavailable)</option>}
        {options.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
      </select>
    </Field>
  )
}

function IssueList({ spec }: { spec: TermSpec }) {
  const issues = termSpecIssues(spec)
  return <>{issues.map((issue) => <Alert key={issue}>{issue}</Alert>)}</>
}

function NativeCard({ column, dtype, spec, typeOptions, onChangeType, onChangeField, onChangeSplineMode, onRemove }: Extract<TermCardProps, { kind: "native" }>) {
  const dtypeClass = glmDtypeClass(dtype)
  const unavailable = !MAIN_FITS_BY_CLASS[dtypeClass].has(spec.type)
  const spline = spec.type === "bs" || spec.type === "ns" || spec.type === "ms"
  return (
    <div className="flex items-start gap-1.5 rounded-lg px-2 py-1.5" style={CARD_STYLE}>
      <div className={`${ROW_CLASS} flex-1`}>
        <TypeSelect label={`${column} term type`} value={spec.type} options={typeOptions} invalid={unavailable} onChange={onChangeType} />
        {spline && <SplineFields label={column} spec={spec} onChangeField={onChangeField} onChangeSplineMode={onChangeSplineMode} />}
        {spec.type === "target_encoding" && <EncodingControls label={column} spec={spec} onChangeField={onChangeField} inline />}
        <MonotonicityArrows label={column} spec={spec} onChangeField={onChangeField} />
        {spec.type === "categorical" && <CategoricalAdvanced label={column} spec={spec} onChangeField={onChangeField} />}
        {unavailable && <Alert>{typeLabel(spec.type)} is not available for a {dtypeClass} column; choose another fit or remove it.</Alert>}
        <IssueList spec={spec} />
      </div>
      <RemoveButton label={`Remove ${column} term`} onRemove={onRemove} />
    </div>
  )
}

type RefusedField = "name" | "expr" | "type"

function AdditionalCard({ termKey, spec, typeOptions, notice, onChangeType, onRename, onChangeExpr, onChangeField, onRemove }: Extract<TermCardProps, { kind: "additional" }>) {
  const [refusals, setRefusals] = useState<Partial<Record<RefusedField, string>>>({})
  const record = (field: RefusedField, result: EditResult) => {
    setRefusals((current) => {
      const next = { ...current }
      if (result.ok) delete next[field]
      else next[field] = result.reason
      return next
    })
    return result.ok
  }
  return (
    <div className="rounded-lg px-2 py-1.5" style={CARD_STYLE}>
      <div className="flex items-start gap-1.5">
        <div className={`${ROW_CLASS} flex-1`}>
          <TypeSelect label={`${termKey} term type`} value={spec.type} options={typeOptions} invalid={refusals.type !== undefined} onChange={(next) => record("type", onChangeType(next))} />
          {spec.type === "expression" ? (
            <>
              <Field label="Name" className="flex-[1_1_7rem]"><CommittedText label={`${termKey} name`} value={termKey} mono invalid={refusals.name !== undefined} onCommit={(next) => record("name", onRename(next))} /></Field>
              <Field label="Expression" className="flex-[2_1_9rem]"><CommittedText label={`${termKey} expression`} value={String(spec.expr ?? "")} title={EXPRESSION_GRAMMAR} mono invalid={refusals.expr !== undefined} onCommit={(next) => record("expr", onChangeExpr(next))} /></Field>
              <MonotonicityArrows label={termKey} spec={spec} onChangeField={onChangeField} />
            </>
          ) : (
            spec.type === "target_encoding" && <EncodingControls label={termKey} spec={spec} onChangeField={onChangeField} inline />
          )}
          {refusals.type && <Alert>{refusals.type}</Alert>}
          {refusals.name && <Alert>{refusals.name}</Alert>}
          {refusals.expr && <Alert>{refusals.expr}</Alert>}
          {notice && <Alert>{notice}</Alert>}
          <IssueList spec={spec} />
        </div>
        <RemoveButton label={`Remove ${termKey} term`} onRemove={onRemove} />
      </div>
    </div>
  )
}

function UnresolvedCard({ termKey, spec, reason, repairOptions, onRepair, onRemove }: Extract<TermCardProps, { kind: "unresolved" }>) {
  const type = typeof spec === "object" && spec !== null && typeof (spec as Record<string, unknown>).type === "string"
    ? (spec as Record<string, string>).type
    : null
  return (
    <div className="flex items-start gap-1.5 rounded-lg px-2 py-1.5" style={CARD_STYLE}>
      <div className={`${ROW_CLASS} flex-1`}>
        <div className={FIELD_CLASS}>
          <span>Term</span>
          <span className="flex h-7 items-center font-mono text-xs" style={{ color: "var(--text-primary)" }}>{termKey}</span>
        </div>
        {repairOptions && onRepair ? (
          <TypeSelect label={`${termKey} term type`} value="" placeholder={type === null ? "Choose a fit…" : `${type} (unsupported)`} options={repairOptions} invalid onChange={onRepair} />
        ) : (
          <div className={FIELD_CLASS}>
            <span>Fit type</span>
            <span className="flex h-7 items-center text-xs" style={{ color: "var(--text-secondary)" }}>{type === null ? "None" : typeLabel(type)}</span>
          </div>
        )}
        <Alert>{reason}</Alert>
      </div>
      <RemoveButton label={`Remove ${termKey} term`} onRemove={onRemove} />
    </div>
  )
}

function SlotCard({ column, dtype, terms, override, partnerSpecs, sharedTargetEncoding, onChangeFit, onChangeField, onChangeSplineMode }: Extract<TermCardProps, { kind: "slot" }>) {
  const resolution = resolveSlot(terms, column, dtype, override)
  const options = slotFitOptions(terms, column, dtype, partnerSpecs)
  const current = resolution.kind === "override"
    ? resolution.spec.type
    : resolution.kind === "main"
      ? "main"
      : resolution.kind === "default"
        ? resolution.spec.type
        : ""
  const unavailable = current !== "" && !options.some((option) => option.value === current)
  const dtypeClass = glmDtypeClass(dtype)
  const classRefuses = resolution.kind === "override" && !SLOT_FITS_BY_CLASS[dtypeClass].has(resolution.spec.type)
  const effectiveType = resolution.kind === "blocked" ? null : resolution.spec.type
  const sharesEncoding = effectiveType === "target_encoding" && sharedTargetEncoding !== undefined
  const overrideDiffersFromShared = override?.type === "target_encoding" && sharedTargetEncoding !== undefined && (
    (override.prior_weight !== undefined && override.prior_weight !== (sharedTargetEncoding.spec.prior_weight ?? "auto"))
    || (override.n_permutations !== undefined && override.n_permutations !== (sharedTargetEncoding.spec.n_permutations ?? 4))
  )
  const optionsWithCurrent: TermTypeOption<SlotFit>[] = options
  return (
    <div className="contents">
      <Field label="Fit type" className={resolution.kind === "main" ? "w-40" : "w-32"}>
        <select
          aria-label={`${column} fit in interaction`}
          aria-invalid={resolution.kind === "blocked" || unavailable}
          className={SELECT_CLASS}
          style={{ ...MODELLING_INPUT_STYLE, borderColor: resolution.kind === "blocked" || unavailable ? "var(--danger)" : "var(--border)" }}
          value={current}
          onChange={(event) => {
            const next = options.find((option) => option.value === event.target.value)
            if (next) onChangeFit(next.value)
          }}
        >
          {resolution.kind === "blocked" && <option value="" disabled>Choose a fit…</option>}
          {unavailable && <option value={current} disabled>{current === "main" ? "As main" : typeLabel(current)} (unavailable)</option>}
          {optionsWithCurrent.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
        </select>
      </Field>
      {resolution.kind === "override" && !unavailable && (resolution.spec.type === "bs" || resolution.spec.type === "ns") && (
        <SplineFields label={column} spec={resolution.spec} onChangeField={onChangeField} onChangeSplineMode={onChangeSplineMode} />
      )}
      {resolution.kind === "override" && !unavailable && resolution.spec.type === "target_encoding" && !sharesEncoding && (
        <EncodingControls label={column} spec={resolution.spec} onChangeField={onChangeField} inline />
      )}
      {effectiveType === "target_encoding" && (
        <Alert tone="muted">Target encoding includes its main effect, even when Include main effects is off.</Alert>
      )}
      {sharesEncoding && <Alert tone="muted">Uses target encoding settings from {sharedTargetEncoding.key}.</Alert>}
      {sharesEncoding && overrideDiffersFromShared && (
        <Alert>
          This override differs from the shared target encoding settings.{" "}
          <button type="button" className="underline" onClick={() => onChangeFit("target_encoding")}>Use shared settings</button>
        </Alert>
      )}
      {resolution.kind === "blocked" && <Alert>{resolution.reason}</Alert>}
      {unavailable && !classRefuses && <Alert>This fit is unavailable for this feature, its main effect, or its partners</Alert>}
      {classRefuses && <Alert>{typeLabel(resolution.spec.type)} is not available for a {dtypeClass} column</Alert>}
      {resolution.kind === "override" && <IssueList spec={resolution.spec} />}
    </div>
  )
}

export function TermCard(props: TermCardProps) {
  switch (props.kind) {
    case "native":
      return <NativeCard {...props} />
    case "additional":
      return <AdditionalCard {...props} />
    case "unresolved":
      return <UnresolvedCard {...props} />
    case "slot":
      return <SlotCard {...props} />
  }
}
