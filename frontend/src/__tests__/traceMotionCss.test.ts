import { describe, expect, it } from "vitest"
import { readFileSync } from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"

const HERE = path.dirname(fileURLToPath(import.meta.url))
const CSS = readFileSync(path.resolve(HERE, "..", "index.css"), "utf8")

describe("graph visual effect CSS", () => {
  it("disables React Flow node and edge-path transitions for trace-motion-lite elements", () => {
    expect(CSS).toMatch(/\.react-flow__node\.trace-motion-lite\s*\{[^}]*transition:\s*none !important;[^}]*\}/)
    expect(CSS).toMatch(
      /\.react-flow__edge\.trace-motion-lite\s+\.react-flow__edge-path\s*\{[^}]*transition:\s*none !important;[^}]*filter:\s*none !important;[^}]*\}/,
    )
  })

  it("scopes large-graph visual effect reductions to the React Flow canvas", () => {
    expect(CSS).toMatch(/\.react-flow\.graph-effects-lite\s+\.react-flow__node\s*\{[^}]*transition:\s*none !important;[^}]*\}/)
    expect(CSS).toMatch(
      /\.react-flow\.graph-effects-lite\s+\.react-flow__node:hover:not\(\.selected\)\s*>\s*div\s*\{[^}]*box-shadow:\s*none !important;[^}]*\}/,
    )
    expect(CSS).toMatch(
      /\.react-flow\.graph-effects-lite\s+\.react-flow__edge-path\s*\{[^}]*transition:\s*none !important;[^}]*filter:\s*none !important;[^}]*\}/,
    )
    expect(CSS).toMatch(
      /\.react-flow\.graph-effects-lite\s+\.react-flow__edge\.selected\s+\.react-flow__edge-path\s*\{[^}]*filter:\s*none !important;[^}]*\}/,
    )
    expect(CSS).toMatch(/\.react-flow\.graph-effects-lite\s+\.react-flow__controls\s*\{[^}]*box-shadow:\s*none !important;[^}]*\}/)
    expect(CSS).toMatch(/\.react-flow\.graph-effects-lite\s+\.animate-pulse-dot\s*\{[^}]*animation:\s*none !important;[^}]*\}/)
  })
})
