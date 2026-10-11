import { AlertTriangle, GripHorizontal } from "lucide-react"
import { useLayoutEffect, useMemo, useRef, type KeyboardEvent } from "react"
import type { Page, Section } from "../api/types"
import useWorkbenchFormStore from "../stores/useWorkbenchFormStore"
import useWorkbenchViewStore from "../stores/useWorkbenchViewStore"
import { SECTION_HEADER, type Stacked } from "../utils/sheetGeometry"
import { fitSection, moveSection, sectionProblems, showWhenText } from "../utils/workbenchForm"
import { deleteSection, startSectionMove, startSectionResize } from "./sheetInteractions"
import { ShowWhenMark } from "./ShowWhenEditor"
import { useSheetVisibility } from "./useSheetValues"
import { SECTION_KIND } from "./widgetKinds"

/** The selected section's handle under its middle, which sets its height. */
const HANDLE = { w: 40, h: 8 }

/**
 * The sections' bars while building (specs/workbench), drawn on the sheet with it, each
 * across the sheet just above its section: a grip, its title, an eye while it has
 * conditions (struck through, the bar faded, while the sample hides it) and the warning
 * colour's mark while it has a problem, its conditions and its problem told to a screen
 * reader as the bar's descriptions. Pressing a bar selects its section, and dragging it
 * moves the section past others, a line marking where it would go; Alt+Up and Alt+Down move
 * the focused bar's section a place, keeping the focus, and Delete or Backspace deletes it.
 * The selected section has a handle under its middle that sets its height; a double-click
 * on it fits the section. The handle and the line are drawn at `layer`, over every component.
 */
