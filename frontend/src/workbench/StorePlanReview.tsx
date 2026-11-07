import type { WorkbenchStoreCompareResponse } from "../api/types"
import useStoreCompareStore, { currentComparison } from "../stores/useStoreCompareStore"
import useWorkbenchFormStore from "../stores/useWorkbenchFormStore"
import DatabaseDisclosure from "./DatabaseDisclosure"

type Change = WorkbenchStoreCompareResponse["changes"][number]
const additionsOf = (answer: WorkbenchStoreCompareResponse) => answer.changes.filter((change) => change.kind === "add column" || change.kind === "create table")

/** A compact inventory of what Export SQL will add. */
export function DatabaseChanges() {
  const form = useWorkbenchFormStore((s) => s.form)
  const state = useStoreCompareStore()
  const answer = currentComparison(state, form)
  if (answer === null) return null
  const additions = additionsOf(answer)
  const columns = additions.filter((change) => change.kind === "add column").length
  const tables = additions.length - columns
  const count = [columns > 0 ? `${columns} ${columns === 1 ? "column" : "columns"}` : "", tables > 0 ? `${tables} ${tables === 1 ? "table" : "tables"}` : ""].filter(Boolean).join(" · ")
  return <DatabaseDisclosure title={`Database changes · ${count || "No additions"}`} className="text-[11px] text-[var(--text-secondary)]">
    <StorePlanReview answer={answer} />
  </DatabaseDisclosure>
}

export default function StorePlanReview({ answer }: { answer: WorkbenchStoreCompareResponse }) {
  const groups = new Map<string, Change[]>()
  for (const change of additionsOf(answer)) {
    const group = groups.get(change.table)
    if (group) group.push(change)
    else groups.set(change.table, [change])
  }
  return <div className="flex flex-col gap-3 py-1" data-testid="store-plan-review">
    {groups.size === 0 && <p>No missing tables or columns.</p>}
    {[...groups].map(([table, changes]) => {
      const created = changes.find((change) => change.kind === "create table")
      return <section key={table} aria-label={`Additions for ${table}`}>
        <div className="flex items-baseline gap-2">
          <span className="break-all font-medium text-[var(--text-primary)]">{table}</span>
          {created && <span className="shrink-0 text-[var(--text-muted)]">New table</span>}
        </div>
        {created?.disposition === "manual" && <p role="alert" className="mt-1 break-words text-[var(--warning)]">{created.description}</p>}
        {changes.some((change) => change.column !== null) && <ul className="mt-1 grid gap-1 pl-3">
          {changes.filter((change) => change.column !== null).map((change) => <li key={change.column}>
            <code className="break-all">{change.column}</code>
            {change.disposition === "manual" && <p role="alert" className="mt-1 break-words text-[var(--warning)]">{change.description}</p>}
          </li>)}
        </ul>}
      </section>
    })}
  </div>
}
