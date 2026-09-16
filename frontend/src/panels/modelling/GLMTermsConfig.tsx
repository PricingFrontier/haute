import { useEffect, useMemo, useRef, useState } from "react"
import { Plus, Search } from "lucide-react"

import type { OnUpdateConfig } from "../editors/_shared"
import { configField } from "../../utils/configField"
import { roleColumns, type ModellingColumn } from "./featureSelection"
import {
  addTerm,
  fitAllWithDefaults,
  modelMembership,
  removeTerm,
  renameExpression,
  setExpression,
  setTermField,
  switchNativeType,
  termsByColumn,
  type InteractionSpec,
  type NativeTermType,
  type Terms,
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
      setJsonError(null)
      writeTerms(parsed as Terms)
    } catch (error) {
      setJsonError((error as Error).message)
    }
  }

  const renderTermCards = (column: ModellingColumn) =>
    (byColumn.get(column.name) ?? []).map(({ key, spec }) =>
      spec.type === "expression" ? (
        <TermCard
          key={key}
          kind="expression"
          termKey={key}
          spec={spec}
          onRename={(nextKey) => {
            const result = renameExpression(terms, key, nextKey, eligibleNames)
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
      ) : (
        <TermCard
          key={key}
          kind="native"
          column={column.name}
          spec={spec}
          onChangeType={(nextType: NativeTermType) => writeTerms(switchNativeType(terms, column.name, nextType))}
          onChangeField={(field, value) => writeTerms(setTermField(terms, column.name, field, value))}
          onRemove={() => writeTerms(removeTerm(terms, column.name))}
        />
      ),
    )

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
            const tag = cards.length > 0 ? null : membership.interactionOnly.has(column.name) ? "Interaction only" : "Not in model"
            return (
              <div key={column.name} role="group" aria-label={`${column.name} feature`} className="rounded-lg px-2 py-1.5" style={{ background: "var(--bg-elevated)", border: "1px solid var(--border)" }}>
                <div className="flex min-w-0 items-center gap-1.5">
                  <span className="min-w-0 flex-1 truncate font-mono text-[11px] font-semibold" title={column.name} style={{ color: "var(--text-primary)" }}>
                    {column.name}
                  </span>
                  <span className="max-w-20 shrink-0 truncate rounded-full px-1.5 py-0.5 font-mono text-[9px]" title={column.dtype} style={{ background: "var(--chrome-hover)", color: "var(--text-secondary)" }}>
                    {column.dtype}
                  </span>
                  {tag && <span className="text-[10px]" style={{ color: "var(--text-muted)" }}>{tag}</span>}
                  <button type="button" aria-label={`Add ${column.name} term`} className="flex items-center gap-1 rounded-lg px-2 py-1 text-[10px] font-medium" style={toggleButtonStyle(false)} onClick={() => writeTerms(addTerm(terms, column.name, column.dtype, eligibleNames))}>
                    <Plus size={10} aria-hidden="true" /> Add term
                  </button>
                </div>
                {cards.length > 0 && <div className="mt-1.5 grid gap-1">{cards}</div>}
              </div>
            )
          })}
          {visible.length === 0 && (
            <p className="rounded-lg border border-dashed px-3 py-5 text-center text-[10px]" style={{ color: "var(--text-muted)", borderColor: "var(--border)" }}>
              No matching feature columns.
            </p>
          )}
          {unresolved.length > 0 && (
            <div role="group" aria-label="Unresolved expressions" className="rounded-lg px-2 py-1.5" style={{ background: "var(--danger-soft-subtle)", border: "1px solid var(--danger-border)" }}>
              <div className="flex items-center justify-between">
                <span className="text-[11px] font-semibold" style={{ color: "var(--danger-text-soft)" }}>Unresolved expressions</span>
                <span className="text-[10px]" style={{ color: "var(--danger-text-soft)" }}>These name no upstream column; training will fail.</span>
              </div>
              <div className="mt-1.5 grid gap-1">
                {unresolved.map(({ key, spec }) => (
                  <TermCard
                    key={key}
                    kind="expression"
                    termKey={key}
                    spec={spec}
                    onRename={(nextKey) => {
                      const result = renameExpression(terms, key, nextKey, eligibleNames)
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
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </section>
  )
}
