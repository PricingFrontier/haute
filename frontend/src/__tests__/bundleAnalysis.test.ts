import { describe, expect, it } from "vitest"
import { mkdtempSync, rmSync, writeFileSync } from "node:fs"
import { join } from "node:path"
import { tmpdir } from "node:os"

import * as analyzer from "../../scripts/analyze-bundle-sourcemaps.mjs"

const BASE64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"

function encodeVlq(value: number): string {
  let vlq = Math.abs(value) << 1
  if (value < 0) vlq += 1
  let encoded = ""
  do {
    let digit = vlq & 31
    vlq >>>= 5
    if (vlq > 0) digit |= 32
    encoded += BASE64[digit]
  } while (vlq > 0)
  return encoded
}

function encodeMappings(lines: number[][][]): string {
  let previousSource = 0
  let previousOriginalLine = 0
  let previousOriginalColumn = 0

  return lines.map((line) => {
    let previousGeneratedColumn = 0
    return line.map((segment) => {
      const [generatedColumn, source, originalLine, originalColumn] = segment
      const encoded = [
        encodeVlq(generatedColumn - previousGeneratedColumn),
        encodeVlq(source - previousSource),
        encodeVlq(originalLine - previousOriginalLine),
        encodeVlq(originalColumn - previousOriginalColumn),
      ].join("")
      previousGeneratedColumn = generatedColumn
      previousSource = source
      previousOriginalLine = originalLine
      previousOriginalColumn = originalColumn
      return encoded
    }).join(",")
  }).join(";")
}

describe("bundle source-map analysis", () => {
  it("attributes generated ranges to source-map sources within a chunk", () => {
    const result = analyzer.analyzeSourceMapChunk({
      chunkName: "chunk.js",
      generatedCode: "0123456789012345678901234",
      sourceMap: {
        version: 3,
        file: "chunk.js",
        sources: ["src/a.ts", "node_modules/lib/index.js"],
        sourcesContent: ["export const a = 1", "export const lib = 2"],
        mappings: encodeMappings([[
          [0, 0, 0, 0],
          [10, 1, 0, 0],
        ]]),
      },
    })

    expect(result.totalMappedGeneratedBytes).toBe(25)
    expect(result.contributors.map((entry) => [entry.source, entry.generatedBytes])).toEqual([
      ["node_modules/lib/index.js", 15],
      ["src/a.ts", 10],
    ])
    expect(result.contributors[0]?.originalBytes).toBe(Buffer.byteLength("export const lib = 2"))
  })

  it("measures mapped generated spans as UTF-8 bytes rather than source-map columns", () => {
    const result = analyzer.analyzeSourceMapChunk({
      chunkName: "unicode.js",
      generatedCode: "αβγδεZ",
      sourceMap: {
        version: 3,
        file: "unicode.js",
        sources: ["src/unicode.ts", "src/ascii.ts"],
        sourcesContent: ["export const letters = 'αβγδε'", "export const z = 'Z'"],
        mappings: encodeMappings([[
          [0, 0, 0, 0],
          [5, 1, 0, 0],
        ]]),
      },
    })

    expect(result.contributors.map((entry) => [entry.source, entry.generatedBytes])).toEqual([
      ["src/unicode.ts", Buffer.byteLength("αβγδε")],
      ["src/ascii.ts", Buffer.byteLength("Z")],
    ])
  })

  it("fails loudly for invalid source maps", () => {
    expect(() =>
      analyzer.analyzeSourceMapChunk({
        chunkName: "bad.js",
        generatedCode: "console.log(1)",
        sourceMap: {
          version: 3,
          sources: ["src/a.ts"],
          mappings: 42,
        },
      }),
    ).toThrow("bad.js source map must include a mappings string.")
  })

  it("fails loudly for unsupported source map versions", () => {
    expect(() =>
      analyzer.analyzeSourceMapChunk({
        chunkName: "legacy.js",
        generatedCode: "console.log(1)",
        sourceMap: {
          version: 2,
          sources: ["src/a.ts"],
          mappings: "",
        },
      }),
    ).toThrow("legacy.js source map must use version 3.")
  })

  it("fails loudly when a JavaScript asset has no matching source map", () => {
    const dir = mkdtempSync(join(tmpdir(), "haute-bundle-analysis-"))
    try {
      writeFileSync(join(dir, "chunk.js"), "console.log(1)")

      expect(() => analyzer.analyzeBundleDirectory(dir)).toThrow(
        "Missing source map for JavaScript asset chunk.js.",
      )
    } finally {
      rmSync(dir, { recursive: true, force: true })
    }
  })

  it("formats largest contributors per chunk", () => {
    const report = analyzer.formatAnalysisReport([
      {
        chunkName: "chunk.js",
        generatedBytes: 100,
        totalMappedGeneratedBytes: 80,
        contributors: [
          { source: "src/big.ts", generatedBytes: 60, originalBytes: 120 },
          { source: "src/small.ts", generatedBytes: 20, originalBytes: 40 },
        ],
      },
    ], { topN: 1 })

    expect(report).toContain("chunk.js: 0.1 KiB raw, 0.1 KiB mapped")
    expect(report).toContain("src/big.ts: 0.1 KiB generated")
    expect(report).not.toContain("src/small.ts")
  })
})
