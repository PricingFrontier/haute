import { AlertTriangle, Plus, SlidersHorizontal } from "lucide-react"
import { useMemo, useState, type KeyboardEvent } from "react"
import { CommittedTextField, INPUT_STYLE } from "../components/form"
import useListReorder from "../hooks/useListReorder"
import useRefocusAfterMove, { altArrowMove } from "../hooks/useRefocusAfterMove"
import useWorkbenchFormStore from "../stores/useWorkbenchFormStore"
import useWorkbenchViewStore, { activePage, shownPage } from "../stores/useWorkbenchViewStore"
import { addPage, createPage, movePage, pageProblems, renamePage, showWhenText } from "../utils/workbenchForm"
import { ShowWhenMark } from "./ShowWhenEditor"
import { useSheetVisibility } from "./useSheetValues"

/** Move the sheet at `from` in the tabs' order to `to`, the sheet showing staying the one that shows. */
function moveSheet(from: number, to: number): void {
  const { form, change } = useWorkbenchFormStore.getState()
  const view = useWorkbenchViewStore.getState()
  if (form === null) throw new Error("The sheet tabs need the form loaded")
  // While no sheet has been chosen the first one shows, and the move may put another first:
  // the sheet is pinned, the selection left as it is.
  const showing = activePage(form, view.pageId)
  if (view.pageId !== showing.id) useWorkbenchViewStore.setState({ pageId: showing.id })
  change((spec) => movePage(spec, from, to))
}

/**
 * The sheets' tabs along the top (specs/workbench): one per sheet, the showing one
 * marked and the one Tab stops at, Left and Right going to the tab before or after, round
 * the ends, and Home and End to the first and the last, each showing its sheet;
 * double-click a tab, or press F2 on it, to rename it, the tab taking the focus back once
 * Enter or Escape ends the renaming; drag a tab onto another to move its sheet
 * there, or press Alt+Left or Alt+Right on it; a plus adds a sheet; the showing sheet's
 * settings mark opens its own panel, which deletes it, a tab having no delete of its own.
 * A sheet with conditions carries an
 * eye, struck through and its tab faded while a new quote hides it, and a sheet whose
 * conditions have a problem a warning mark naming it. Read only, in Preview, the tabs only
 * switch sheets, and only between the sheets the values show.
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
  const tabs = useRefocusAfterMove<HTMLButtonElement>()
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
  // Left and Right go to the tab before or after, round the ends, and Home and End to the
  // first and the last, showing its sheet. While building, F2 renames the tab's sheet, and
  // Alt+Left and Alt+Right move it a place, never past an end.
  const onTabKeyDown = (event: KeyboardEvent<HTMLButtonElement>, sheetId: string, index: number) => {
    if (event.altKey) {
      const to = readOnly ? null : altArrowMove(event, "horizontal", index, form.pages.length)
      if (to === null) return
      tabs.moved(sheetId)
      moveSheet(index, to)
      return
    }
    if (event.key === "F2" && !readOnly) {
      setRenaming(sheetId)
      return
    }
    const place =
      event.key === "ArrowLeft" ? index - 1 : event.key === "ArrowRight" ? index + 1 : event.key === "Home" ? 0 : event.key === "End" ? sheets.length - 1 : null
    if (place === null) return
    event.preventDefault()
    const next = sheets[(place + sheets.length) % sheets.length]
    tabs.element(next.id)?.focus()
    showPage(next.id)
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
                if (event.key !== "Enter" && event.key !== "Escape") return
                // The tab takes the focus back as the field goes.
                tabs.moved(sheet.id)
                setRenaming(null)
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
              ref={tabs.register(sheet.id)}
              type="button"
              role="tab"
              aria-selected={active}
              tabIndex={active ? 0 : -1}
              aria-keyshortcuts={readOnly ? undefined : "F2 Alt+ArrowLeft Alt+ArrowRight"}
              onClick={() => showPage(sheet.id)}
              onDoubleClick={readOnly ? undefined : () => setRenaming(sheet.id)}
              onKeyDown={(event) => onTabKeyDown(event, sheet.id, index)}
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
                title="Sheet settings"
                className="focus-ring hover-bg -ml-1 rounded p-0.5"
                style={{ color: "var(--text-muted)" }}
              >
                <SlidersHorizontal size={11} aria-hidden="true" />
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
