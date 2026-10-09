/**
 * The request inputs (specs/extensions): the Quote Input and the Workbench Input read
 * the quote request alike, so editor code that means "the request input" asks
 * `isRequestInputType` rather than naming the Quote Input. The guard reports each line
 * naming the Quote Input outside the forms its file may hold.
 */
import { readdirSync, readFileSync, statSync } from "node:fs"
import path from "node:path"
import { describe, expect, it } from "vitest"
import {
  NODE_TYPES,
  isRequestInputType,
  singletonTypesInDocument,
  singletonTypesOccupiedBy,
} from "../nodeTypes"

const SRC = path.resolve(__dirname, "..", "..")
const REFERENCE = /NODE_TYPES\.API_INPUT\b|["'`]apiInput["'`]/g
/** The input cache's source kind: how a source is read, `apiInput` for either request input. */
const SOURCE_KIND = /node_type\??: "apiInput"|node_type [!=]== "apiInput"|node_type\?: "dataInput" \| "apiInput"/
/** Sets that list their members, both request inputs among them, for the backend to read. */
const LISTING_SETS = /export const (?:REQUEST_INPUT_TYPES|SOURCE_ONLY_TYPES|SINGLETON_TYPES) = new Set<\w+>\(\[([\s\S]*?)\]\)/g
const ALLOWED: Record<string, RegExp[]> = {
  "types/node.ts": [/^\s*API_INPUT: "apiInput",$/],
  "utils/nodeTypes.ts": [/^\s*\[NODE_TYPES\.API_INPUT\]:/, /^\s*NODE_TYPES\.API_INPUT, NODE_TYPES\.LIVE_SWITCH, NODE_TYPES\.OUTPUT,$/],
  "utils/nodeTypeRegistry.ts": [/^\s*\[NODE_TYPES\.API_INPUT\]: PipelineNode,$/],
  "panels/NodeConfigEditor.tsx": [/^\s*case NODE_TYPES\.API_INPUT:$/],
  "panels/NodePalette.tsx": [/if \(workbench && type === NODE_TYPES\.API_INPUT\) return NODE_TYPES\.WORKBENCH_INPUT$/],
  "api/types.ts": [SOURCE_KIND],
  "utils/inputSnapshotSource.ts": [SOURCE_KIND],
  "hooks/ensureInputSnapshots.ts": [SOURCE_KIND],
  "hooks/useNodeDataCache.ts": [SOURCE_KIND],
  "components/InputImportButton.tsx": [SOURCE_KIND],
}
/** Files whose Quote Input entry must have its Workbench Input twin. */
const TWINNED = ["utils/nodeTypes.ts", "utils/nodeTypeRegistry.ts"]

/** Each line of *source* (at *file*, relative to src) that names the Quote Input alone. */
export function requestInputReports(file: string, source: string): string[] {
  const exempt: [number, number][] = []
  if (file === "utils/nodeTypes.ts") {
    for (const match of source.matchAll(LISTING_SETS)) {
      const start = match.index + match[0].indexOf(match[1])
      if (match[1].includes("NODE_TYPES.WORKBENCH_INPUT")) exempt.push([start, start + match[1].length])
    }
  }
  const lines = source.split("\n")
  const reports: string[] = []
  for (const match of source.matchAll(REFERENCE)) {
    if (exempt.some(([start, end]) => match.index >= start && match.index < end)) continue
    const number = source.slice(0, match.index).split("\n").length
    const line = lines[number - 1].replace(/\r$/, "")
    if ((ALLOWED[file] ?? []).some((form) => form.test(line))) continue
    reports.push(`${file}:${number}: ${line.trim()}`)
  }
  if (TWINNED.includes(file) && /\[NODE_TYPES\.API_INPUT\]:/.test(source) && !/\[NODE_TYPES\.WORKBENCH_INPUT\]:/.test(source)) {
    reports.push(`${file}: no [NODE_TYPES.WORKBENCH_INPUT] entry beside the Quote Input's`)
  }
  return [...new Set(reports)]
}

function sourceFiles(directory: string): string[] {
  return readdirSync(directory).flatMap((name) => {
    const full = path.join(directory, name)
    if (statSync(full).isDirectory()) return name === "__tests__" || name === "generated" ? [] : sourceFiles(full)
    return /\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name) && name !== "setupTests.ts" ? [full] : []
  })
}

