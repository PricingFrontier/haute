import { useLayoutEffect, useMemo, useRef, useState } from "react"
import useWorkbenchFormStore from "../stores/useWorkbenchFormStore"
import useWorkbenchViewStore, { activePage } from "../stores/useWorkbenchViewStore"
import { GRID, SHEET_PADDING, sheetHeight, sheetWidth } from "../utils/sheetGeometry"
import { shownFields, widgetProblems } from "../utils/workbenchForm"
import { dropAt } from "./sheetInteractions"
import SheetWidget from "./SheetWidget"
import { SheetValuesContext, usePreviewValues, useSampleValues } from "./useSheetValues"
import { WIDGET_KINDS } from "./widgetKinds"

/**
 * The sheet showing, in a scrolling viewport (specs/workbench): a snap grid that fills
 * the viewport across at the zoom and grows to hold its components, each a
 * `SheetWidget` given the sheet's values (the sample while building, the quote in
 * Preview), with, while building, a ghost where a component dragged out of the palette
 * would land. Pressing the empty sheet deselects. The sheet and its viewport are
 * registered with the view store, which places drops on them and fits the zoom to them;
 * what is on the sheet is fitted to the viewport when the sheets first show.
 */
export default function SheetCanvas() {
  const form = useWorkbenchFormStore((s) => s.form)
  const pageId = useWorkbenchViewStore((s) => s.pageId)
  const previewing = useWorkbenchViewStore((s) => s.section === "preview")
  const zoom = useWorkbenchViewStore((s) => s.zoom)
  const selectedId = useWorkbenchViewStore((s) => s.selectedId)
  const select = useWorkbenchViewStore((s) => s.select)
  const setSheet = useWorkbenchViewStore((s) => s.setSheet)
  const setViewport = useWorkbenchViewStore((s) => s.setViewport)
  const fitZoom = useWorkbenchViewStore((s) => s.fitZoom)
  const sample = useSampleValues()
  const preview = usePreviewValues()
  const viewportRef = useRef<HTMLDivElement>(null)
  // The viewport's width changes with the window and the panels either side of it.
  const [viewportWidth, setViewportWidth] = useState(0)
  useLayoutEffect(() => {
    const element = viewportRef.current
    if (element === null) return
    setViewport(element)
    const measure = () => setViewportWidth(element.clientWidth)
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(element)
    return () => {
      observer.disconnect()
      setViewport(null)
    }
  }, [setViewport])
  useLayoutEffect(() => {
    fitZoom()
  }, [fitZoom])
  const problems = useMemo(() => {
    const byWidget = new Map<string, string>()
    if (form !== null) for (const problem of widgetProblems(form)) if (!byWidget.has(problem.widgetId)) byWidget.set(problem.widgetId, problem.message)
    return byWidget
  }, [form])
  if (form === null) throw new Error("The sheet needs the form loaded")
  const page = activePage(form, pageId)
  const width = sheetWidth(viewportWidth - SHEET_PADDING * 2, zoom, page.widgets)
  const height = sheetHeight(page.widgets)

  return (
    <div
      ref={viewportRef}
      data-testid="sheet-viewport"
      className="min-h-0 flex-1 overflow-auto"
      style={{ background: "var(--bg-canvas)" }}
      onPointerDown={() => select(null)}
    >
      <div className="relative" style={{ width: width * zoom + SHEET_PADDING * 2, height: height * zoom + SHEET_PADDING * 2 }}>
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
            background: "var(--bg-base)",
            boxShadow: "0 0 0 1px var(--border)",
            backgroundImage: "radial-gradient(circle, rgba(255,255,255,.07) 1px, transparent 1px)",
            backgroundSize: `${GRID * 2}px ${GRID * 2}px`,
          }}
        >
          <SheetValuesContext value={previewing ? preview : sample}>
            {page.widgets.map((widget) => (
              <SheetWidget
                key={widget.id}
                widget={widget}
                fields={shownFields(form, widget)}
                selected={!previewing && selectedId === widget.id}
                problem={problems.get(widget.id) ?? null}
                editable={!previewing}
              />
            ))}
          </SheetValuesContext>
          {!previewing && <DropGhost />}
        </div>
      </div>
    </div>
  )
}

/** Where a component being dragged out of the palette would land. */
function DropGhost() {
  const creating = useWorkbenchViewStore((s) => s.creating)
  if (creating === null) return null
  const rect = dropAt(creating.type, creating.clientX, creating.clientY)
  if (rect === null) return null
  return (
    <div
      data-testid="drop-ghost"
      className="pointer-events-none absolute z-20 grid place-items-center rounded-md border-2 border-dashed text-xs"
      style={{ left: rect.x, top: rect.y, width: rect.w, height: rect.h, borderColor: "var(--accent)", background: "var(--accent-soft)", color: "var(--text-accent)" }}
    >
      {WIDGET_KINDS[creating.type].label}
    </div>
  )
}
