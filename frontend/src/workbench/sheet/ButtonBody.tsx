/** An Import or an Export on a sheet (specs/workbench). */
import { useState } from "react"
import { apiErrorMessage } from "../../api/errors"
import { exportWorkbenchFile } from "../../api/workbench"
import type { ExportWidget } from "../../api/types"
import { downloadBlob } from "../../panels/editors/shared/tableClipboard"
import useToastStore from "../../stores/useToastStore"
import useWorkbenchFormStore from "../../stores/useWorkbenchFormStore"
import useWorkbenchViewStore from "../../stores/useWorkbenchViewStore"
import { exportFormatShown, type ButtonWidget } from "../../utils/workbenchForm"
import { useSheetValues } from "../useSheetValues"
import { FORMAT_MARKS } from "../widgetKinds"
import type { SheetColours } from "./sheetColours"

/** Whether a button runs where it is drawn: an Export to an Excel workbook of cells, in Preview outside the app. */
const runsInPreview = (widget: ButtonWidget, previewing: boolean, underwriterApp: boolean): widget is ExportWidget =>
  previewing && !underwriterApp && widget.type === "export" && widget.target.format === "excel_cells"

/**
 * A button filling the component's box, its format's mark beside its label, centred, framed
 * in the sheets' border colour on the components' background in their font, named by its
 * label, or its kind while it has none. While building it is inert as every component's
 * picture is. In Preview and the app it is drawn faded and a press does nothing, but for an
 * Export to an Excel workbook of cells in Preview, which runs (`CellsExportButton`).
 */
export function ButtonBody({ widget, colours, previewing, problem }: { widget: ButtonWidget; colours: SheetColours; previewing: boolean; problem: string | null }) {
  const underwriterApp = useWorkbenchViewStore((s) => s.underwriterApp)
  if (runsInPreview(widget, previewing, underwriterApp)) return <CellsExportButton widget={widget} colours={colours} problem={problem} />
  return <ButtonFace widget={widget} colours={colours} disabled faded={previewing} />
}

/** The button's look, pressable or not. */
function ButtonFace({
  widget,
  colours,
  disabled,
  faded,
  onPress,
}: {
  widget: ButtonWidget
  colours: SheetColours
  disabled: boolean
  faded: boolean
  onPress?: () => void
}) {
  const format = widget.type === "import" ? widget.source.format : exportFormatShown(widget.target.format)
  const Mark = FORMAT_MARKS[format]
  const label = widget.label.trim()
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onPress}
      aria-label={label || (widget.type === "import" ? "Import" : "Export")}
      className={`flex h-full w-full items-center justify-center gap-2 overflow-hidden rounded-md px-3 text-xs font-medium ${faded ? "cursor-not-allowed opacity-60" : ""}`}
      style={{ border: `1px solid ${colours.frame}`, background: colours.background, color: colours.font ?? "var(--text-primary)" }}
    >
      <Mark size={14} aria-hidden="true" className="shrink-0" />
      {label && <span className="truncate">{label}</span>}
    </button>
  )
}

/**
 * An Export to an Excel workbook of cells in Preview: pressable while it has no problem and
 * the sample's price is the price of the sample as it stands, and no press of it is
 * unanswered. A press sends the form, the Export and that price to the server, downloads
 * the workbook it answers under the name it gives, and shows a refusal as an error toast.
 */
function CellsExportButton({ widget, colours, problem }: { widget: ExportWidget; colours: SheetColours; problem: string | null }) {
  const { price, basis } = useSheetValues()
  const [pressed, setPressed] = useState(false)
  const current = price !== null && price.basis === basis
  const disabled = problem !== null || !current || pressed
  const press = async () => {
    const { form } = useWorkbenchFormStore.getState()
    if (form === null || price === null) return
    setPressed(true)
    try {
      const file = await exportWorkbenchFile(form, widget.id, price)
      downloadBlob(file.blob, file.fileName)
    } catch (error: unknown) {
      useToastStore.getState().addToast("error", apiErrorMessage(error))
    } finally {
      setPressed(false)
    }
  }
  return <ButtonFace widget={widget} colours={colours} disabled={disabled} faded={disabled} onPress={() => void press()} />
}
