import type { WorkbenchStoreCompareResponse } from "../api/types"

/** Describes the server's plan; SQL and release compatibility are never inferred here. */
export default function StorePlanReview({ answer }: { answer: WorkbenchStoreCompareResponse }) {
  const sections = [
    { disposition: "sql", title: "SQL changes" },
    { disposition: "manual", title: "Manual work" },
    { disposition: "retained", title: "Retained in database" },
  ] as const
  return (
    <div className="flex flex-col gap-3" data-testid="store-plan-review">
      {answer.blocked.length > 0 && (
        <div role="alert" style={{ color: "var(--danger-text)" }}>
          <h3 className="font-semibold">Resolve before exporting SQL</h3>
          <ul className="mt-1 list-disc pl-5">{answer.blocked.map((message) => <li key={message}>{message}</li>)}</ul>
        </div>
      )}
      {answer.matches && answer.blocked.length === 0 && (
        <p role="status" style={{ color: "var(--success)" }}>Compatible with this schema{answer.extras.length > 0 ? " · retained columns or tables" : ""}.</p>
      )}
      {answer.requires_maintenance && (
        <p style={{ color: "var(--warning)" }}>
          Coordinate these changes with the app release. A running app may still need the old names or types; its compatibility has not been checked.
        </p>
      )}
      {sections.map(({ disposition, title }) => {
        const changes = answer.changes.filter((change) => change.disposition === disposition)
        const notes = disposition === "manual" ? answer.manual : []
        if (changes.length === 0 && notes.length === 0) return null
        return (
          <section key={disposition} aria-label={title}>
            <h3 className="mb-1 font-semibold">{title}</h3>
            <ul className="flex flex-col gap-2">
              {changes.map((change, index) => (
                <li key={`${change.table}.${change.column}.${change.kind}.${index}`}>
                  <div className="flex flex-wrap items-baseline gap-x-2">
                    <code className="break-all">{change.table}{change.column === null ? "" : `.${change.column}`}</code>
                    {change.quotes !== null && <span style={{ color: "var(--text-secondary)" }}>{change.quotes.toLocaleString()} {change.quotes === 1 ? "quote" : "quotes"} with values</span>}
                  </div>
                  <p style={{ color: "var(--text-secondary)" }}>{change.description}</p>
                </li>
              ))}
              {notes.map((message) => <li key={message}>{message}</li>)}
            </ul>
          </section>
        )
      })}
    </div>
  )
}
