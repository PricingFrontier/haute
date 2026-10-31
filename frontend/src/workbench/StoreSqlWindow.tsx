import { useEffect, useState } from "react"
import { apiErrorMessage } from "../api/errors"
import type { FormSpec, WorkbenchStoreSqlResponse } from "../api/types"
import { fetchStoreSql } from "../api/workbench"
import ModalShell from "../components/ModalShell"
import { ModalFormHeader } from "../components/ModalForm"
import useWorkbenchFormStore from "../stores/useWorkbenchFormStore"

type Start = "branch" | "none"

type Shown =
  | { status: "loading" }
  | { status: "failed"; message: string }
  | { status: "ready"; answer: WorkbenchStoreSqlResponse }

const QUIET_STYLE = { color: "var(--text-muted)" } as const

/**
 * The tables' SQL (specs/workbench): the SQL that brings each hosted environment's tables to
 * the form the view holds, saved or not, as the server writes it, a tab for each environment
 * `haute.toml` declares, starting from the form the default branch holds while it holds one,
 * else from none; read only, with Copy. It changes nothing.
 */
export default function StoreSqlWindow({ onClose }: { onClose: () => void }) {
  const form = useWorkbenchFormStore((s) => s.form)
  const [start, setStart] = useState<Start>("branch")
  // The last answer, with what it was asked for: loading until it is the one asked now.
  const [answered, setAnswered] = useState<{ form: FormSpec; start: Start; shown: Shown } | null>(null)
  const [tab, setTab] = useState(0)
  // The default branch holding a form, as the first answer named it: the start offered.
  const [branch, setBranch] = useState<string | null>(null)

  useEffect(() => {
    if (form === null) return
    let live = true
    fetchStoreSql(form, start).then(
      (answer) => {
        if (!live) return
        if (start === "branch") setBranch(answer.branch)
        setAnswered({ form, start, shown: { status: "ready", answer } })
      },
      (error: unknown) => {
        if (live) setAnswered({ form, start, shown: { status: "failed", message: apiErrorMessage(error) } })
      },
    )
    return () => {
      live = false
    }
  }, [form, start])

  const shown: Shown =
    answered !== null && answered.form === form && answered.start === start ? answered.shown : { status: "loading" }
  const answer = shown.status === "ready" ? shown.answer : null
  const environment = answer?.environments[Math.min(tab, answer.environments.length - 1)] ?? null
  return (
    <ModalShell ariaLabel="SQL" onClose={onClose} width="w-[760px]" testId="store-sql-window">
      <ModalFormHeader title="SQL" />
      <div className="flex flex-col gap-3 px-4 py-3 text-[12px]">
        {shown.status === "loading" ? (
          <div role="status" style={QUIET_STYLE}>
            Writing the SQL…
          </div>
        ) : shown.status === "failed" ? (
          <div role="alert" style={{ color: "var(--danger-text)" }}>
            {shown.message}
          </div>
        ) : answer !== null && answer.environments.length === 0 ? (
          <div role="status" style={QUIET_STYLE}>
            No hosted environments are declared.
          </div>
        ) : (
          answer !== null &&
          environment !== null && (
            <>
              <div className="flex flex-wrap items-center gap-3">
                <div role="tablist" aria-label="Environments" className="flex gap-1">
                  {answer.environments.map((each, index) => (
                    <button
                      key={each.name}
                      type="button"
                      role="tab"
                      aria-selected={each.name === environment.name}
                      className="btn-ghost rounded px-2 py-1"
                      style={each.name === environment.name ? { background: "var(--bg-hover)" } : undefined}
                      onClick={() => setTab(index)}
                    >
                      {each.name} <span style={QUIET_STYLE}>{each.provider}</span>
                    </button>
                  ))}
                </div>
                <label className="ml-auto flex items-center gap-1.5">
                  <span style={QUIET_STYLE}>From</span>
                  <select
                    value={start === "branch" && branch !== null ? "branch" : "none"}
                    onChange={(event) => setStart(event.target.value as Start)}
                    className="rounded border px-1.5 py-0.5"
                    style={{ background: "var(--bg-input)", borderColor: "var(--border)" }}
                  >
                    {branch !== null && <option value="branch">{branch}</option>}
                    <option value="none">A new database</option>
                  </select>
                </label>
                <button
                  type="button"
                  className="btn-ghost rounded px-2 py-1"
                  onClick={() => void navigator.clipboard.writeText(environment.sql)}
                >
                  Copy
                </button>
              </div>
              <pre
                data-testid="store-sql"
                className="max-h-[60vh] overflow-auto rounded p-3 font-mono text-[11px] leading-relaxed"
                style={{ background: "var(--bg-base)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
              >
                {environment.sql}
              </pre>
            </>
          )
        )}
      </div>
    </ModalShell>
  )
}
