/**
 * Unit tests for the pure helpers behind the shared table-actions + OUTPUT
 * path tools: CSV serialisation, common-root derivation, and the paste header
 * sniffer. (substitutePrefix / composePrefix are covered in
 * OutputEditorPathTools.test.tsx; TSV/paste round-trip in FrameTableActions.)
 */
import { describe, it, expect } from "vitest"
import { buildCsv, buildTsv, parsePastedGrid } from "../../panels/editors/shared/tableClipboard"
import { commonRootPath, dropMappingHeader } from "../../panels/editors/outputPathTools"

describe("buildCsv", () => {
  it("joins cells with commas and rows with newlines", () => {
    expect(buildCsv([["a", "b"], ["c", "d"]])).toBe("a,b\nc,d")
  })
  it("quotes cells containing a comma, quote, or newline (RFC-4180)", () => {
    expect(buildCsv([["x,y", 'has "q"', "line\nbreak"]])).toBe(
      '"x,y","has ""q""","line\nbreak"',
    )
  })
  it("a TSV round-trips through the paste parser; CSV is comma-joined", () => {
    const grid = [["col", "path"], ["a", "$[:].a"]]
    expect(parsePastedGrid(buildTsv(grid))).toEqual(grid)
    expect(buildCsv(grid)).toBe("col,path\na,$[:].a")
  })
})

describe("commonRootPath", () => {
  it("returns $[:] for no rows or a single leaf path", () => {
    expect(commonRootPath([])).toBe("$[:]")
    expect(commonRootPath(["$[:].policy_id"])).toBe("$[:]")
  })
  it("trims the common prefix back to a path boundary", () => {
    expect(commonRootPath(["$[:].a.x", "$[:].a.y"])).toBe("$[:].a")
    expect(commonRootPath(["$[:].policy_id", "$[:].premium"])).toBe("$[:]")
  })
})

describe("dropMappingHeader", () => {
  it("drops a recognised column/path header row", () => {
    expect(dropMappingHeader([["column", "path", "enabled"], ["a", "$[:].a", "true"]])).toEqual([
      ["a", "$[:].a", "true"],
    ])
    expect(dropMappingHeader([["source_column", "output_path"], ["a", "$[:].a"]])).toEqual([
      ["a", "$[:].a"],
    ])
  })
  it("keeps every row when the first row is data, not a header", () => {
    const grid = [["a", "$[:].a"], ["b", "$[:].b"]]
    expect(dropMappingHeader(grid)).toEqual(grid)
  })
})
