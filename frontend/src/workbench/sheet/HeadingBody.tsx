/** A Heading on a sheet: its text at its size and its description, aligned (specs/workbench). */
import type { HeadingWidget } from "../../api/types"
import useWorkbenchViewStore from "../../stores/useWorkbenchViewStore"
import { type SheetColours } from "./sheetColours"

/** What each size of Heading is: its element, a heading's level to a screen reader, its type and its colour. */
const HEADING_STYLES: Record<HeadingWidget["size"], { as: "h2" | "h3" | "h4"; className: string; color: string }> = {
  large: { as: "h2", className: "text-xl font-semibold", color: "var(--text-primary)" },
  medium: { as: "h3", className: "text-base font-semibold", color: "var(--text-primary)" },
  small: { as: "h4", className: "text-xs font-semibold", color: "var(--text-secondary)" },
}

/** Where each alignment puts a Heading's text, and its description, across its width. */
const HEADING_ALIGN: Record<HeadingWidget["align"], string> = { left: "text-left", center: "text-center", right: "text-right" }

/**
 * A Heading: its text alone on the sheet, at its size, with its description under it while
 * it has one, both aligned as it says, wrapping within its width and centred in its height.
 * With no text it asks for some while building, its description unshown, and shows nothing
 * in Preview.
 */
export function HeadingBody({ widget, colours }: { widget: HeadingWidget; colours: SheetColours }) {
  const previewing = useWorkbenchViewStore((s) => s.section === "preview")
  if (widget.text.trim() === "") {
    if (previewing) return null
    // One line, cut short in a small Heading rather than running out over what is below it.
    return (
      <div
        className="flex h-full items-center justify-center overflow-hidden rounded-md px-2 text-[11px]"
        style={{ border: "1px dashed var(--border)", color: "var(--text-secondary)" }}
      >
        <span className="min-w-0 truncate">Type its text in the panel on the right</span>
      </div>
    )
  }
  const { as: Level, className, color } = HEADING_STYLES[widget.size]
  return (
    <div className={`flex h-full flex-col justify-center-safe gap-0.5 overflow-hidden ${HEADING_ALIGN[widget.align]}`}>
      <Level className={`min-w-0 break-words ${className}`} style={{ color: colours.font ?? color }}>
        {widget.text}
      </Level>
      {widget.description.trim() !== "" && (
        <p className="min-w-0 text-xs break-words" style={{ color: colours.font ?? "var(--text-secondary)" }}>
          {widget.description}
        </p>
      )}
    </div>
  )
}
