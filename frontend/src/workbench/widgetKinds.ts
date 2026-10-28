import { ChartColumn, Database, FileInput, FileOutput, FileSpreadsheet, FileText, Heading, LayoutGrid, Rows3, Table2, type LucideIcon } from "lucide-react"
import { NODE_GROUP_COLORS } from "../theme/colors"
import type { Size } from "../utils/sheetGeometry"
import type { ImportFormat, PaletteType, WidgetType } from "../utils/workbenchForm"

/** A palette entry: its label, its icon, its colour in the palette and its panel, and its hint. */
export interface PaletteKind {
  label: string
  icon: LucideIcon
  color: string
  hint: string
}

export interface WidgetKind extends PaletteKind {
  /** The size a component starts with, and the smallest it can be resized to. */
  size: Size
  min: Size
}

/** A section: a strip across the sheet, dropped where it should start. */
export const SECTION_KIND: PaletteKind = {
  label: "Section",
  icon: Rows3,
  // The structure colour, as a Heading's: a section shapes the sheet and holds no values.
  color: NODE_GROUP_COLORS.structure,
  hint: "A strip across the sheet that hides and shows as one; drop it where a section should start",
}

/** Haute's entry colour, quote data coming in, which the Table and the Collection share. */
export const COMPONENT_COLOR = NODE_GROUP_COLORS.entry

/** The data colour, the Data Input's and the Data Output's: an Import and an Export move data in and out, as they do. */
export const BUTTON_COLOR = NODE_GROUP_COLORS.data

/** A button's size: a line of text with its mark, on the grid. */
const BUTTON_SIZE = { w: 160, h: 40 }
const BUTTON_MIN = { w: 80, h: 32 }

/** What each kind of component is: its palette entry and its sizes. */
export const WIDGET_KINDS: Record<WidgetType, WidgetKind> = {
  tableInput: {
    label: "Table",
    icon: Table2,
    color: COMPONENT_COLOR,
    hint: "A grid an underwriter fills in, showing columns of many-row tables",
    size: { w: 720, h: 200 },
    min: { w: 240, h: 96 },
  },
  collection: {
    label: "Collection",
    icon: LayoutGrid,
    color: COMPONENT_COLOR,
    hint: "Boxes an underwriter fills in, showing columns of one-row tables",
    size: { w: 720, h: 120 },
    min: { w: 160, h: 72 },
  },
  chart: {
    label: "Chart",
    icon: ChartColumn,
    // Explore's colour: a Chart shows values, as Explore's charts do, and takes none.
    color: NODE_GROUP_COLORS.explore,
    hint: "A chart of number columns, typed or priced",
    size: { w: 480, h: 280 },
    min: { w: 240, h: 160 },
  },
  heading: {
    label: "Heading",
    icon: Heading,
    // The structure colour: a Heading names a section of the sheet and holds no values.
    color: NODE_GROUP_COLORS.structure,
    hint: "Text naming a section of the sheet: a title, a heading or a label",
    // Tall enough for a large heading and a line of description under it.
    size: { w: 480, h: 48 },
    min: { w: 80, h: 24 },
  },
  import: {
    label: "Import",
    icon: FileInput,
    color: BUTTON_COLOR,
    hint: "A button an underwriter presses to fill a table from a CSV, an Excel workbook or a database",
    size: BUTTON_SIZE,
    min: BUTTON_MIN,
  },
  export: {
    label: "Export",
    icon: FileOutput,
    color: BUTTON_COLOR,
    hint: "A button an underwriter presses to take the quote's tables out to a CSV, an Excel workbook or a database",
    size: BUTTON_SIZE,
    min: BUTTON_MIN,
  },
}

/** The mark of each format a button reads or writes: a CSV's, a workbook's or a database's. */
export const FORMAT_MARKS: Record<ImportFormat, LucideIcon> = { csv: FileText, excel: FileSpreadsheet, sql: Database }

/** What the palette offers, in order: a section, then the components. */
export const PALETTE_KINDS: readonly PaletteType[] = ["section", "heading", "collection", "tableInput", "chart", "import", "export"]

/** A palette entry by what it adds. */
export const paletteKind = (type: PaletteType): PaletteKind => (type === "section" ? SECTION_KIND : WIDGET_KINDS[type])
