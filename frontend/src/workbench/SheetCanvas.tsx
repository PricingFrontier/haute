import { useLayoutEffect, useMemo, useRef, type ReactNode } from "react"
import type { Page, Section } from "../api/types"
import useRefocusAfterMove from "../hooks/useRefocusAfterMove"
import useWorkbenchFormStore from "../stores/useWorkbenchFormStore"
import useWorkbenchViewStore, { activePage, previewSections, shownPage } from "../stores/useWorkbenchViewStore"
import { EDGE_HANDLE, GRID, readingOrder, SECTION_HEADER, SHEET_PADDING, sheetHeight, stackBottom, stackSections } from "../utils/sheetGeometry"
import { pageWidgets, sectionProblems, showWhenText, widgetProblems } from "../utils/workbenchForm"
import { SectionBar, SectionMoveLine } from "./SectionHeaders"
import { dropAt, leaveField, sectionDropAt, startSheetResize } from "./sheetInteractions"
import SheetWidget from "./SheetWidget"
import { useHeldWhilePressed, useKeptPlace } from "./steadyPreview"
import { SheetValuesContext, useNewQuoteValues, useQuoteValues, useSampleValues } from "./useSheetValues"
import { paletteKind } from "./widgetKinds"

const NO_SECTIONS: readonly Section[] = []

/**
 * The sheet showing, in a scrolling viewport (specs/workbench): a snap grid as wide as the
 * sheet's width at the zoom, from the viewport's top-left corner, so it stays beside the
 * palette and under the tabs whatever the zoom, and as long as its sections, stacked one
 * under the next,
 * each holding its components, each a `SheetWidget` given the sheet's values (a new
 * quote while building; in Preview the sample, or the underwriter app's quote), with,
 * while building, a ghost where a component dragged out of the palette would land, or a
 * line where a section would start. While building every section and component is drawn,
 * those a new quote hides faded, each section under its bar (`SectionBar`), drawn first in
 * it, and while the sheet's own panel is open its handles (`SheetHandles`); in
 * Preview only the sections and components the values show, on
 * the first sheet that shows when the chosen one does not, the sections that show closing
 * up the space of those that do not, held while a pointer is pressed on the sheet and kept
 * in the underwriter's view as they move. The components are drawn in the order they are
 * read, section by section and, within each, row by row and left to right along each row
 * (`readingOrder`), so a screen reader and Tab follow the layout, each section's bar before
 * its components while building, on the sheet's background
 * (the form's `theme`, else the editor's own), and stacked in the form's
 * order, so where two overlap the one added later covers the other (a copy its original),
 * the selected one covers both and the ghost covers all. Pressing the empty sheet
 * deselects, a field being typed into in the panel committing first. The sheet and its
 * viewport are registered with the view store, which places drops on them and fits the
 * zoom to them, the viewport scrolling over no less than the sheet as pressed while one of
 * its edges or a section's is dragged (`heldSize`); what is on the sheet is fitted to the
 * viewport when the sheets first show for a form, and a zoom chosen since is kept across a
 * trip to the schema or the pipeline editor. Only the values in use are made, and a
 * component is drawn again only when what it shows changes, so a drag redraws what it moves.
 */
