import { Plus, X } from "lucide-react"
import { useState } from "react"
import { CommittedTextField } from "../components/form"
import { INPUT_STYLE } from "../panels/editors/_shared"
import useWorkbenchFormStore from "../stores/useWorkbenchFormStore"
import useWorkbenchViewStore, { activePage } from "../stores/useWorkbenchViewStore"
import { addPage, createPage, removePage, renamePage } from "../utils/workbenchForm"

/**
 * The sheets' tabs along the top (specs/workbench): one per sheet, the showing one
 * marked; double-click a tab to rename it; a plus adds a sheet; the showing sheet's cross
 * deletes it, asking first when components are on it, while another sheet remains. Read
 * only, in Preview, the tabs only switch sheets.
 */
export default function PageTabs({ readOnly = false }: { readOnly?: boolean }) {
  const form = useWorkbenchFormStore((s) => s.form)
  const change = useWorkbenchFormStore((s) => s.change)
  const pageId = useWorkbenchViewStore((s) => s.pageId)
  const showPage = useWorkbenchViewStore((s) => s.showPage)
  const [renaming, setRenaming] = useState<string | null>(null)
  if (form === null) throw new Error("The sheet tabs need the form loaded")
  const page = activePage(form, pageId)

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
      {form.pages.map((sheet) => {
        const active = sheet.id === page.id
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
        return (
          <div key={sheet.id} className="flex items-center">
            <button
              type="button"
              role="tab"
              aria-selected={active}
              onClick={() => showPage(sheet.id)}
              onDoubleClick={readOnly ? undefined : () => setRenaming(sheet.id)}
              title={readOnly ? undefined : "Double-click to rename"}
              className="-mb-px border-b-2 px-3 py-2 text-xs"
              style={{
                borderColor: active ? "var(--accent)" : "transparent",
                color: active ? "var(--text-primary)" : "var(--text-secondary)",
              }}
            >
              {sheet.title}
            </button>
            {!readOnly && active && form.pages.length > 1 && (
              <button
                type="button"
                onClick={deleteSheet}
                aria-label={`Delete sheet ${sheet.title}`}
                title="Delete this sheet"
                className="icon-danger-btn focus-ring -ml-1 rounded p-0.5"
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
