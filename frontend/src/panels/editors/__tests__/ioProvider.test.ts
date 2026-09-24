import { describe, expect, it } from "vitest"

import type { IoCapabilityGroup, IoFormatCapability } from "../../../api/types"
import { ioBranchConfig, ioProviderFieldsReady } from "../_ioProvider"

const csv = {
  name: "csv",
  input: { modes: ["scan"] },
  output: { modes: ["sink"] },
} as unknown as IoFormatCapability
const writeOnly = {
  name: "xlsx",
  input: null,
  output: { modes: ["write"] },
} as unknown as IoFormatCapability

const fileGroup = {
  name: "file",
  label: "File",
  formats: [writeOnly, csv],
  input_fields: [
    { name: "path", label: "Path", kind: "path", required: true },
    { name: "rows", label: "Rows", kind: "records", required: true },
    { name: "note", label: "Note", kind: "text", required: false },
  ],
  output_fields: [
    { name: "path", label: "Path", kind: "path", required: true },
    { name: "rows", label: "Rows", kind: "records", required: true },
  ],
} as unknown as IoCapabilityGroup

const databaseGroup = {
  name: "database",
  label: "Database",
  formats: [],
  input_fields: [],
  output_fields: [],
} as unknown as IoCapabilityGroup

describe("ioBranchConfig", () => {
  const config = { instanceOf: "base", selected_columns: ["a"], path: "old.csv", stale: true }

  it("keeps the common keys and starts the side's first format and mode", () => {
    expect(ioBranchConfig({ direction: "input", config, group: fileGroup, commonKeys: ["instanceOf", "selected_columns"] }))
      .toEqual({
        instanceOf: "base",
        selected_columns: ["a"],
        inputType: "file",
        format: "csv",
        mode: "scan",
        arguments: {},
        path: "",
        rows: [],
      })
    expect(ioBranchConfig({ direction: "output", config, group: fileGroup, commonKeys: ["instanceOf"] }))
      .toEqual({
        instanceOf: "base",
        outputType: "file",
        format: "xlsx",
        mode: "write",
        arguments: {},
        path: "",
        rows: "",
      })
  })

  it("uses a requested format that serves the side, and keeps filled-in fields when asked", () => {
    expect(ioBranchConfig({
      direction: "output",
      config,
      group: fileGroup,
      commonKeys: [],
      requestedFormat: csv,
      preserveProviderFields: true,
    })).toEqual({ outputType: "file", format: "csv", mode: "sink", arguments: {}, path: "old.csv", rows: "" })
    expect(ioBranchConfig({ direction: "input", config, group: fileGroup, commonKeys: [], requestedFormat: writeOnly }).format)
      .toBe("csv")
  })
})

describe("ioProviderFieldsReady", () => {
  it("needs exactly one of a connection or a URI, plus the side's query or table", () => {
    expect(ioProviderFieldsReady("input", databaseGroup, { connection: "warehouse", query: "select 1" })).toBe(true)
    expect(ioProviderFieldsReady("input", databaseGroup, { connection: "warehouse", uri: "db://", query: "select 1" })).toBe(false)
    expect(ioProviderFieldsReady("input", databaseGroup, { uri: "db://", table: "t" })).toBe(false)
    expect(ioProviderFieldsReady("output", databaseGroup, { uri: "db://", table: "t" })).toBe(true)
    expect(ioProviderFieldsReady("output", databaseGroup, { uri: "db://", query: "select 1" })).toBe(false)
  })

  it("reads required record fields as lists only on the input side", () => {
    expect(ioProviderFieldsReady("input", fileGroup, { path: "a.csv", rows: [] })).toBe(true)
    expect(ioProviderFieldsReady("input", fileGroup, { path: "a.csv", rows: "" })).toBe(false)
    expect(ioProviderFieldsReady("output", fileGroup, { path: "a.csv", rows: [] })).toBe(false)
    expect(ioProviderFieldsReady("output", fileGroup, { path: " ", rows: "x" })).toBe(false)
  })
})