describe("request-input checks", () => {
  it.each([
    ["types/node.ts", '  API_INPUT: "apiInput",'],
    ["utils/nodeTypes.ts", '  [NODE_TYPES.API_INPUT]: { name: "Quote Input" },\n  [NODE_TYPES.WORKBENCH_INPUT]: { name: "Workbench Input" },'],
    ["utils/nodeTypes.ts", "export const SOURCE_ONLY_TYPES = new Set<string>([\n  NODE_TYPES.API_INPUT, NODE_TYPES.WORKBENCH_INPUT,\n])"],
    ["panels/NodeConfigEditor.tsx", "    case NODE_TYPES.API_INPUT:"],
    ["utils/inputSnapshotSource.ts", '    return { schema_version: 1, node_type: "apiInput", config }'],
    ["hooks/ensureInputSnapshots.ts", '  const quotes = sources.filter((source) => source.node_type === "apiInput")'],
  ])("accepts the form %s may hold: %s", (file, source) => {
    expect(requestInputReports(file, source)).toEqual([])
  })

  it.each([
    ["utils/graphHelpers.ts", "  if (node.data.nodeType === NODE_TYPES.API_INPUT) {"],
    ["utils/nodeTypes.ts", "  return nodeType === NODE_TYPES.API_INPUT && nodeType !== NODE_TYPES.WORKBENCH_INPUT"],
    ["utils/nodeTypes.ts", "const OTHER = new Set([NODE_TYPES.API_INPUT, NODE_TYPES.WORKBENCH_INPUT])"],
    ["api/client.ts", '    if (requestNode.node_type === "apiInput") {'],
    ["utils/nodeTypeRegistry.ts", "  [NODE_TYPES.API_INPUT]: PipelineNode,"],
    ["hooks/usePreview.ts", "  if (nodeType === 'apiInput') return null"],
  ])("reports %s naming the Quote Input alone: %s", (file, source) => {
    expect(requestInputReports(file, source)).toHaveLength(1)
  })

  it("finds no editor code naming the Quote Input where it means a request input", () => {
    const reports = sourceFiles(SRC).flatMap((full) =>
      requestInputReports(path.relative(SRC, full).split(path.sep).join("/"), readFileSync(full, "utf8")))
    expect(reports).toEqual([])
  })
})

describe("the request inputs", () => {
  it("are the Quote Input and the Workbench Input", () => {
    expect(isRequestInputType(NODE_TYPES.API_INPUT)).toBe(true)
    expect(isRequestInputType(NODE_TYPES.WORKBENCH_INPUT)).toBe(true)
    expect(isRequestInputType(NODE_TYPES.DATA_INPUT)).toBe(false)
    expect(isRequestInputType(undefined)).toBe(false)
  })

  it("share one singleton slot: either occupies both", () => {
    expect(singletonTypesOccupiedBy(NODE_TYPES.WORKBENCH_INPUT)).toEqual([NODE_TYPES.API_INPUT, NODE_TYPES.WORKBENCH_INPUT])
    // The response nodes share another.
    expect(singletonTypesOccupiedBy(NODE_TYPES.OUTPUT)).toEqual([NODE_TYPES.OUTPUT, NODE_TYPES.WORKBENCH_OUTPUT])
    expect(singletonTypesOccupiedBy(NODE_TYPES.WORKBENCH_OUTPUT)).toEqual([NODE_TYPES.OUTPUT, NODE_TYPES.WORKBENCH_OUTPUT])
    expect(singletonTypesOccupiedBy(NODE_TYPES.POLARS)).toEqual([])

    for (const nodeType of [NODE_TYPES.API_INPUT, NODE_TYPES.WORKBENCH_INPUT]) {
      const occupied = singletonTypesInDocument([{ data: { label: "quote", nodeType } }])
      expect([...occupied].sort()).toEqual([NODE_TYPES.API_INPUT, NODE_TYPES.WORKBENCH_INPUT].sort())
    }
  })
})