export default function SheetCanvas() {
  const form = useWorkbenchFormStore((s) => s.form)
  const pageId = useWorkbenchViewStore((s) => s.pageId)
  const previewing = useWorkbenchViewStore((s) => s.section === "preview")
  const underwriterApp = useWorkbenchViewStore((s) => s.underwriterApp)
  const zoom = useWorkbenchViewStore((s) => s.zoom)
  const selectedId = useWorkbenchViewStore((s) => s.selectedId)
  const selectedSection = useWorkbenchViewStore((s) => s.sectionId)
  const sheetSelected = useWorkbenchViewStore((s) => s.sheetSelected)
  const heldSize = useWorkbenchViewStore((s) => s.heldSize)
  const select = useWorkbenchViewStore((s) => s.select)
  const setSheet = useWorkbenchViewStore((s) => s.setSheet)
  const setViewport = useWorkbenchViewStore((s) => s.setViewport)
  const viewport = useWorkbenchViewStore((s) => s.viewport)
  const fitZoom = useWorkbenchViewStore((s) => s.fitZoom)
  const fitted = useWorkbenchViewStore((s) => s.fitted)
  const newQuote = useNewQuoteValues(!previewing)
  const sample = useSampleValues(previewing && !underwriterApp)
  const quote = useQuoteValues(previewing && underwriterApp)
  // The bars' focus, kept through a section moved by the keyboard.
  const bars = useRefocusAfterMove<HTMLButtonElement>()
  const viewportRef = useRef<HTMLDivElement>(null)
  useLayoutEffect(() => {
    setViewport(viewportRef.current)
    return () => setViewport(null)
  }, [setViewport])
  useLayoutEffect(() => {
    if (!fitted) fitZoom()
  }, [fitted, fitZoom])
  const problems = useMemo(() => {
    const byWidget = new Map<string, string>()
    if (form !== null) for (const problem of widgetProblems(form)) if (!byWidget.has(problem.widgetId)) byWidget.set(problem.widgetId, problem.message)
    return byWidget
  }, [form])
  const barProblems = useMemo(() => {
    const bySection = new Map<string, string>()
    if (form !== null) for (const problem of sectionProblems(form)) if (!bySection.has(problem.sectionId)) bySection.set(problem.sectionId, problem.message)
    return bySection
  }, [form])
  const values = !previewing ? newQuote : underwriterApp ? quote : sample
  const { visibility } = values
  let showing: Page | null = null
  if (form !== null) showing = previewing ? shownPage(form, pageId, visibility.pages) : activePage(form, pageId)
  const live = useMemo(
    () => ({ page: showing, sections: showing === null ? NO_SECTIONS : previewing ? previewSections(showing, visibility) : showing.sections }),
    [showing, previewing, visibility],
  )
  // In Preview nothing moves under a pressed pointer, the sheet showing included, and the
  // underwriter's place is kept.
  const { page, sections } = useHeldWhilePressed(live, previewing, viewport)
  // While building each section sits under the bar that names it; Preview has none.
  const stack = useMemo(() => stackSections(sections, previewing ? 0 : SECTION_HEADER), [sections, previewing])
  useKeptPlace(viewport, stack, zoom, previewing && page !== null ? page.id : null)
  if (form === null) throw new Error("The sheet needs the form loaded")
  const width = page === null ? 0 : page.width
  const height = sheetHeight(stackBottom(stack))
  // While an edge is dragged, never less than the sheet as pressed, so the scroll stays put.
  const scrolled = { width: Math.max(width, heldSize?.w ?? 0), height: Math.max(height, heldSize?.h ?? 0) }
  // Each component's place in the stack, the form's order, and above them all the selected one, then the ghost.
  const layers = new Map((page === null ? [] : pageWidgets(page)).map((widget, index) => [widget.id, index + 1] as const))
  const top = layers.size + 1

  return (
    <div
      ref={viewportRef}
      data-testid="sheet-viewport"
      className="min-h-0 flex-1 overflow-auto"
      style={{ background: "var(--bg-canvas)", overflowAnchor: "none" }}
      onPointerDown={(event) => {
        // A press on a scrollbar, outside the viewport's client box, scrolls and selects nothing.
        const viewport = event.currentTarget
        const box = viewport.getBoundingClientRect()
        if (event.clientX - box.left - viewport.clientLeft >= viewport.clientWidth || event.clientY - box.top - viewport.clientTop >= viewport.clientHeight) return
        leaveField(event.currentTarget)
        select(null)
      }}
    >
      {page === null ? (
        <div className="grid h-full place-items-center text-xs" style={{ color: "var(--text-muted)" }}>
          No sheet shows for this quote.
        </div>
      ) : (
        <div className="relative" style={{ width: scrolled.width * zoom + SHEET_PADDING * 2, height: scrolled.height * zoom + SHEET_PADDING * 2 }}>
          <div
            ref={setSheet}
            data-testid="sheet"
            data-zoom={zoom}
            className="absolute select-none rounded-lg"
            style={{
              left: SHEET_PADDING,
              top: SHEET_PADDING,
              width,
              height,
              transform: `scale(${zoom})`,
              transformOrigin: "0 0",
              backgroundColor: form.theme.sheet_color ?? "var(--bg-base)",
              boxShadow: "0 0 0 1px var(--border)",
              backgroundImage: "radial-gradient(circle, var(--border) 1px, transparent 1px)",
              backgroundSize: `${GRID * 2}px ${GRID * 2}px`,
            }}
          >
            <SheetValuesContext value={values}>
              {stack.map(({ section, top: sectionTop }, index) => (
                <SheetSection key={section.id} section={section} top={sectionTop} building={!previewing} selected={!previewing && selectedSection === section.id} zoom={zoom}>
                  {!previewing && (
                    <SectionBar
                      page={page}
                      section={section}
                      index={index}
                      zoom={zoom}
                      layer={top + 1}
                      selected={selectedSection === section.id}
                      hidden={!visibility.sections.has(section.id)}
                      conditions={section.show_when.conditions.length > 0 ? showWhenText(form, section.show_when) : null}
                      problem={barProblems.get(section.id) ?? null}
                      refocus={bars}
                    />
                  )}
                  {readingOrder(section.widgets).map((widget) => {
                    const selected = !previewing && selectedId === widget.id
                    return (
                      <SheetWidget
                        key={widget.id}
                        widget={widget}
                        schema={form.schema}
                        selected={selected}
                        problem={problems.get(widget.id) ?? null}
                        editable={!previewing}
                        // Preview draws only what shows, but for what a press holds, never faded.
                        hidden={!previewing && !visibility.widgets.has(widget.id)}
                        conditions={previewing || widget.show_when.conditions.length === 0 ? null : showWhenText(form, widget.show_when)}
                        layer={selected ? top : (layers.get(widget.id) ?? top)}
                      />
                    )
                  })}
                </SheetSection>
              ))}
            </SheetValuesContext>
            {!previewing && <SectionMoveLine stack={stack} zoom={zoom} layer={top + 1} />}
            {!previewing && sheetSelected && <SheetHandles page={page} height={height} zoom={zoom} layer={top + 1} />}
            {!previewing && <DropGhost layer={top + 1} zoom={zoom} />}
          </div>
        </div>
      )}
    </div>
  )
}

