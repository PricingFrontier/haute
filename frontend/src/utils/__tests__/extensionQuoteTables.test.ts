/**
 * The copy rules for a Workbench Input, whose tables and sample quote an installed
 * extension supplies, and a Workbench Output, whose response tables it supplies
 * (specs/extensions): when a copy needs updating, what each starts with from the palette,
 * and which extension supplies them.
 */
import { describe, expect, it } from "vitest"
import type { ExtensionInfo } from "../../api/types"
import {
  paletteWorkbenchInputConfig,
  paletteWorkbenchOutputConfig,
  quoteTablesPatch,
  quoteTablesSupplier,
  responseTablesPatch,
  responseTablesSupplier,
  workbenchOutputTableLabels,
  type QuoteTables,
} from "../extensionQuoteTables"

const table = {
  path: "$[:]",
  label: "policy_details",
  emit: true,
  row_id_column: null,
  columns: [
    { name: "state", path: "$[:].policy_details.state", type: "str", status: "Confirmed", selected: true, levels: null },
  ],
}
const sample = { policy_details: { state: "NY" } }
const responseTable = {
  path: "$[:].layers[:]",
  label: "layers",
  emit: true,
  row_id_column: "layer",
  columns: [
    { name: "layer", path: "$[:].layers[:].layer", type: "int", status: "Confirmed", selected: true, levels: null },
  ],
}
const fetched: QuoteTables = {
  extension: "obverse",
  tables: [table],
  sample,
  responseTables: [responseTable],
  fetch: 3,
}

const extension = (name: string, quoteTables: boolean, responseTables = false): ExtensionInfo => ({
  name,
  label: name,
  api_base: `/api/extensions/${name}`,
  entry_url: `/extensions/${name}/${name}.js`,
  ready: true,
  detail: null,
  quote_tables: quoteTables,
  response_tables: responseTables,
})

describe("quoteTablesPatch", () => {
  it("updates a copy whose tables or sample differ from the extension's, with both", () => {
    expect(quoteTablesPatch({ tables: [], sample }, fetched)).toEqual({ tables: [table], sample })
    expect(quoteTablesPatch({}, fetched)).toEqual({ tables: [table], sample })
    expect(quoteTablesPatch({ tables: [table], sample: {} }, fetched)).toEqual({ tables: [table], sample })
  })

  it("leaves a copy that matches alone, whatever the order of its keys", () => {
    const reordered = Object.fromEntries(Object.entries(table).reverse())
    expect(quoteTablesPatch({ tables: [reordered], sample }, fetched)).toBeNull()
  })

  it("reads a copy without a sample as one with an empty sample", () => {
    expect(quoteTablesPatch({ tables: [table] }, { ...fetched, sample: {} })).toBeNull()
  })

  it("changes nothing before a fetch", () => {
    expect(quoteTablesPatch({ tables: [] }, null)).toBeNull()
  })

  it("gives the config its own copy of the fetched tables and sample", () => {
    expect(quoteTablesPatch({}, fetched)?.tables[0]).not.toBe(table)
    expect(quoteTablesPatch({}, fetched)?.sample.policy_details).not.toBe(sample.policy_details)
  })
})

describe("paletteWorkbenchInputConfig", () => {
  it("starts a Workbench Input with the newest tables and sample fetched, or none until they are", () => {
    const defaults = { tables: [] }

    expect(paletteWorkbenchInputConfig(defaults, fetched)).toEqual({ tables: [table], sample })
    expect(paletteWorkbenchInputConfig(defaults, null)).toEqual({ tables: [], sample: {} })
    expect(paletteWorkbenchInputConfig(defaults, fetched).tables).not.toBe(fetched.tables)
  })
})

describe("quoteTablesSupplier", () => {
  it("is the listed extension that supplies the tables, if one does", () => {
    expect(quoteTablesSupplier([extension("forms", false), extension("obverse", true)])?.name).toBe("obverse")
    expect(quoteTablesSupplier([extension("forms", false)])).toBeNull()
    expect(quoteTablesSupplier([])).toBeNull()
  })
})

describe("the response's tables", () => {
  it("updates a Workbench Output's copy whose tables differ, and leaves a matching one alone", () => {
    expect(responseTablesPatch({ tables: [] }, fetched)).toEqual({ tables: [responseTable] })
    const reordered = Object.fromEntries(Object.entries(responseTable).reverse())
    expect(responseTablesPatch({ tables: [reordered] }, fetched)).toBeNull()
    expect(responseTablesPatch({ tables: [] }, null)).toBeNull()
    expect(responseTablesPatch({}, fetched)?.tables[0]).not.toBe(responseTable)
  })

  it("drops the mapping's entries for tables and columns the new tables lack", () => {
    const mapping = { layers: { layer: "number", premium: null }, gone: { x: "y" } }
    const renamed = { ...responseTable, columns: [{ ...responseTable.columns[0], name: "number" }] }

    expect(responseTablesPatch({ tables: [responseTable], mapping }, fetched)).toEqual({
      tables: [responseTable],
      mapping: { layers: { layer: "number" } },
    })
    expect(responseTablesPatch({ tables: [], mapping }, { ...fetched, responseTables: [renamed] })).toEqual({
      tables: [renamed],
      mapping: {},
    })
    expect(responseTablesPatch({ tables: [responseTable], mapping: { layers: { layer: "number" } } }, fetched))
      .toBeNull()
  })

  it("starts a Workbench Output with the newest response tables fetched, or none until they are", () => {
    expect(paletteWorkbenchOutputConfig({ tables: [] }, fetched)).toEqual({ tables: [responseTable] })
    expect(paletteWorkbenchOutputConfig({ tables: [] }, null)).toEqual({ tables: [] })
  })

  it("is supplied by the tables' supplier when it supplies them too", () => {
    expect(responseTablesSupplier([extension("obverse", true, true)])?.name).toBe("obverse")
    expect(responseTablesSupplier([extension("obverse", true)])).toBeNull()
    expect(responseTablesSupplier([])).toBeNull()
  })

  it("makes each table's label a port, in order", () => {
    expect(workbenchOutputTableLabels({ tables: [responseTable, table] })).toEqual(["layers", "policy_details"])
    expect(workbenchOutputTableLabels({})).toEqual([])
  })
})