export default function SectionHeaders({ page, stack, zoom, layer }: { page: Page; stack: readonly Stacked<Section>[]; zoom: number; layer: number }) {
  const form = useWorkbenchFormStore((s) => s.form)
  const change = useWorkbenchFormStore((s) => s.change)
  const selectedSection = useWorkbenchViewStore((s) => s.sectionId)
  const selectSection = useWorkbenchViewStore((s) => s.selectSection)
  const moving = useWorkbenchViewStore((s) => s.movingSection)
  // The bars are Build's: the sample decides what they mark hidden.
  const visibility = useSheetVisibility(false)
  const bars = useRef(new Map<string, HTMLButtonElement>())
  // A bar whose section moved by the keyboard, focused again once it is drawn in its new place.
  const refocus = useRef<string | null>(null)
  useLayoutEffect(() => {
    if (refocus.current === null) return
    bars.current.get(refocus.current)?.focus()
    refocus.current = null
  })
  const problems = useMemo(() => {
    const bySection = new Map<string, string>()
    if (form !== null) for (const problem of sectionProblems(form)) if (!bySection.has(problem.sectionId)) bySection.set(problem.sectionId, problem.message)
    return bySection
  }, [form])
  if (form === null) return null
  // The lines are drawn a screen pixel thick whatever the zoom.
  const line = 1 / zoom

  const onBarKeyDown = (event: KeyboardEvent<HTMLButtonElement>, section: Section, index: number) => {
    if (event.altKey && (event.key === "ArrowUp" || event.key === "ArrowDown")) {
      event.preventDefault()
      const to = index + (event.key === "ArrowUp" ? -1 : 1)
      if (to < 0 || to >= page.sections.length) return
      refocus.current = section.id
      change((spec) => moveSection(spec, page.id, index, to))
    } else if (event.key === "Delete" || event.key === "Backspace") {
      event.preventDefault()
      deleteSection(page, section)
    }
  }

  // Where a section dragged by its bar would go: above the section now at that place among
  // the others, or under the last of them.
  let movingAt: number | null = null
  if (moving !== null) {
    const others = stack.filter(({ section }) => section.id !== moving.id)
    const last = others.at(-1)
    const before = others[moving.to]
    movingAt = before !== undefined ? before.top - SECTION_HEADER : last === undefined ? 0 : last.top + last.section.height
  }

  return (
    <>
      {stack.map(({ section, top }, index) => {
        const hidden = !visibility.sections.has(section.id)
        const selected = selectedSection === section.id
        const problem = problems.get(section.id)
        // A button's marks are no part of what a screen reader says of it: its conditions and
        // its problem are told through descriptions of its own.
        const conditions = section.show_when.conditions.length > 0 ? showWhenText(form, section.show_when) : null
        const described = [conditions !== null && `${section.id}-conditions`, problem !== undefined && `${section.id}-problem`].filter(Boolean).join(" ")
        return (
          <div key={section.id}>
            <button
              ref={(element) => {
                if (element === null) bars.current.delete(section.id)
                else bars.current.set(section.id, element)
              }}
              type="button"
              data-testid={`section-header-${section.id}`}
              data-hidden={hidden ? "true" : undefined}
              aria-label={`Section ${index + 1}, ${section.title}`}
              aria-describedby={described || undefined}
              aria-pressed={selected}
              aria-keyshortcuts="Alt+ArrowUp Alt+ArrowDown Delete"
              title={`${section.title}: drag, or Alt+Up and Alt+Down, to move`}
              onPointerDown={(event) => {
                // Not a press on the empty sheet, which would close the section's panel.
                event.stopPropagation()
                startSectionMove(event, section.id)
              }}
              onClick={() => selectSection(section.id)}
              onKeyDown={(event) => onBarKeyDown(event, section, index)}
              className="focus-ring absolute left-0 flex w-full cursor-grab items-center gap-1.5 px-2 text-[11px] font-medium hover-bg"
              style={{
                top: top - SECTION_HEADER,
                height: SECTION_HEADER,
                borderTop: `${line}px solid var(--border)`,
                background: selected ? "var(--accent-soft)" : "var(--bg-elevated)",
                color: selected ? "var(--text-accent)" : "var(--text-secondary)",
                opacity: hidden ? 0.5 : undefined,
              }}
            >
              <GripHorizontal size={12} aria-hidden="true" style={{ color: SECTION_KIND.color }} />
              <span className="truncate">{section.title}</span>
              {conditions !== null && <ShowWhenMark hidden={hidden} title={conditions} />}
              {problem !== undefined && (
                <span role="img" aria-label={problem} title={problem} className="flex" style={{ color: "var(--warning)" }}>
                  <AlertTriangle size={11} aria-hidden="true" />
                </span>
              )}
            </button>
            {conditions !== null && (
              <span id={`${section.id}-conditions`} className="sr-only">
                {hidden ? `Hidden for the sample. ${conditions}` : conditions}
              </span>
            )}
            {problem !== undefined && (
              <span id={`${section.id}-problem`} className="sr-only">
                {problem}
              </span>
            )}
            {selected && (
              <div
                data-testid={`section-edge-${section.id}`}
                aria-hidden="true"
                title="Drag to set the section's height; double-click to fit it"
                onPointerDown={(event) => startSectionResize(event, section.id)}
                onDoubleClick={() => change((spec) => fitSection(spec, section.id))}
                className="absolute cursor-row-resize rounded-sm"
                // Under the middle of the section's bottom, over its components.
                style={{
                  left: `calc(50% - ${HANDLE.w / 2}px)`,
                  top: top + section.height - HANDLE.h / 2,
                  width: HANDLE.w,
                  height: HANDLE.h,
                  zIndex: layer,
                  border: `${line}px solid var(--accent)`,
                  background: "var(--bg-base)",
                }}
              />
            )}
          </div>
        )
      })}
      {movingAt !== null && (
        <div
          data-testid="section-moving-line"
          className="pointer-events-none absolute left-0 w-full"
          style={{ top: movingAt - line, height: 2 * line, background: "var(--accent)", zIndex: layer }}
        />
      )}
    </>
  )
}
