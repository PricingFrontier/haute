import { useEffect, useMemo, useRef, useState } from "react"
import { Plus, Search } from "lucide-react"

import type { OnUpdateConfig } from "../editors/_shared"
import { configField } from "../../utils/configField"
import { roleColumns, type ModellingColumn } from "./featureSelection"
import {
  addTerm,
  additionalTypeOptions,
  canAddTerm,
  fitAllWithDefaults,
  isAdditionalSpec,
  isEncodingSpec,
  isExpressionSpec,
  isTermSpecShape,
  modelMembership,
  nativeTypeOptions,
  removeTerm,
  renameExpression,
  setExpression,
  setTermField,
  switchNativeType,
  switchAdditionalType,
  termsByColumn,
  type InteractionSpec,
  type NativeTermType,
  type Terms,
  type TermEntry,
} from "./glmTerms"
import { MODELLING_INPUT_STYLE, toggleButtonStyle } from "./styles"
import { TermCard } from "./TermCard"

type Props = {
  config: Record<string, unknown>
  onUpdate: OnUpdateConfig
  columns: ModellingColumn[]
}

const REMOVE_ALL_CONFIRMATION = "Remove every term and interaction from this model?"

export function GLMTermsConfig({ config, onUpdate, columns }: Props) {
  const [filter, setFilter] = useState("")
  const [inModelOnly, setInModelOnly] = useState(false)
  const [mode, setMode] = useState<"builder" | "json">("builder")
  const terms = configField<Terms>(config, "terms", {})
  const interactions = configField<InteractionSpec[]>(config, "interactions", [])

  const eligible = useMemo(() => {
    const roles = roleColumns(config)
    return columns.filter((column) => !roles.has(column.name))
  }, [columns, config])
  const eligibleNames = useMemo(() => new Set(eligible.map((column) => column.name)), [eligible])
  const allColumnNames = useMemo(() => new Set(columns.map((column) => column.name)), [columns])
  const { byColumn, unresolved } = useMemo(() => termsByColumn(terms, eligibleNames), [terms, eligibleNames])
  const membership = useMemo(
    () => modelMembership(terms, interactions, eligibleNames),
    [terms, interactions, eligibleNames],
  )

  const visible = eligible.filter((column) => {
    if (!column.name.toLowerCase().includes(filter.trim().toLowerCase())) return false
    return !inModelOnly || membership.inModel.has(column.name)
  })

  const writeTerms = (next: Terms) => onUpdate("terms", next)

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
      // Every consumer of a terms dict reads `spec.type`, so a malformed entry
      // written here would break the builder on the next render. Refuse the
      // whole dict and keep the draft on screen so it can be corrected.
      const malformed = Object.entries(parsed as Record<string, unknown>).find(
        ([, spec]) => !isTermSpecShape(spec),
      )
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

  const renderAdditionalCard = ({ key, spec }: TermEntry, column: ModellingColumn) => {
    const sharedProps = {
      termKey: key,
      spec,
      onRename: (nextKey: string) => {
        const result = renameExpression(terms, key, nextKey, allColumnNames)
        if (result.ok) writeTerms(result.terms)
        return result
      },
      onChangeExpr: (expr: string) => {
        const result = setExpression(terms, key, expr, eligibleNames)
        if (result.ok) writeTerms(result.terms)
        return result
      },
      onChangeField: (field: string, value: unknown) => writeTerms(setTermField(terms, key, field, value)),
      onRemove: () => writeTerms(removeTerm(terms, key)),
    }
    return (
      <TermCard
        key={key}
        kind="additional"
        {...sharedProps}
        column={column.name}
        dtype={column.dtype}
        typeOptions={additionalTypeOptions(column.dtype, terms, column.name, key)}
        onChangeType={(nextType) => {
          const result = switchAdditionalType(terms, key, column.name, column.dtype, nextType)
          if (result.ok) writeTerms(result.terms)
          return result
        }}
      />
    )
  }

  const addColumnTerm = (column: ModellingColumn) => {
    writeTerms(addTerm(terms, column.name, column.dtype, allColumnNames))
  }
  const addButton = (column: ModellingColumn) => (
    <button type="button" aria-label={`Add ${column.name} term`} disabled={!canAddTerm(terms, column.name, column.dtype)} className="focus-ring inline-flex shrink-0 items-center gap-1 rounded-md px-2.5 py-1.5 text-[11px] font-semibold disabled:cursor-not-allowed disabled:opacity-50" style={toggleButtonStyle(false)} onClick={() => addColumnTerm(column)}>
      <Plus size={12} aria-hidden="true" /> Add term
    </button>
  )

  const renderTermCards = (column: ModellingColumn) =>
    (byColumn.get(column.name) ?? []).map(({ key, spec }) =>
      // Not `spec.type`: a stale entry can be type-less or null, and reading
      // through it here used to take the whole builder pane down.
      isAdditionalSpec(spec, key) ? renderAdditionalCard({ key, spec }, column) : (
        <TermCard
          key={key}
          kind="native"
          column={column.name}
          spec={spec}
          typeOptions={nativeTypeOptions(terms, column.name, column.dtype)}
          onChangeType={(nextType: NativeTermType) => writeTerms(switchNativeType(terms, column.name, nextType))}
          onChangeField={(field, value) => writeTerms(setTermField(terms, column.name, field, value))}
          onRemove={() => writeTerms(removeTerm(terms, column.name))}
        />
      ),
    )

  const renderUnresolvedTerm = ({ key, spec }: TermEntry) => {
    if (isExpressionSpec(spec)) {
      return (
        <TermCard
          key={key}
          kind="expression"
          termKey={key}
          spec={spec}
          onRename={(nextKey) => {
            const result = renameExpression(terms, key, nextKey, allColumnNames)
            if (result.ok) writeTerms(result.terms)
            return result
          }}
          onChangeExpr={(expr) => {
            const result = setExpression(terms, key, expr, eligibleNames)
            if (result.ok) writeTerms(result.terms)
            return result
          }}
          onChangeField={(field, value) => writeTerms(setTermField(terms, key, field, value))}
          onRemove={() => writeTerms(removeTerm(terms, key))}
        />
      )
    }
    if (isEncodingSpec(spec)) {
      const type = spec.type
      return (
        <TermCard
          key={key}
          kind="additional"
          termKey={key}
          column={typeof spec.variable === "string" ? spec.variable : ""}
          dtype="String"
          spec={spec}
          typeOptions={[{ value: type, label: type === "target_encoding" ? "Target enc." : "Frequency enc.", disabled: false }]}
          onChangeType={() => ({ ok: false, reason: "Repair the source column in JSON before changing this term." })}
          onRename={(nextKey) => {
            const result = renameExpression(terms, key, nextKey, allColumnNames)
            if (result.ok) writeTerms(result.terms)
            return result
          }}
          onChangeExpr={() => ({ ok: false, reason: "This encoding has no expression." })}
          onChangeField={(field, value) => writeTerms(setTermField(terms, key, field, value))}
          onRemove={() => writeTerms(removeTerm(terms, key))}
        />
      )
    }
    return null
  }

  return (
    <section aria-labelledby="model-features-heading">
      <div className="flex items-end justify-between gap-3">
        <h3 id="model-features-heading" className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: "var(--text-muted)" }}>
          Features
        </h3>
        <span className="text-[10px] tabular-nums" style={{ color: "var(--text-secondary)" }}>
          {membership.inModel.size} of {eligible.length} in model
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
        <button type="button" className="rounded-lg px-2.5 py-1 text-[10px] font-medium" style={toggleButtonStyle(false)} onClick={() => writeTerms(fitAllWithDefaults(terms, eligible))}>
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
          {jsonError && <p className="mt-0.5 text-[10px]" style={{ color: "var(--danger)" }}>{jsonError}</p>}
        </div>
      ) : (
        <div className="mt-3 grid gap-1.5">
          {visible.map((column) => {
            const cards = renderTermCards(column)
            // Membership, not the cards anchored here: an expression naming
            // this column is anchored under its *first* identifier, so a row
            // with no cards of its own can still be in the model.
            const tag = !membership.inModel.has(column.name)
              ? "Not in model"
              : cards.length > 0
                ? null
                : membership.interactionOnly.has(column.name)
                  ? "Interaction only"
                  : "In an expression"
            return (
              <div key={column.name} role="group" aria-label={`${column.name} feature`} className="min-w-0 rounded-lg px-3 py-2" style={{ background: "var(--bg-elevated)", border: "1px solid var(--border)" }}>
                <div className="flex min-w-0 flex-wrap items-center gap-2">
                  <span className="min-w-0 flex-1 break-words font-mono text-xs font-semibold" title={column.name} style={{ color: "var(--text-primary)", overflowWrap: "anywhere" }}>
                    {column.name}
                  </span>
                  <span className="max-w-20 shrink-0 truncate rounded px-1.5 py-0.5 font-mono text-[10px]" title={column.dtype} style={{ background: "var(--chrome-hover)", color: "var(--text-secondary)" }}>{column.dtype}</span>
                  {tag && <span className="text-[11px]" style={{ color: "var(--text-secondary)" }}>{tag}</span>}
                  {addButton(column)}
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
          {unresolved.length > 0 && (
            <div role="group" aria-label="Unresolved terms" className="rounded-lg px-3 py-2" style={{ background: "var(--danger-soft-subtle)", border: "1px solid var(--danger-border)" }}>
              <p className="text-xs font-semibold" style={{ color: "var(--danger-text-soft)" }}>Unresolved terms</p>
              <p className="mt-1 text-[11px]" style={{ color: "var(--danger-text-soft)" }}>These name no upstream column; training will fail.</p>
              <div className="ml-2 mt-1.5 grid min-w-0 gap-1.5 border-l pl-3" style={{ borderColor: "var(--danger-border)" }}>{unresolved.map(renderUnresolvedTerm)}</div>
            </div>
          )}
        </div>
      )}
    </section>
  )
}
