import { useEffect, useMemo, useRef, useState } from "react"
import { Plus, Search } from "lucide-react"

import type { OnUpdateConfig } from "../editors/_shared"
import { configField } from "../../utils/configField"
import { roleColumnReasons, type ModellingColumn } from "./featureSelection"
import {
  addTerm,
  addTermAvailability,
  additionalTypeOptions,
  columnContext,
  expressionIdentifiers,
  fitAllWithDefaults,
  interactionEntryIssue,
  isAdditionalSpec,
  isExpressionSpec,
  isTermSpecShape,
  modelMembership,
  nativeTypeOptions,
  removeTerm,
  renameTerm,
  repairMalformedTerm,
  setExpression,
  setSplineMode,
  setTermField,
  simulateInteractionDesign,
  switchAdditionalType,
  switchNativeType,
  featureTag,
  termsByColumn,
  typeLabel,
  type ColumnContext,
  type EditResult,
  type InteractionSpec,
  type Terms,
  type TermEntry,
  type UnresolvedTerm,
} from "./glmTerms"
import { MODELLING_INPUT_STYLE, toggleButtonStyle } from "./styles"
import { TermCard } from "./TermCard"

type Props = {
  config: Record<string, unknown>
  onUpdate: OnUpdateConfig
  columns: ModellingColumn[]
}

const REMOVE_ALL_CONFIRMATION = "Remove every term and interaction from this model?"

/** The eligible column an additional term belongs to, for its type options. */
function additionalColumn(key: string, spec: TermEntry["spec"], context: ColumnContext): ModellingColumn | null {
  const source = isExpressionSpec(spec)
    ? expressionIdentifiers(String(spec.expr ?? ""))?.[0]
    : typeof spec.variable === "string" ? spec.variable : key
  return source !== undefined && context.eligibleNames.has(source) ? context.byName.get(source) ?? null : null
}

