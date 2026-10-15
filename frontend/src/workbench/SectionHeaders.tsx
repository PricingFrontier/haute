import { AlertTriangle, GripHorizontal } from "lucide-react"
import type { KeyboardEvent } from "react"
import type { Page, Section } from "../api/types"
import { altArrowMove, type RefocusAfterMove } from "../hooks/useRefocusAfterMove"
import useWorkbenchFormStore from "../stores/useWorkbenchFormStore"
import useWorkbenchViewStore from "../stores/useWorkbenchViewStore"
import { SECTION_HEADER, type Stacked } from "../utils/sheetGeometry"
import { fitSection, moveSection } from "../utils/workbenchForm"
import { deleteSection, startSectionMove, startSectionResize } from "./sheetInteractions"
import { ShowWhenMark } from "./ShowWhenEditor"
import { SECTION_KIND } from "./widgetKinds"

/** The selected section's handle under its middle, which sets its height. */
const HANDLE = { w: 40, h: 8 }

/**
 * A section's bar while building (specs/workbench), drawn first in its section, so Tab and
 * a screen reader meet each section's bar before its components, and just above it: a
 * grip, its title, an eye while it has `conditions` (struck through, the bar faded, while a
 * new quote hides it) and the warning colour's mark while it has a problem, its conditions
 * and its problem told to a screen reader as the bar's descriptions. Pressing the bar
 * selects its section, and dragging it moves the section past others; Alt+Up and Alt+Down
 * move the section a place, the bar keeping the focus through `refocus`, which the sheet's
 * bars share, and Delete or Backspace deletes it. The selected section has a handle under
 * its middle that sets its height; a double-click on it fits the section. The handle is
 * drawn at `layer`, over every component.
 */
export function SectionBar({
  page,
  section,
  index,
  zoom,
  layer,
  selected,
  hidden,
  conditions,
  problem,
  refocus,
}: {
  page: Page
  section: Section
  index: number
  zoom: number
  layer: number
  selected: boolean
  hidden: boolean
  conditions: string | null
  problem: string | null
  refocus: RefocusAfterMove<HTMLButtonElement>
}) {
  const change = useWorkbenchFormStore((s) => s.change)
  const selectSection = useWorkbenchViewStore((s) => s.selectSection)
  // The lines are drawn a screen pixel thick whatever the zoom.
  const line = 1 / zoom

  const onKeyDown = (event: KeyboardEvent<HTMLButtonElement>) => {
    if (event.altKey) {
      const to = altArrowMove(event, "vertical", index, page.sections.length)
      if (to === null) return
      refocus.moved(section.id)
      change((spec) => moveSection(spec, page.id, index, to))
    } else if (event.key === "Delete" || event.key === "Backspace") {
      event.preventDefault()
      void deleteSection(page, section)
    }
  }

  // A button's marks are no part of what a screen reader says of it: its conditions and its
  // problem are told through descriptions of its own.
  const described = [conditions !== null && `${section.id}-conditions`, problem !== null && `${section.id}-problem`].filter(Boolean).join(" ")
  return (
    <>
      <button
        ref={refocus.register(section.id)}
        type="button"
        data-testid={`section-header-${section.id}`}
        data-hidden={hidden ? "true" : undefined}
        data-selected={selected ? "true" : undefined}
        aria-label={`Section ${index + 1}, ${section.title}`}
        aria-describedby={described || undefined}
        aria-keyshortcuts="Alt+ArrowUp Alt+ArrowDown Delete"
        title={section.title}
        onPointerDown={(event) => {
          // Not a press on the empty sheet, which would close the section's panel.
          event.stopPropagation()
          startSectionMove(event, section.id)
        }}
        onClick={() => selectSection(section.id)}
        onKeyDown={onKeyDown}
        className="focus-ring absolute left-0 flex w-full cursor-grab items-center gap-1.5 px-2 text-[11px] font-medium hover-bg"
        style={{
          top: -SECTION_HEADER,
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
        {problem !== null && (
          <span role="img" aria-label={problem} title={problem} className="flex" style={{ color: "var(--warning)" }}>
            <AlertTriangle size={11} aria-hidden="true" />
          </span>
        )}
      </button>
      {conditions !== null && (
        <span id={`${section.id}-conditions`} className="sr-only">
          {hidden ? `Hidden for a new quote. ${conditions}` : conditions}
        </span>
      )}
      {problem !== null && (
        <span id={`${section.id}-problem`} className="sr-only">
          {problem}
        </span>
      )}
      {selected && (
        <div
          data-testid={`section-edge-${section.id}`}
          aria-hidden="true"
          title="Height"
          onPointerDown={(event) => startSectionResize(event, section.id)}
          onDoubleClick={() => change((spec) => fitSection(spec, section.id))}
          className="absolute cursor-row-resize rounded-sm"
          // Under the middle of the section's bottom, over its components.
          style={{
            left: `calc(50% - ${HANDLE.w / 2}px)`,
            top: section.height - HANDLE.h / 2,
            width: HANDLE.w,
            height: HANDLE.h,
            zIndex: layer,
            border: `${line}px solid var(--accent)`,
            background: "var(--bg-base)",
          }}
        />
      )}
    </>
  )
}

/**
 * Where a section dragged by its bar would go, while one is (specs/workbench): a line across
 * the sheet above the section now at that place among the others, or under the last of
 * them, at `layer`, over every component.
 */
export function SectionMoveLine({ stack, zoom, layer }: { stack: readonly Stacked<Section>[]; zoom: number; layer: number }) {
  const moving = useWorkbenchViewStore((s) => s.movingSection)
  if (moving === null) return null
  const others = stack.filter(({ section }) => section.id !== moving.id)
  const last = others.at(-1)
  const before = others[moving.to]
  const at = before !== undefined ? before.top - SECTION_HEADER : last === undefined ? 0 : last.top + last.section.height
  const line = 1 / zoom
  return (
    <div
      data-testid="section-moving-line"
      className="pointer-events-none absolute left-0 w-full"
      style={{ top: at - line, height: 2 * line, background: "var(--accent)", zIndex: layer }}
    />
  )
}
