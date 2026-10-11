import { AlertTriangle, Plus, SlidersHorizontal, X } from "lucide-react"
import { useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent } from "react"
import { CommittedTextField } from "../components/form"
import useListReorder from "../hooks/useListReorder"
import { INPUT_STYLE } from "../panels/editors/_shared"
import useWorkbenchFormStore from "../stores/useWorkbenchFormStore"
import useWorkbenchViewStore, { activePage, shownPage } from "../stores/useWorkbenchViewStore"
import { addPage, createPage, movePage, pageProblems, removePage, renamePage, showWhenText } from "../utils/workbenchForm"
import { ShowWhenMark } from "./ShowWhenEditor"
import { useSheetVisibility } from "./useSheetValues"

/** Move the sheet at `from` in the tabs' order to `to`, the sheet showing staying the one that shows. */
function moveSheet(from: number, to: number): void {
  const { form, change } = useWorkbenchFormStore.getState()
  const view = useWorkbenchViewStore.getState()
  if (form === null) throw new Error("The sheet tabs need the form loaded")
  // While no sheet has been chosen the first one shows, and the move may put another first.
  const showing = activePage(form, view.pageId)
  if (view.pageId !== showing.id) view.showPage(showing.id)
  change((spec) => movePage(spec, from, to))
}

/**
 * The sheets' tabs along the top (specs/workbench): one per sheet, the showing one
 * marked; double-click a tab to rename it; drag a tab onto another to move its sheet
 * there, or press Alt+Left or Alt+Right on it; a plus adds a sheet; the showing sheet's
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
  const dragFor = useListReorder(moveSheet)
  // A tab moved by the keyboard is put back in the focus, as its button may be moved in
  // the page to reorder the tabs, which loses the focus.
  const tabs = useRef(new Map<string, HTMLButtonElement>())
  const refocus = useRef<string | null>(null)
  useLayoutEffect(() => {
    const moved = refocus.current
    if (moved === null) return
    refocus.current = null
    tabs.current.get(moved)?.focus()
  })
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
  // F2 renames the tab's sheet; Alt+Left and Alt+Right move it a place, never past an end.
  const onTabKeyDown = (event: KeyboardEvent<HTMLButtonElement>, sheetId: string, index: number) => {
    if (event.key === "F2") {
      setRenaming(sheetId)
      return
    }
    if (!event.altKey || (event.key !== "ArrowLeft" && event.key !== "ArrowRight")) return
    event.preventDefault()
    const to = index + (event.key === "ArrowLeft" ? -1 : 1)
    if (to < 0 || to >= form.pages.length) return
    refocus.current = sheetId
    moveSheet(index, to)
  }

  return (
    <div role="tablist" aria-label="Sheets" className="flex items-center gap-0.5" style={{ borderBottom: "1px solid var(--border)" }}>
      {sheets.map((sheet, index) => {
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
        // In the form's order while building, so a tab's place is its sheet's.
        const drag = readOnly ? null : dragFor(index)
        return (
          <div
            key={sheet.id}
            data-testid={`sheet-tab-${sheet.id}`}
            draggable={drag !== null}
            onDragStart={drag?.onStart}
            onDragOver={drag?.onOver}
            onDrop={drag?.onDrop}
            onDragEnd={drag?.onEnd}
            data-drop-target={drag?.target ? "true" : undefined}
            className="flex items-center"
            style={{ opacity: drag?.dragging ? 0.5 : undefined, boxShadow: drag?.target ? "inset 2px 0 0 0 var(--accent)" : undefined }}
          >
            <button
              ref={(element) => {
                if (element === null) tabs.current.delete(sheet.id)
                else tabs.current.set(sheet.id, element)
              }}
              type="button"
              role="tab"
              aria-selected={active}
              aria-keyshortcuts={readOnly ? undefined : "F2 Alt+ArrowLeft Alt+ArrowRight"}
              onClick={() => showPage(sheet.id)}
              onDoubleClick={readOnly ? undefined : () => setRenaming(sheet.id)}
              onKeyDown={readOnly ? undefined : (event) => onTabKeyDown(event, sheet.id, index)}
              title={readOnly ? undefined : "Double-click, or F2, to rename; drag, or Alt+Left and Alt+Right, to move"}
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