export function GLMTermsConfig({ config, onUpdate, columns }: Props) {
  const [filter, setFilter] = useState("")
  const [inModelOnly, setInModelOnly] = useState(false)
  const [mode, setMode] = useState<"builder" | "json">("builder")
  const terms = configField<Terms>(config, "terms", {})
  const rawInteractions = configField<unknown>(config, "interactions", [])

  const roles = useMemo(() => roleColumnReasons(config), [config])
  const context = useMemo(() => columnContext(columns, roles), [columns, roles])
  const interactions = useMemo(
    () => (Array.isArray(rawInteractions) ? rawInteractions : []).filter(
      (entry): entry is InteractionSpec => interactionEntryIssue(entry) === null,
    ),
    [rawInteractions],
  )
  const dtypeOf = useMemo(() => (name: string) => context.byName.get(name)?.dtype ?? "", [context])
  const { byColumn, unresolved } = useMemo(() => termsByColumn(terms, context), [terms, context])
  const membership = useMemo(() => modelMembership(terms, interactions, context), [terms, interactions, context])
  const design = useMemo(() => simulateInteractionDesign(terms, interactions, dtypeOf), [terms, interactions, dtypeOf])

  const query = filter.trim().toLowerCase()
  const visible = useMemo(
    () => context.eligible.filter((column) =>
      column.name.toLowerCase().includes(query) && (!inModelOnly || membership.inModel.has(column.name)),
    ),
    [context, query, inModelOnly, membership],
  )
  const rowOptions = useMemo(
    () => new Map(visible.map((column) => [
      column.name,
      {
        native: nativeTypeOptions(terms, column.name, column.dtype),
        add: addTermAvailability(terms, column.name, column.dtype),
      },
    ])),
    [visible, terms],
  )

  const writeTerms = (next: Terms) => onUpdate("terms", next)
  const applied = (result: EditResult): EditResult => {
    if (result.ok) writeTerms(result.terms)
    return result
  }
  const handlersFor = (key: string) => ({
    onChangeField: (field: string, value: unknown) => writeTerms(setTermField(terms, key, field, value)),
    onRemove: () => writeTerms(removeTerm(terms, key)),
    onRename: (nextKey: string) => applied(renameTerm(terms, key, nextKey, context.upstreamNames)),
    onChangeExpr: (expr: string) => applied(setExpression(terms, key, expr, context)),
  })

  // JSON mode: the RustyStats dict, saved on blur.
  const termsJson = useMemo(() => JSON.stringify(terms, null, 2), [terms])
  const [jsonDraft, setJsonDraft] = useState(termsJson)
  const [jsonError, setJsonError] = useState<string | null>(null)
  const lastSyncedRef = useRef(termsJson)
  useEffect(() => {
    if (termsJson !== lastSyncedRef.current) {
      setJsonDraft(termsJson)
      lastSyncedRef.current = termsJson
      setJsonError(null)
    }
  }, [termsJson])
  const commitJson = (text: string) => {
    try {
      const parsed = JSON.parse(text)
      if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
        setJsonError("Must be a JSON object")
        return
      }
      const malformed = Object.entries(parsed as Record<string, unknown>).find(([, spec]) => !isTermSpecShape(spec))
      if (malformed) {
        setJsonError(`Term "${malformed[0]}" must be an object with a string "type"`)
        return
      }
      setJsonError(null)
      writeTerms(parsed as Terms)
    } catch (error) {
      setJsonError((error as Error).message)
    }
  }

  const renderAdditional = ({ key, spec }: TermEntry, column: ModellingColumn | null, notice?: string) => {
    const handlers = handlersFor(key)
    return (
      <TermCard
        key={key}
        kind="additional"
        termKey={key}
        spec={spec}
        notice={notice}
        typeOptions={column
          ? additionalTypeOptions(terms, key, column.name, column.dtype)
          : [{ value: spec.type as "expression", label: typeLabel(spec.type) }]}
        onChangeType={(nextType) => column
          ? applied(switchAdditionalType(terms, key, column.name, column.dtype, nextType))
          : { ok: false, reason: "Fix the columns this term reads before changing its type." }}
        onRename={handlers.onRename}
        onChangeExpr={handlers.onChangeExpr}
        onChangeField={handlers.onChangeField}
        onRemove={handlers.onRemove}
      />
    )
  }

  const renderTermCards = (column: ModellingColumn) =>
    (byColumn.get(column.name) ?? []).map((entry) => {
      if (isAdditionalSpec(entry.spec, entry.key)) return renderAdditional(entry, column)
      const handlers = handlersFor(entry.key)
      return (
        <TermCard
          key={entry.key}
          kind="native"
          column={column.name}
          dtype={column.dtype}
          spec={entry.spec}
          typeOptions={rowOptions.get(column.name)?.native ?? []}
          onChangeType={(nextType) => writeTerms(switchNativeType(terms, column.name, nextType))}
          onChangeField={handlers.onChangeField}
          onChangeSplineMode={(splineMode) => writeTerms(setSplineMode(terms, column.name, splineMode))}
          onRemove={handlers.onRemove}
        />
      )
    })

  const renderUnresolved = (entry: UnresolvedTerm) => {
    const handlers = handlersFor(entry.key)
    const column = context.eligibleNames.has(entry.key) ? context.byName.get(entry.key) : undefined
    if (!entry.malformed && isAdditionalSpec(entry.spec, entry.key)) {
      return renderAdditional(entry, additionalColumn(entry.key, entry.spec, context), entry.reason)
    }
    return (
      <TermCard
        key={entry.key}
        kind="unresolved"
        termKey={entry.key}
        spec={entry.spec}
        reason={entry.reason}
        repairOptions={entry.malformed && column ? nativeTypeOptions(terms, column.name, column.dtype) : undefined}
        onRepair={entry.malformed && column ? (type) => writeTerms(repairMalformedTerm(terms, column.name, type)) : undefined}
        onRemove={handlers.onRemove}
      />
    )
  }

  const hiddenCount = context.unsupported.length
  return (
    <section aria-labelledby="model-features-heading">
      <div className="flex items-end justify-between gap-3">
        <h3 id="model-features-heading" className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: "var(--text-muted)" }}>
          Features
        </h3>
        <span className="text-[10px] tabular-nums" style={{ color: "var(--text-secondary)" }}>
          {membership.inModel.size} of {context.eligible.length} in model
        </span>
      </div>

      <div className="relative mt-2">
        <Search aria-hidden="true" className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2" size={13} style={{ color: "var(--text-muted)" }} />
        <input
          aria-label="Search features"
          className="w-full rounded-lg py-2 pl-8 pr-2.5 text-xs outline-none focus:ring-1 focus:ring-[var(--model-accent-border)]"
          style={MODELLING_INPUT_STYLE}
          value={filter}
          onChange={(event) => setFilter(event.target.value)}
          placeholder="Search features"
        />
      </div>

      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        <button type="button" className="rounded-lg px-2.5 py-1 text-[10px] font-medium" style={toggleButtonStyle(false)} onClick={() => writeTerms(fitAllWithDefaults(terms, context))}>
          Fit all with defaults
        </button>
        <button
          type="button"
          className="rounded-lg px-2.5 py-1 text-[10px] font-medium"
          style={{ background: "var(--danger-soft)", border: "1px solid var(--danger)", color: "var(--danger)" }}
          onClick={() => {
            if (!confirm(REMOVE_ALL_CONFIRMATION)) return
            onUpdate({ terms: {}, interactions: [] })
          }}
        >
          Remove all terms
        </button>
        <button type="button" role="switch" aria-checked={inModelOnly} aria-label="In model only" className="rounded-lg px-2.5 py-1 text-[10px] font-medium" style={toggleButtonStyle(inModelOnly)} onClick={() => setInModelOnly((value) => !value)}>
          In model only
        </button>
        <span className="flex-1" />
        <button type="button" className="rounded px-2 py-0.5 text-[10px] font-medium" style={toggleButtonStyle(mode === "builder")} onClick={() => setMode("builder")}>
          Builder
        </button>
        <button type="button" className="rounded px-2 py-0.5 text-[10px] font-medium" style={toggleButtonStyle(mode === "json")} onClick={() => setMode("json")}>
          JSON
        </button>
      </div>
      {hiddenCount > 0 && (
        <p className="mt-1.5 text-[10px]" style={{ color: "var(--text-secondary)" }}>
          {hiddenCount} {hiddenCount === 1 ? "column is" : "columns are"} hidden because GLM fits do not support {hiddenCount === 1 ? "its dtype" : "their dtypes"}: {context.unsupported.map((column) => `${column.name} (${column.dtype})`).join(", ")}.
        </p>
      )}

      {mode === "json" ? (
        <div className="mt-2">
          <p className="mb-1 text-[10px]" style={{ color: "var(--text-muted)" }}>
            RustyStats terms dict. Paste from Atelier or edit directly. Saved on blur.
          </p>
          <textarea
            aria-label="Terms JSON"
            value={jsonDraft}
            onChange={(event) => setJsonDraft(event.target.value)}
            onBlur={() => commitJson(jsonDraft)}
            spellCheck={false}
            rows={Math.min(20, Math.max(6, jsonDraft.split("\n").length + 1))}
            className="w-full rounded-lg px-2.5 py-2 font-mono text-xs"
            style={{ ...MODELLING_INPUT_STYLE, border: `1px solid ${jsonError ? "var(--danger)" : "var(--border)"}`, resize: "vertical" }}
          />
          {jsonError && <p role="alert" className="mt-0.5 text-[10px]" style={{ color: "var(--danger)" }}>{jsonError}</p>}
        </div>
      ) : (
        <div className="mt-3 grid gap-1.5">
          {unresolved.length > 0 && (
            <div role="group" aria-label="Unresolved terms" className="rounded-lg px-3 py-2" style={{ background: "var(--danger-soft-subtle)", border: "1px solid var(--danger-border)" }}>
              <p className="text-xs font-semibold" style={{ color: "var(--danger-text-soft)" }}>Unresolved terms</p>
              <p className="mt-1 text-[11px]" style={{ color: "var(--danger-text-soft)" }}>These terms cannot be fitted. Fix or remove them before training.</p>
              <div className="ml-2 mt-1.5 grid min-w-0 gap-1.5 border-l pl-3" style={{ borderColor: "var(--danger-border)" }}>{unresolved.map(renderUnresolved)}</div>
            </div>
          )}
          {visible.map((column) => {
            const cards = renderTermCards(column)
            const tag = featureTag(column.name, cards.length > 0, membership, design)
            const availability = rowOptions.get(column.name)?.add ?? { ok: true as const }
            return (
              <div key={column.name} role="group" aria-label={`${column.name} feature`} className="min-w-0 rounded-lg px-3 py-2" style={{ background: "var(--bg-elevated)", border: "1px solid var(--border)" }}>
                <div className="flex min-w-0 flex-wrap items-center gap-2">
                  <span className="min-w-0 flex-1 break-words font-mono text-xs font-semibold" title={column.name} style={{ color: "var(--text-primary)", overflowWrap: "anywhere" }}>
                    {column.name}
                  </span>
                  <span className="max-w-20 shrink-0 truncate rounded px-1.5 py-0.5 font-mono text-[10px]" title={column.dtype} style={{ background: "var(--chrome-hover)", color: "var(--text-secondary)" }}>{column.dtype}</span>
                  {tag && <span className="text-[11px]" style={{ color: "var(--text-secondary)" }}>{tag}</span>}
                  <span title={availability.ok ? undefined : availability.reason}>
                    <button
                      type="button"
                      aria-label={`Add ${column.name} term`}
                      aria-description={availability.ok ? undefined : availability.reason}
                      disabled={!availability.ok}
                      className="focus-ring inline-flex shrink-0 items-center gap-1 rounded-md px-2.5 py-1.5 text-[11px] font-semibold disabled:cursor-not-allowed disabled:opacity-50"
                      style={toggleButtonStyle(false)}
                      onClick={() => writeTerms(addTerm(terms, column.name, column.dtype, context.upstreamNames))}
                    >
                      <Plus size={12} aria-hidden="true" /> Add term
                    </button>
                  </span>
                </div>
                {cards.length > 0 && (
                  <div className="ml-2 mt-1.5 grid min-w-0 gap-1.5 border-l pl-3" style={{ borderColor: "var(--border)" }}>{cards}</div>
                )}
              </div>
            )
          })}
          {visible.length === 0 && (
            <p className="rounded-lg border border-dashed px-3 py-5 text-center text-[10px]" style={{ color: "var(--text-muted)", borderColor: "var(--border)" }}>
              No matching feature columns.
            </p>
          )}
        </div>
      )}
    </section>
  )
}