/**
 * A section of the sheet: a strip as wide as the sheet, its components placed from its
 * top-left corner. While building it is a group named by its title, under its bar, outlined
 * in the accent colour while selected, the line drawn a screen pixel thick whatever the zoom.
 */
function SheetSection({
  section,
  top,
  building,
  selected,
  zoom,
  children,
}: {
  section: Section
  top: number
  building: boolean
  selected: boolean
  zoom: number
  children: ReactNode
}) {
  const line = 1 / zoom
  return (
    <div
      role={building ? "group" : undefined}
      aria-label={building ? section.title : undefined}
      data-testid={`sheet-section-${section.id}`}
      data-section-id={section.id}
      className="absolute left-0 w-full"
      style={{
        top,
        height: section.height,
        boxShadow: selected ? `inset 0 0 0 ${2 * line}px var(--accent)` : undefined,
      }}
    >
      {children}
    </div>
  )
}

/**
 * The sheet's handles while its own panel is open: one at the middle of its right edge,
 * `page.width` across, setting its width, and one under the middle of its bottom, `height`
 * down, setting its height through its last section's, at `layer`, over every component,
 * outlined a screen pixel thick whatever the zoom.
 */
function SheetHandles({ page, height, zoom, layer }: { page: Page; height: number; zoom: number; layer: number }) {
  const look = { zIndex: layer, border: `${1 / zoom}px solid var(--accent)`, background: "var(--bg-base)" }
  return (
    <>
      <div
        data-testid="sheet-edge-width"
        aria-hidden="true"
        title="Width"
        onPointerDown={(event) => startSheetResize(event, page.id, "width")}
        className="absolute cursor-col-resize rounded-sm"
        style={{ left: page.width - EDGE_HANDLE.h / 2, top: `calc(50% - ${EDGE_HANDLE.w / 2}px)`, width: EDGE_HANDLE.h, height: EDGE_HANDLE.w, ...look }}
      />
      <div
        data-testid="sheet-edge-height"
        aria-hidden="true"
        title="Height"
        onPointerDown={(event) => startSheetResize(event, page.id, "height")}
        className="absolute cursor-row-resize rounded-sm"
        style={{ left: `calc(50% - ${EDGE_HANDLE.w / 2}px)`, top: height - EDGE_HANDLE.h / 2, width: EDGE_HANDLE.w, height: EDGE_HANDLE.h, ...look }}
      />
    </>
  )
}

/**
 * Where a component being dragged out of the palette would land, or, for a section, the line
 * across the sheet where it would start, at `layer`, above every component.
 */
function DropGhost({ layer, zoom }: { layer: number; zoom: number }) {
  const creating = useWorkbenchViewStore((s) => s.creating)
  if (creating === null) return null
  if (creating.type === "section") {
    const drop = sectionDropAt(creating.clientX, creating.clientY)
    if (drop === null) return null
    const line = 2 / zoom
    return (
      <div
        data-testid="drop-ghost"
        className="pointer-events-none absolute left-0 w-full"
        style={{ top: drop.line - line / 2, height: line, zIndex: layer, background: "var(--accent)" }}
      >
        <span
          className="absolute left-2 -translate-y-1/2 rounded px-1.5 py-0.5 text-[11px] font-medium"
          style={{ top: line / 2, background: "var(--accent-soft)", color: "var(--text-accent)", border: "1px solid var(--accent)" }}
        >
          {"split" in drop.place ? "New section from here" : "New section"}
        </span>
      </div>
    )
  }
  const drop = dropAt(creating.type, creating.clientX, creating.clientY)
  if (drop === null) return null
  const { at } = drop
  return (
    <div
      data-testid="drop-ghost"
      className="pointer-events-none absolute grid place-items-center rounded-md border-2 border-dashed text-xs"
      style={{ left: at.x, top: at.y, width: at.w, height: at.h, zIndex: layer, borderColor: "var(--accent)", background: "var(--accent-soft)", color: "var(--text-accent)" }}
    >
      {paletteKind(creating.type).label}
    </div>
  )
}
