/** An Import run on a sheet (specs/workbench, Running an Import): by its button, or by its lookup beside a field's cell. */
import { type ReactNode, useLayoutEffect, useMemo, useRef, useState } from "react"
import { apiErrorMessage } from "../../api/errors"
import type { FormSpec, ImportWidget } from "../../api/types"
import { useRequestKind } from "../../hooks/useRequestKind"
import { confirmed } from "../../stores/useQuestionStore"
import useToastStore from "../../stores/useToastStore"
import useWorkbenchFormStore from "../../stores/useWorkbenchFormStore"
import { answer, under } from "../../utils/answers"
import { landedRows, replacesTyped } from "../../utils/sheetValues"
import { columnKey } from "../../utils/sheetVisibility"
import { allWidgets, widgetProblems } from "../../utils/workbenchForm"
import { useSheetValues } from "../useSheetValues"

/** Whether an Import reads a file the underwriter picks. */
const readsFile = (widget: ImportWidget): boolean => widget.source.format === "csv" || widget.source.format === "excel"

/** The files a file Import's picker offers. */
const FILE_TYPES: Record<"csv" | "excel", string> = { csv: ".csv,text/csv", excel: ".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" }

/**
 * An Import run where it is drawn, by its button or its lookup: runnable while it has no
 * problem, the sheet's values may change (`importRows`) and no run of it is unanswered. A
 * file Import first asks for the file (`picker`, drawn beside what runs it). Its rows land
 * on its table as typing does (`landedRows`) on the values as they are once it answers, a
 * Replace over filled rows asking first; a refusal shows as an error toast.
 */
export function useImportRun(widget: ImportWidget, problem: string | null): { disabled: boolean; run: () => void; picker: ReactNode } {
  const values = useSheetValues()
  // The values as they stand when the answer comes, not as they stood at the press.
  const current = useRef(values)
  useLayoutEffect(() => {
    current.current = values
  })
  const [pressed, setPressed] = useState(false)
  const picking = useRef<HTMLInputElement>(null)
  // A run answers while what ran it shows.
  const shown = useRequestKind()
  const disabled = problem !== null || values.importRows === null || pressed
  const land = async (file: File | null) => {
    const { importRows } = current.current
    if (importRows === null) return
    await answer([under(shown)], () => importRows(widget.id, file), {
      ok: async (imported) => {
        const table = useWorkbenchFormStore.getState().form?.schema.tables.find((candidate) => candidate.name === imported.table)
        if (table === undefined) return
        if (replacesTyped(table, current.current.rows[table.name] ?? [], imported)) {
          const label = widget.label.trim() || "The Import"
          const yes = await confirmed({
            title: `Replace the rows of ${table.name}?`,
            text: `${label} read ${imported.rows.length === 1 ? "1 row" : `${imported.rows.length} rows`}, which take the place of those typed.`,
            confirm: "Replace",
            danger: true,
          })
          if (!yes) return
        }
        current.current.setRows({ [table.name]: landedRows(table, current.current.rows[table.name] ?? [], imported) })
      },
      failed: (error) => useToastStore.getState().addToast("error", apiErrorMessage(error)),
    }, { pending: setPressed })
  }
  const run = () => {
    if (disabled) return
    if (readsFile(widget)) picking.current?.click()
    else void land(null)
  }
  const picker =
    widget.source.format === "csv" || widget.source.format === "excel" ? (
      <input
        ref={picking}
        type="file"
        hidden
        accept={FILE_TYPES[widget.source.format]}
        data-testid={`import-file-${widget.id}`}
        onChange={(event) => {
          const file = event.target.files?.[0] ?? null
          // The same file picked again is a change too.
          event.target.value = ""
          if (file !== null) void land(file)
        }}
      />
    ) : null
  return { disabled, run, picker }
}

/** Each Import's lookup by the `columnKey` of the field it is offered beside, with its problem, if any. */
export type Lookups = ReadonlyMap<string, readonly { widget: ImportWidget; problem: string | null }[]>

const lookupsOf = new WeakMap<FormSpec, Lookups>()

/** The Imports of `form` offered beside a field's cell (an Import's `lookup`), the same for the same form. */
export function formLookups(form: FormSpec): Lookups {
  let found = lookupsOf.get(form)
  if (found === undefined) {
    const imports = allWidgets(form).filter((widget): widget is ImportWidget => widget.type === "import" && widget.lookup !== null)
    const problems = imports.length === 0 ? [] : widgetProblems(form)
    const byField = new Map<string, { widget: ImportWidget; problem: string | null }[]>()
    for (const widget of imports) {
      if (widget.lookup === null) continue
      const key = columnKey(widget.lookup.table, widget.lookup.column)
      const problem = problems.find((each) => each.widgetId === widget.id)?.message ?? null
      byField.set(key, [...(byField.get(key) ?? []), { widget, problem }])
    }
    found = byField
    lookupsOf.set(form, found)
  }
  return found
}

const NO_LOOKUPS: Lookups = new Map()

/**
 * The Imports offered beside the fields of `keys` (each a `columnKey`) while `active`, the
 * same object while what is offered beside them is the same: a change to the form that
 * offers nothing new, as a drag makes, redraws no component showing fields.
 */
export function useLookups(keys: readonly string[], active: boolean): Lookups {
  // What is drawn of each: its Import's id, label and format, and its problem.
  const signature = useWorkbenchFormStore((s) => {
    if (!active || s.form === null) return ""
    const all = formLookups(s.form)
    const offered = keys.flatMap((key) => (all.get(key) ?? []).map(({ widget, problem }) => [key, widget.id, widget.label, widget.source.format, problem]))
    return offered.length === 0 ? "" : JSON.stringify(offered)
  })
  return useMemo(() => {
    const form = useWorkbenchFormStore.getState().form
    if (signature === "" || form === null) return NO_LOOKUPS
    const all = formLookups(form)
    return new Map(keys.flatMap((key) => (all.has(key) ? [[key, all.get(key) ?? []] as const] : [])))
    // The signature says what is offered; the keys and the form are read as they are then.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signature])
}
