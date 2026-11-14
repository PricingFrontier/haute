import { useCallback, useEffect, useMemo, useRef, useState } from "react"

import { EditorLabel } from "../../../components/form"
import {
  createPivotFormula,
  pivotFormulas,
  pivotValueReference,
} from "../../explore/pivotConfig"
import type {
  ExplorePivotConfig,
  PivotFormulaPlacement,
} from "../../explore/pivotConfig"
import { Input, TextArea } from "../../../haute-ui"

type PivotFormulaSectionProps = {
  pivot: ExplorePivotConfig
  formulas: PivotFormulaPlacement[]
  persistPivot: (pivot: ExplorePivotConfig) => void
  persistFormula: (formula: PivotFormulaPlacement) => void
  /** Asks first; true once the formula is deleted. */
  deleteFormula: (formula: PivotFormulaPlacement) => Promise<boolean>
  onFormulaEditorChange: (inserter: ((field: string) => void) | null) => void
}

type FormulaEditorState =
  | { mode: "new" }
  | { mode: "edit"; formulaId: string }
  | null

export default function PivotFormulaSection({
  pivot,
  formulas,
  persistPivot,
  persistFormula,
  deleteFormula,
  onFormulaEditorChange,
}: PivotFormulaSectionProps) {
  const [editor, setEditor] = useState<FormulaEditorState>(null)
  const [nameDraft, setNameDraft] = useState("")
  const [expressionDraft, setExpressionDraft] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [formulaSearch, setFormulaSearch] = useState("")
  const expressionRef = useRef<HTMLTextAreaElement>(null)
  const selectedFormulas = pivotFormulas(pivot)
  const selectedIds = new Set(selectedFormulas.map((formula) => formula.id))
  const availableFormulas = useMemo(() => {
    const query = formulaSearch.trim().toLocaleLowerCase()
    return formulas.filter((formula) => (
      formula.display_name.toLocaleLowerCase().includes(query)
      || formula.reference.toLocaleLowerCase().includes(query)
    ))
  }, [formulaSearch, formulas])
  const editingFormula = editor?.mode === "edit"
    ? formulas.find((formula) => formula.id === editor.formulaId)
    : undefined
  // An edit whose formula is gone (an undo, a redo) is closed.
  const openEditor = editor?.mode === "edit" && editingFormula === undefined ? null : editor
  const editorOpen = openEditor !== null

  const openNewEditor = () => {
    setEditor({ mode: "new" })
    setNameDraft("")
    setExpressionDraft("")
    setError(null)
  }

  const openEditEditor = (formula: PivotFormulaPlacement) => {
    setEditor({ mode: "edit", formulaId: formula.id })
    setNameDraft(formula.display_name)
    setExpressionDraft(formula.expression)
    setError(null)
  }

  const closeEditor = () => {
    setEditor(null)
    setError(null)
  }

  const saveFormula = () => {
    const displayName = nameDraft.trim()
    const expression = expressionDraft.trim()
    if (!displayName) {
      setError("Formula name cannot be blank.")
      return
    }
    if (!expression) {
      setError("Polars expression cannot be blank.")
      return
    }
    if (editor?.mode === "edit") {
      if (!editingFormula) {
        setError("This calculated field no longer exists.")
        return
      }
      persistFormula({
        ...editingFormula,
        display_name: displayName,
        expression,
      })
    } else {
      persistFormula(createPivotFormula(formulas, displayName, expression))
    }
    closeEditor()
  }

  const insertReference = useCallback((reference: string) => {
    const textarea = expressionRef.current
    const insertion = `pl.col(${JSON.stringify(reference)})`
    const selectionStart = textarea?.selectionStart
    const selectionEnd = textarea?.selectionEnd
    setExpressionDraft((current) => {
      const start = selectionStart ?? current.length
      const end = selectionEnd ?? start
      const next = `${current.slice(0, start)}${insertion}${current.slice(end)}`
      requestAnimationFrame(() => {
        textarea?.focus()
        textarea?.setSelectionRange(start + insertion.length, start + insertion.length)
      })
      return next
    })
  }, [])

  // The field well adds fields to the formula while an editor is open, and
  // returns to adding them to the pivot when it closes, is closed by an undo,
  // or leaves the page.
  const latestOnChange = useRef(onFormulaEditorChange)
  useEffect(() => {
    latestOnChange.current = onFormulaEditorChange
  })
  useEffect(() => {
    if (!editorOpen) return
    latestOnChange.current(insertReference)
    return () => latestOnChange.current(null)
  }, [editorOpen, insertReference])

  return (
    <section data-testid="pivot-formula-section">
      <h4><EditorLabel as="span">Formulas</EditorLabel></h4>
      <label className="mt-1.5 block text-[11px] font-semibold" style={{ color: "var(--text-secondary)" }}>
        Search formulas
        <Input
          fieldSize="large"
          type="search"
          role="searchbox"
          aria-label="Search formulas"
          value={formulaSearch}
          onChange={(event) => setFormulaSearch(event.target.value)}
          className="mt-1 block w-full"
        />
      </label>
      <div
        role="group"
        aria-label="Available formulas"
        className="mt-2 max-h-52 overflow-y-auto rounded-md"
        style={{
          background: "var(--bg-input)",
          border: "1px solid var(--border)",
        }}
      >
        {availableFormulas.length === 0 ? (
          <div
            className="px-2 py-3 text-center text-[10px]"
            style={{ color: "var(--text-muted)" }}
          >
            {formulas.length === 0
              ? "No formulas yet."
              : "No formulas match your search."}
          </div>
        ) : (
          availableFormulas.map((formula) => {
            const selected = selectedIds.has(formula.id)
            const referenceCollision = pivot.values.some(
              (value) => pivotValueReference(value) === formula.reference,
            )
            const cannotAdd = selected || referenceCollision
            return (
              <div
                key={formula.id}
                role="group"
                aria-label={`Calculated field ${formula.display_name}`}
                className="flex min-h-8 flex-wrap items-center gap-x-2 gap-y-1 px-2 py-1 text-[11px]"
                style={{ borderBottom: "1px solid var(--border)" }}
              >
                <span className="min-w-[7rem] flex-1 truncate font-medium">
                  {formula.display_name}
                </span>
                <code
                  title={formula.reference}
                  className="max-w-28 shrink-0 truncate text-[9px]"
                  style={{ color: "var(--text-muted)" }}
                >
                  {formula.reference}
                </code>
                <div className="flex shrink-0 flex-wrap items-center gap-1">
                  <button
                    type="button"
                    aria-label={`Edit formula ${formula.display_name}`}
                    onClick={() => openEditEditor(formula)}
                    className="focus-ring rounded px-1.5 py-0.5 text-[10px] font-semibold"
                    style={{ border: "1px solid var(--border)", color: "var(--text-secondary)" }}
                  >
                    Edit formula
                  </button>
                  <button
                    type="button"
                    aria-label={selected
                      ? `${formula.display_name} is already in Values`
                      : `Add ${formula.display_name} to Values`}
                    disabled={cannotAdd}
                    title={referenceCollision
                      ? `A Value already uses the reference ${formula.reference}.`
                      : undefined}
                    onClick={() => persistPivot({
                      ...pivot,
                      formulas: [...selectedFormulas, formula],
                      value_order: [...pivot.value_order, formula.id],
                    })}
                    className="focus-ring rounded px-1.5 py-0.5 text-[10px] font-semibold disabled:cursor-not-allowed disabled:opacity-40"
                    style={{ border: "1px solid var(--border)", color: "var(--text-secondary)" }}
                  >
                    {selected ? "Added to: Values" : "Add to: Values"}
                  </button>
                </div>
              </div>
            )
          })
        )}
      </div>

      {openEditor && (
        <div
          role="group"
          aria-label={openEditor.mode === "new" ? "New formula" : `Edit ${editingFormula?.display_name ?? "formula"}`}
          className="mt-2 rounded-md border p-2"
          style={{ borderColor: "var(--border)", background: "var(--bg-panel)" }}
        >
          <label className="block text-[10px]" style={{ color: "var(--text-secondary)" }}>
            Formula name
            <Input
              aria-label="Formula name"
              value={nameDraft}
              onChange={(event) => setNameDraft(event.target.value)}
              className="mt-1 block w-full"
            />
          </label>

          <label className="mt-2 block text-[10px]" style={{ color: "var(--text-secondary)" }}>
            Polars expression
            <TextArea
              ref={expressionRef}
              aria-label="Polars expression"
              value={expressionDraft}
              onChange={(event) => setExpressionDraft(event.target.value)}
              rows={3}
              spellCheck={false}
              className="mt-1 block w-full resize-y font-mono"
            />
          </label>

          {editingFormula && (
            <code className="mt-1 block truncate text-[9px]" style={{ color: "var(--text-muted)" }}>
              Output: {editingFormula.reference}
            </code>
          )}
          {error && (
            <div role="alert" className="mt-1 text-[10px]" style={{ color: "var(--danger)" }}>
              {error}
            </div>
          )}
          <div className="mt-2 flex items-center justify-end gap-1.5">
            {editingFormula && (
              <button
                type="button"
                aria-label={`Delete formula ${editingFormula.display_name}`}
                onClick={() =>
                  void (async () => {
                    if (await deleteFormula(editingFormula)) closeEditor()
                  })()
                }
                className="mr-auto rounded px-1.5 py-1 text-[10px]"
                style={{ color: "var(--danger)" }}
              >
                Delete formula
              </button>
            )}
            <button
              type="button"
              onClick={closeEditor}
              className="rounded px-1.5 py-1 text-[10px]"
            >
              Cancel
            </button>
            <button
              type="button"
              aria-label="Save formula"
              onClick={saveFormula}
              className="focus-ring rounded px-2 py-1 text-[10px] font-semibold"
              style={{ border: "1px solid var(--border)" }}
            >
              Save formula
            </button>
          </div>
        </div>
      )}

      <div className="mt-2 flex justify-end">
        <button
          type="button"
          aria-label="Add formula"
          disabled={openEditor !== null}
          onClick={openNewEditor}
          className="focus-ring rounded px-2 py-1 text-[10px] font-semibold disabled:cursor-not-allowed disabled:opacity-40"
          style={{ border: "1px solid var(--border)" }}
        >
          Add formula
        </button>
      </div>
    </section>
  )
}
