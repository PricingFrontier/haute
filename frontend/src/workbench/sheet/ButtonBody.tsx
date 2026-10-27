/** An Import or an Export on a sheet (specs/workbench). */
import type { ButtonWidget } from "../../utils/workbenchForm"
import { FORMAT_MARKS } from "../widgetKinds"
import type { SheetColours } from "./sheetColours"

/**
 * A button filling the component's box, its format's mark beside its label, centred, framed
 * in the sheets' border colour on the components' background in their font, named by its
 * label, or its kind while it has none. Nothing runs it yet, so it is disabled: while
 * building it is inert as every component's picture is, and in Preview and the app it is
 * drawn faded and a press does nothing.
 */
export function ButtonBody({ widget, colours, previewing }: { widget: ButtonWidget; colours: SheetColours; previewing: boolean }) {
  const format = widget.type === "import" ? widget.source.format : widget.target.format
  const Mark = FORMAT_MARKS[format]
  const label = widget.label.trim()
  return (
    <button
      type="button"
      disabled
      aria-label={label || (widget.type === "import" ? "Import" : "Export")}
      className={`flex h-full w-full items-center justify-center gap-2 overflow-hidden rounded-md px-3 text-xs font-medium ${previewing ? "cursor-not-allowed opacity-60" : ""}`}
      style={{ border: `1px solid ${colours.frame}`, background: colours.background, color: colours.font ?? "var(--text-primary)" }}
    >
      <Mark size={14} aria-hidden="true" className="shrink-0" />
      {label && <span className="truncate">{label}</span>}
    </button>
  )
}
