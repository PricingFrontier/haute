import { ChartColumn, Heading, LayoutGrid, Table2, type LucideIcon } from "lucide-react"
import { NODE_GROUP_COLORS } from "../theme/colors"
import type { Size } from "../utils/sheetGeometry"
import type { WidgetType } from "../utils/workbenchForm"

export interface WidgetKind {
  label: string
  icon: LucideIcon
  /** Its colour in the palette and its panel. */
  color: string
  hint: string
  /** The size a component starts with, and the smallest it can be resized to. */
  size: Size
  min: Size
}

/** Haute's entry colour, quote data coming in, which the Table and the Collection share. */
export const COMPONENT_COLOR = NODE_GROUP_COLORS.entry

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
}

/** What the palette offers, in order. */
export const PALETTE_KINDS: readonly WidgetType[] = ["tableInput", "collection", "chart", "heading"]
