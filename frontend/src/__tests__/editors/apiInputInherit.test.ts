/**
 * Unit tests for the inherit / cascade logic (`apiInputInherit.ts`) and the
 * structural path helpers it leans on (`jsonpath.ts` additions). These pin the
 * path-inventory model, the salted naming, the prefix-based pull/push candidacy,
 * and the `$value` exclusion against the engine's accept/reject shape.
 */
import { describe, it, expect } from "vitest"
import {
  arrayDepth,
  frameSegments,
  parseColumnPathFull,
  segmentPrefix,
} from "../../panels/editors/jsonpath"
import {
  buildPathInventory,
  buildInheritGroups,
  getCascadeDestinations,
  inheritedColumnName,
  dedupName,
  validateColumnPathAgainstFrame,
  isReservedLeafPath,
  buildInsertedColumns,
  type InventoryKey,
} from "../../panels/editors/apiInputInherit"
import { readV2, writeV2 } from "../../panels/editors/apiInputSchema"
import type { ApiInputColumnV2, ApiInputTableV2, ColumnType } from "../../panels/editors/apiInputSchema"

function col(name: string, path: string, type: ColumnType = "str"): ApiInputColumnV2 {
  return { name, path, type, status: "Inferred", selected: true, levels: null }
}
function tbl(path: string, columns: ApiInputColumnV2[]): ApiInputTableV2 {
  return { path, label: path, displayPath: null, emit: true, row_id_column: null, columns }
}
function inv(...keys: InventoryKey[]): Map<string, InventoryKey> {
  return new Map(keys.map((k) => [k.path, k]))
}
function key(path: string, name: string, type: ColumnType = "str"): InventoryKey {
  return { path, name, type, levels: null }
}

// ─── jsonpath structural helpers ────────────────────────────────────

describe("arrayDepth", () => {
  it.each([
    ["$[:]", 0],
    ["$[:].customer.id", 0],
    ["$[:].orders[:].amount", 1],
    ["$[:].orders[:].items[:].sku", 2],
    ["$[:].proposer.claims[:].amount", 1],
  ])("%s → %i", (p, n) => expect(arrayDepth(p)).toBe(n))
})

describe("parseColumnPathFull — splits at the deepest array hop", () => {
  it("root object leaf has empty locating", () => {
    expect(parseColumnPathFull("$[:].customer.id")).toEqual({ locating: [], leaf: "customer.id" })
  })
  it("array level + dotted leaf", () => {
    const { locating, leaf } = parseColumnPathFull("$[:].drivers[:].profile.age")
    expect(locating).toEqual([{ name: "drivers", isArray: true }])
    expect(leaf).toBe("profile.age")
  })
  it("object-located array + leaf", () => {
    const { locating, leaf } = parseColumnPathFull("$[:].proposer.claims[:].amount")
    expect(locating).toEqual([
      { name: "proposer", isArray: false },
      { name: "claims", isArray: true },
    ])
    expect(leaf).toBe("amount")
  })
  it("$value surfaces as the leaf string", () => {
    expect(parseColumnPathFull("$[:].coverages[:].$value").leaf).toBe("$value")
  })
  it("rejects a path naming no leaf", () => {
    expect(() => parseColumnPathFull("$[:].orders[:]")).toThrow()
  })
})

describe("segmentPrefix — structural, step-by-step", () => {
  const root = frameSegments("$[:]")
  const orders = frameSegments("$[:].orders[:]")
  const items = frameSegments("$[:].orders[:].items[:]")
  const vehicles = frameSegments("$[:].vehicles[:]")

  it("root is a proper prefix of any deeper frame", () => {
    expect(segmentPrefix(root, orders, { proper: true })).toBe(true)
    expect(segmentPrefix(root, items, { proper: true })).toBe(true)
  })
  it("orders is a proper prefix of items but not of itself", () => {
    expect(segmentPrefix(orders, items, { proper: true })).toBe(true)
    expect(segmentPrefix(orders, orders, { proper: true })).toBe(false)
  })
  it("same level is a non-proper prefix (prefix-or-equal)", () => {
    expect(segmentPrefix(orders, orders)).toBe(true)
  })
  it("siblings are never a prefix even at equal depth", () => {
    expect(segmentPrefix(orders, vehicles, { proper: true })).toBe(false)
    expect(segmentPrefix(orders, vehicles)).toBe(false)
  })
  it("deeper is never a prefix of shallower", () => {
    expect(segmentPrefix(items, orders)).toBe(false)
  })
})

// ─── path inventory ─────────────────────────────────────────────────

