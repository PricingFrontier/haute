/** An Import or an Export on a sheet (specs/workbench). */
import { Search } from "lucide-react"
import { useState } from "react"
import { apiErrorMessage } from "../../api/errors"
import { useRequestKind } from "../../hooks/useRequestKind"
import type { ExportWidget, ImportWidget } from "../../api/types"
import { downloadBlob } from "../../panels/editors/shared/tableClipboard"
import useToastStore from "../../stores/useToastStore"
import { answer, under } from "../../utils/answers"
import { exportFormatShown, type ButtonWidget } from "../../utils/workbenchForm"
import { useSheetValues } from "../useSheetValues"
import { useImportRun } from "./importRun"
import { FORMAT_MARKS } from "../widgetKinds"
import type { SheetColours } from "./sheetColours"

/** Whether an Export runs where it is drawn: one to an Excel workbook of cells, in Preview and in the app. */
const exportRuns = (widget: ButtonWidget, previewing: boolean): widget is ExportWidget =>
  previewing && widget.type === "export" && widget.target.format === "excel_cells"

/**
 * A button filling the component's box, its format's mark beside its label, centred, framed
 * in the sheets' border colour on the components' background in their font, named by its
 * label, or its kind while it has none. While building it is inert as every component's
 * picture is. In Preview and the app an Import runs (`ImportButton`) and so does an Export
 * to an Excel workbook of cells (`CellsExportButton`); any other Export is drawn faded and
 * a press does nothing.
 */
export function ButtonBody({ widget, colours, previewing, problem }: { widget: ButtonWidget; colours: SheetColours; previewing: boolean; problem: string | null }) {
  if (previewing && widget.type === "import") return <ImportButton widget={widget} colours={colours} problem={problem} />
  if (exportRuns(widget, previewing)) return <CellsExportButton widget={widget} colours={colours} problem={problem} />
  return <ButtonFace widget={widget} colours={colours} disabled faded={previewing} />
}

/** An Import as a button that runs it (`useImportRun`). */
function ImportButton({ widget, colours, problem }: { widget: ImportWidget; colours: SheetColours; problem: string | null }) {
  const { disabled, run, picker } = useImportRun(widget, problem)
  return (
    <>
      <ButtonFace widget={widget} colours={colours} disabled={disabled} faded={disabled} onPress={run} />
      {picker}
    </>
  )
}

/**
 * An Import's lookup beside its field's cell (specs/workbench, Running an Import): a search
 * button, as tall as the cell, that runs it (`useImportRun`), named by the Import's label.
 */
export function LookupButton({ widget, problem, height, frame }: { widget: ImportWidget; problem: string | null; height: string; frame: string }) {
  const { disabled, run, picker } = useImportRun(widget, problem)
  const label = widget.label.trim() || "Import"
  return (
    <>
      <button
        type="button"
        disabled={disabled}
        onClick={run}
        aria-label={`Look up: ${label}`}
        title={problem ?? label}
        className={`focus-ring flex ${height} shrink-0 items-center rounded-md px-1.5 ${disabled ? "cursor-not-allowed opacity-60" : ""}`}
        style={{ border: `1px solid ${frame}` }}
      >
        <Search size={12} aria-hidden="true" />
      </button>
      {picker}
    </>
  )
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
 * An Export to an Excel workbook of cells: pressable while it has no problem, the sheet's
 * values give it something to run on (`exportCells`: in Preview the sample's current price,
 * in the app a final quote), and no press of it is unanswered. A press runs it on the
 * server, downloads the workbook it answers under the name it gives, and shows a refusal as
 * an error toast.
 */
function CellsExportButton({ widget, colours, problem }: { widget: ExportWidget; colours: SheetColours; problem: string | null }) {
  const { exportCells } = useSheetValues()
  const [pressed, setPressed] = useState(false)
  // A press answers while the button shows.
  const shown = useRequestKind()
  const disabled = problem !== null || exportCells === null || pressed
  const press = async () => {
    if (exportCells === null) return
    await answer([under(shown)], () => exportCells(widget.id), {
      ok: (file) => downloadBlob(file.blob, file.fileName),
      failed: (error) => useToastStore.getState().addToast("error", apiErrorMessage(error)),
    }, { pending: setPressed })
  }
  return <ButtonFace widget={widget} colours={colours} disabled={disabled} faded={disabled} onPress={() => void press()} />
}
