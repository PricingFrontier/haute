/** Input records behind the shared display rows of a Table or Chart. */
import type { SchemaTable } from "../api/types"
import { tableIndex } from "./quoteIdentity"
import { filled, indexIdentity, typedIndexValue, type RowsByTable } from "./sheetValues"
import { tableGrain, type SampleValue } from "./workbenchForm"

export interface InputRowLayout {
  count: number
  /** Each display row's source row, absent when that table has no matching record. */
  rows: Map<string, (number | undefined)[]>
  /** The index to give a record created in a previously empty cell. */
  indexes: Map<string, (SampleValue | undefined)[]>
}

interface Slot {
  index: SampleValue | undefined
  sources: Map<string, number>
}

export function inputRowLayout(tables: readonly SchemaTable[], held: RowsByTable): InputRowLayout {
  const groups = new Map<string, SchemaTable[]>()
  for (const table of tables) {
    const grain = tableGrain(table)
    const group = groups.get(grain) ?? []
    group.push(table)
    groups.set(grain, group)
  }
  const layout: InputRowLayout = { count: 0, rows: new Map(), indexes: new Map() }
  for (const group of groups.values()) {
    const slots: Slot[] = []
    const keyed = new Map<string | null, Slot[]>()
    for (const table of group) {
      const index = tableIndex(table)
      if (index === null) throw new Error(`Many-row table ${table.name} has no index`)
      const nextKey = new Map<string | null, number>()
      for (const [source, row] of (held[table.name] ?? []).entries()) {
        const raw = index.index === "numbered" ? String(source + 1) : row[index.name]
        const identity = typeof raw === "string" && filled(raw)
          ? indexIdentity(typedIndexValue(index.type, raw.trim()))
          : null
        // Match repeated or blank indexes by occurrence, retaining every record so its
        // validation error remains visible. A blank index never matches a filled one.
        const matches = keyed.get(identity) ?? []
        const at = nextKey.get(identity) ?? 0
        let slot = matches[at]
        nextKey.set(identity, at + 1)
        if (slot === undefined) {
          slot = { index: raw, sources: new Map() }
          slots.push(slot)
          matches.push(slot)
          keyed.set(identity, matches)
        }
        slot.sources.set(table.name, source)
      }
    }
    layout.count = Math.max(layout.count, slots.length)
    for (const table of group) {
      layout.rows.set(table.name, slots.map((slot) => slot.sources.get(table.name)))
      layout.indexes.set(table.name, slots.map((slot) => slot.index))
    }
  }
  return layout
}