describe("buildPathInventory", () => {
  it("unions current frames and the last-infer snapshot, frames winning on name", () => {
    const frames = [tbl("$[:].orders[:]", [col("order_total", "$[:].orders[:].total", "float")])]
    const lastInfer = [
      tbl("$[:]", [col("qid", "$[:].quote_id")]),
      tbl("$[:].orders[:]", [col("total", "$[:].orders[:].total", "float")]),
    ]
    const inventory = buildPathInventory(frames, lastInfer)
    // pruned-frame key survives via the snapshot
    expect(inventory.get("$[:].quote_id")?.name).toBe("qid")
    // shared path: the current frame's name wins
    expect(inventory.get("$[:].orders[:].total")?.name).toBe("order_total")
  })
  it("excludes $value keys", () => {
    const lastInfer = [tbl("$[:].coverages[:]", [col("value", "$[:].coverages[:].$value")])]
    expect(buildPathInventory([], lastInfer).has("$[:].coverages[:].$value")).toBe(false)
  })
  it("schema-only when nothing inferred", () => {
    const frames = [tbl("$[:]", [col("qid", "$[:].quote_id")])]
    expect([...buildPathInventory(frames, null).keys()]).toEqual(["$[:].quote_id"])
  })
})

// ─── inherit candidacy (pull) ───────────────────────────────────────

describe("buildInheritGroups", () => {
  const inventory = inv(
    key("$[:].quote_id", "quote_id"),
    key("$[:].customer.id", "customer_id"),
    key("$[:].orders[:].order_date", "order_date"),
    key("$[:].vehicles[:].vid", "vid"),
    key("$[:].orders[:].items[:].sku", "sku"),
  )

  it("offers ancestor levels only, grouped and lexically sorted", () => {
    const groups = buildInheritGroups("$[:].orders[:].items[:]", inventory)
    expect(groups.map((g) => g.ancestorPath)).toEqual(["$[:]", "$[:].orders[:]"])
    expect(groups[0].ancestorLabel).toBe("root")
    expect(groups[1].ancestorLabel).toBe("orders")
    // root group carries both root-level keys, in inventory order
    expect(groups[0].candidates.map((c) => c.path)).toEqual(["$[:].quote_id", "$[:].customer.id"])
    expect(groups[1].candidates.map((c) => c.path)).toEqual(["$[:].orders[:].order_date"])
  })
  it("excludes siblings, same-level, and deeper keys", () => {
    const groups = buildInheritGroups("$[:].orders[:].items[:]", inventory)
    const offered = groups.flatMap((g) => g.candidates.map((c) => c.path))
    expect(offered).not.toContain("$[:].vehicles[:].vid") // sibling branch
    expect(offered).not.toContain("$[:].orders[:].items[:].sku") // same level
  })
  it("a root frame has no ancestor groups", () => {
    expect(buildInheritGroups("$[:]", inventory)).toEqual([])
  })
})

// ─── cascade destinations (push) ────────────────────────────────────

describe("getCascadeDestinations", () => {
  const tables = [
    tbl("$[:]", []),
    tbl("$[:].orders[:]", []),
    tbl("$[:].orders[:].items[:]", []),
    tbl("$[:].vehicles[:]", []),
  ]
  it("a root key cascades into every deeper frame", () => {
    expect(getCascadeDestinations("$[:].quote_id", tables)).toEqual([1, 2, 3])
  })
  it("an orders-level key cascades only into its descendants", () => {
    expect(getCascadeDestinations("$[:].orders[:].order_date", tables)).toEqual([2])
  })
})

// ─── naming + dedup ─────────────────────────────────────────────────

describe("inheritedColumnName — salts the full leaf", () => {
  it.each([
    ["$[:].customer.id", "customer_id"],
    ["$[:].a.b.c", "a_b_c"],
    ["$[:].quote_id", "quote_id"],
    ["$[:].orders[:].order_date", "order_date"],
  ])("%s → %s", (p, name) => expect(inheritedColumnName(p)).toBe(name))
})

describe("dedupName", () => {
  it("leaves a free name untouched", () => {
    expect(dedupName("customer_id", new Set())).toBe("customer_id")
  })
  it("suffixes _2 then _3 on collision", () => {
    expect(dedupName("id", new Set(["id"]))).toBe("id_2")
    expect(dedupName("id", new Set(["id", "id_2"]))).toBe("id_3")
  })
})

// ─── hand-entry validation ──────────────────────────────────────────

