import { LayoutGrid, Table2, type LucideIcon } from "lucide-react"
import { NODE_GROUP_COLORS } from "../theme/colors"
import type { Size } from "../utils/sheetGeometry"
import type { WidgetType } from "../utils/workbenchForm"

export interface WidgetKind {
  label: string
  icon: LucideIcon
  hint: string
  /** The size a component starts with, and the smallest it can be resized to. */
  size: Size
  min: Size
}

/** What each kind of component is: its palette entry and its sizes. */
export const WIDGET_KINDS: Record<WidgetType, WidgetKind> = {
  tableInput: {
    label: "Table",
    icon: Table2,
    hint: "A grid an underwriter fills in, showing columns of many-row tables",
    size: { w: 720, h: 200 },
    min: { w: 240, h: 96 },
  },
  collection: {
    label: "Collection",
    icon: LayoutGrid,
    hint: "Boxes an underwriter fills in, showing columns of one-row tables",
    size: { w: 720, h: 120 },
    min: { w: 160, h: 72 },
  },
}

/** What the palette offers, in order. */
export const PALETTE_KINDS: readonly WidgetType[] = ["tableInput", "collection"]

/** Haute's entry colour, quote data coming in, which the Table and the Collection share. */
export const COMPONENT_COLOR = NODE_GROUP_COLORS.entry
