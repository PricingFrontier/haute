import type { WorkbenchStoreCompareResponse } from "../api/types"
import { useDatabaseComparison } from "../stores/useStoreCompareStore"
import DatabaseDisclosure from "./DatabaseDisclosure"

type Change = WorkbenchStoreCompareResponse["changes"][number]

/** What each change Export SQL makes to an existing column is marked with in the list. */
const MARKS: Partial<Record<Change["kind"], string>> = { "retype column": "Type change", "rename key": "Rename", "rekey table": "Key change" }

/** A compact inventory of what Export SQL will add, convert or re-key. */
export function DatabaseChanges() {
  const { answer } = useDatabaseComparison()
  if (answer === null) return null
  const counted = (...kinds: Change["kind"][]) => answer.changes.filter((change) => kinds.includes(change.kind)).length
  const columns = counted("add column")
  const tables = counted("create table")
  const retypes = counted("retype column")
  const keys = counted("rename key", "rekey table")
  const count = [columns > 0 ? `${columns} ${columns === 1 ? "column" : "columns"}` : "", tables > 0 ? `${tables} ${tables === 1 ? "table" : "tables"}` : "",
    retypes > 0 ? `${retypes} type ${retypes === 1 ? "change" : "changes"}` : "",
    keys > 0 ? `${keys} key ${keys === 1 ? "change" : "changes"}` : ""].filter(Boolean).join(" · ")
  return <DatabaseDisclosure title={`Database changes · ${count || "No additions"}`} className="text-[11px] text-[var(--text-secondary)]">
    <StorePlanReview answer={answer} />
  </DatabaseDisclosure>
}

function StorePlanReview({ answer }: { answer: WorkbenchStoreCompareResponse }) {
  const groups = new Map<string, Change[]>()
  for (const change of answer.changes) {
    const group = groups.get(change.table)
    if (group) group.push(change)
    else groups.set(change.table, [change])
  }
  return <div className="flex flex-col gap-3 py-1" data-testid="store-plan-review">
    {groups.size === 0 && <p>No missing tables or columns.</p>}
    {[...groups].map(([table, changes]) => {
      const created = changes.find((change) => change.kind === "create table")
      return <section key={table} aria-label={`Changes for ${table}`}>
        <div className="flex items-baseline gap-2">
          <span className="break-all font-medium text-[var(--text-primary)]">{table}</span>
          {created && <span className="shrink-0 text-[var(--text-muted)]">New table</span>}
        </div>
        {created?.disposition === "manual" && <p role="alert" className="mt-1 break-words text-[var(--warning)]">{created.description}</p>}
        {changes.some((change) => change.column !== null) && <ul className="mt-1 grid gap-1 pl-3">
          {changes.filter((change) => change.column !== null).map((change) => <li key={change.column}>
            <code className="break-all">{change.column}</code>
            {MARKS[change.kind] !== undefined && <span className="ml-2 text-[var(--text-muted)]">{MARKS[change.kind]}</span>}
            {change.disposition === "manual" && <p role="alert" className="mt-1 break-words text-[var(--warning)]">{change.description}</p>}
          </li>)}
        </ul>}
      </section>
    })}
  </div>
}