describe("validateColumnPathAgainstFrame", () => {
  const frame = "$[:].orders[:]"
  it("accepts a same-level column", () => {
    expect(validateColumnPathAgainstFrame("$[:].orders[:].total", frame)).toBeNull()
  })
  it("accepts an ancestor (broadcast) column", () => {
    expect(validateColumnPathAgainstFrame("$[:].customer.id", frame)).toBeNull()
  })
  it("rejects a deeper column", () => {
    expect(validateColumnPathAgainstFrame("$[:].orders[:].items[:].sku", frame)).not.toBeNull()
  })
  it("rejects a sideways (sibling) column", () => {
    expect(validateColumnPathAgainstFrame("$[:].vehicles[:].vid", frame)).not.toBeNull()
  })
  it("rejects a $value key in an object frame", () => {
    expect(validateColumnPathAgainstFrame("$[:].coverages[:].$value", frame)).not.toBeNull()
  })
  it("accepts a $value key on its own scalar-array frame", () => {
    expect(validateColumnPathAgainstFrame("$[:].coverages[:].$value", "$[:].coverages[:]")).toBeNull()
  })
})

describe("isReservedLeafPath", () => {
  it("detects $value leaves and ignores normal ones", () => {
    expect(isReservedLeafPath("$[:].coverages[:].$value")).toBe(true)
    expect(isReservedLeafPath("$[:].customer.id")).toBe(false)
  })
})

// ─── the shared insert builder ──────────────────────────────────────

describe("buildInsertedColumns", () => {
  const inventory = inv(
    key("$[:].quote_id", "qid"),
    key("$[:].orders[:].order_date", "order_date", "date"),
  )

  it("reuses the inventory name (name transport) and carries type/levels", () => {
    const [c] = buildInsertedColumns(["$[:].quote_id"], inventory, new Set())
    expect(c.name).toBe("qid")
    expect(c.path).toBe("$[:].quote_id")
    expect(c.origin).toBe("inherited")
    expect(c.status).toBe("Confirmed")
    expect(c.selected).toBe(true)
  })

  it("falls back to the salted leaf for an off-inventory path, and honours origin", () => {
    const [c] = buildInsertedColumns(["$[:].customer.id"], inventory, new Set(), "manual")
    expect(c.name).toBe("customer_id")
    expect(c.origin).toBe("manual")
  })

  it("de-duplicates names against existing and within the batch", () => {
    const rows = buildInsertedColumns(
      ["$[:].customer.id", "$[:].order.id"],
      new Map(),
      new Set(["customer_id"]),
    )
    expect(rows.map((r) => r.name)).toEqual(["customer_id_2", "order_id"])
  })

  it("orders shallowest level first, then by input order", () => {
    const rows = buildInsertedColumns(
      ["$[:].orders[:].order_date", "$[:].quote_id"],
      inventory,
      new Set(),
    )
    // quote_id is at the root (depth 0) so it sorts ahead of the orders-level key
    expect(rows.map((r) => r.path)).toEqual(["$[:].quote_id", "$[:].orders[:].order_date"])
  })
})

// ─── status-model origin derivation (readV2/writeV2) ────────────────

describe("readV2 / writeV2 origin", () => {
  it("derives 'inherited' for an ancestor-prefix column and 'inferred' otherwise", () => {
    const onDisk = {
      path: "d.json",
      contract: "opaque",
      tables: [
        {
          path: "$[:].orders[:]",
          label: "orders",
          emit: true,
          columns: [
            { name: "total", path: "$[:].orders[:].total", type: "float", status: "Inferred", selected: true },
            { name: "qid", path: "$[:].quote_id", type: "str", status: "Confirmed", selected: true },
          ],
        },
      ],
    }
    const v2 = readV2(onDisk)
    const cols = v2.tables[0].columns
    expect(cols.find((c) => c.name === "total")?.origin).toBe("inferred")
    expect(cols.find((c) => c.name === "qid")?.origin).toBe("inherited") // shallower than the frame
  })

  it("preserves an explicitly persisted origin and round-trips it", () => {
    const onDisk = {
      path: "d.json",
      contract: "opaque",
      tables: [
        {
          path: "$[:]",
          label: "root",
          emit: true,
          columns: [
            { name: "k", path: "$[:].k", type: "str", status: "Confirmed", selected: true, origin: "manual" },
          ],
        },
      ],
    }
    const v2 = readV2(onDisk)
    expect(v2.tables[0].columns[0].origin).toBe("manual")
    const raw = writeV2(v2) as { tables: { columns: { origin: string }[] }[] }
    expect(raw.tables[0].columns[0].origin).toBe("manual")
  })
})
