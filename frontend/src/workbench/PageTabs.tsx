import { AlertTriangle, Plus, SlidersHorizontal, X } from "lucide-react"
import { useMemo, useState } from "react"
import { CommittedTextField } from "../components/form"
import { INPUT_STYLE } from "../panels/editors/_shared"
import useWorkbenchFormStore from "../stores/useWorkbenchFormStore"
import useWorkbenchViewStore, { activePage, shownPage } from "../stores/useWorkbenchViewStore"
import { addPage, createPage, pageProblems, removePage, renamePage, showWhenText } from "../utils/workbenchForm"
import { ShowWhenMark } from "./ShowWhenEditor"
import { useSheetVisibility } from "./useSheetValues"

/**
 * The sheets' tabs along the top (specs/workbench): one per sheet, the showing one
 * marked; double-click a tab to rename it; a plus adds a sheet; the showing sheet's
 * settings mark opens its own panel and its cross deletes it, asking first when
 * components are on it, while another sheet remains. A sheet with conditions carries an
 * eye, struck through and its tab faded while the sample hides it, and a sheet whose
 * conditions have a problem a warning mark naming it. Read only, in Preview, the tabs only
 * switch sheets, and only between the sheets the quote shows.
 */
export default function PageTabs({ readOnly = false }: { readOnly?: boolean }) {
  const form = useWorkbenchFormStore((s) => s.form)
  const change = useWorkbenchFormStore((s) => s.change)
  const pageId = useWorkbenchViewStore((s) => s.pageId)
  const showPage = useWorkbenchViewStore((s) => s.showPage)
  const selectSheet = useWorkbenchViewStore((s) => s.selectSheet)
  const visibility = useSheetVisibility(readOnly)
  const [renaming, setRenaming] = useState<string | null>(null)
  const problems = useMemo(() => {
    const byPage = new Map<string, string>()
    if (form !== null) for (const problem of pageProblems(form)) if (!byPage.has(problem.pageId)) byPage.set(problem.pageId, problem.message)
    return byPage
  }, [form])
  if (form === null) throw new Error("The sheet tabs need the form loaded")
  const page = activePage(form, pageId)
  const showing = readOnly ? shownPage(form, pageId, visibility.pages) : page
  const sheets = readOnly ? form.pages.filter((sheet) => visibility.pages.has(sheet.id)) : form.pages

  const addSheet = () => {
    const sheet = createPage(form)
    change((spec) => addPage(spec, sheet))
    showPage(sheet.id)
  }
  const deleteSheet = () => {
    const count = page.widgets.length
    if (
      count > 0
      && !window.confirm(
        `Delete "${page.title}" and the ${count} ${count === 1 ? "component" : "components"} on it? The schema and the sample stay.`,
      )
    ) return
    const remaining = form.pages.find((sheet) => sheet.id !== page.id)
    if (remaining === undefined) return
    change((spec) => removePage(spec, page.id))
    showPage(remaining.id)
  }

  return (
    <div role="tablist" aria-label="Sheets" className="flex items-center gap-0.5" style={{ borderBottom: "1px solid var(--border)" }}>
      {sheets.map((sheet) => {
        const active = sheet.id === showing?.id
        if (renaming === sheet.id) {
          return (
            <CommittedTextField
              key={sheet.id}
              autoFocus
              aria-label="Sheet name"
              value={sheet.title}
              onCommit={(title) => {
                const trimmed = title.trim()
                if (trimmed) change((spec) => renamePage(spec, sheet.id, trimmed))
              }}
              onBlur={() => setRenaming(null)}
              onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === "Escape") setRenaming(null)
              }}
              className="focus-ring w-32 rounded-md px-2 py-1 text-xs"
              style={INPUT_STYLE}
            />
          )
        }
        const hidden = !visibility.pages.has(sheet.id)
        const problem = readOnly ? undefined : problems.get(sheet.id)
        return (
          <div key={sheet.id} className="flex items-center">
            <button
              type="button"
              role="tab"
              aria-selected={active}
              onClick={() => showPage(sheet.id)}
              onDoubleClick={readOnly ? undefined : () => setRenaming(sheet.id)}
              onKeyDown={readOnly ? undefined : (event) => { if (event.key === "F2") setRenaming(sheet.id) }}
              title={readOnly ? undefined : "Double-click, or F2, to rename"}
              data-hidden={hidden ? "true" : undefined}
              className="-mb-px flex items-center gap-1 border-b-2 px-3 py-2 text-xs"
              style={{
                borderColor: active ? "var(--accent)" : "transparent",
                color: active ? "var(--text-primary)" : "var(--text-secondary)",
                opacity: hidden ? 0.5 : undefined,
              }}
            >
              {!readOnly && sheet.show_when.conditions.length > 0 && (
                <ShowWhenMark hidden={hidden} title={showWhenText(form, sheet.show_when)} />
              )}
              {sheet.title}
            </button>
            {problem !== undefined && (
              <span role="img" aria-label={problem} title={problem} className="-ml-2 mr-1 flex" style={{ color: "var(--warning)" }}>
                <AlertTriangle size={11} aria-hidden="true" />
              </span>
            )}
            {!readOnly && active && (
              <button
                type="button"
                onClick={selectSheet}
                aria-label={`Settings of sheet ${sheet.title}`}
                title="Sheet settings: its title and when it shows"
                className="focus-ring hover-bg -ml-1 rounded p-0.5"
                style={{ color: "var(--text-muted)" }}
              >
                <SlidersHorizontal size={11} aria-hidden="true" />
              </button>
            )}
            {!readOnly && active && form.pages.length > 1 && (
              <button
                type="button"
                onClick={deleteSheet}
                aria-label={`Delete sheet ${sheet.title}`}
                title="Delete this sheet"
                className="icon-danger-btn focus-ring rounded p-0.5"
              >
                <X size={11} aria-hidden="true" />
              </button>
            )}
          </div>
        )
      })}
      {!readOnly && (
        <button
          type="button"
          onClick={addSheet}
          aria-label="Add a sheet"
          title="Add a sheet"
          className="focus-ring hover-bg rounded p-1.5"
          style={{ color: "var(--text-secondary)" }}
        >
          <Plus size={14} aria-hidden="true" />
        </button>
      )}
    </div>
  )
}
