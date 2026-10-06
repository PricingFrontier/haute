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

  it("keeps edgeJoin handles visually quiet on hover", () => {
    expect(CSS).toMatch(
      /\.react-flow__handle\.edge-join-handle--suppress-hover:hover\s*\{[^}]*width:\s*2px !important;[^}]*height:\s*2px !important;[^}]*background:\s*transparent !important;[^}]*border:\s*none !important;[^}]*\}/,
    )
    expect(CSS).toMatch(
      /\.react-flow__handle\.edge-join-handle--suppress-hover::after\s*\{[^}]*width:\s*2px;[^}]*height:\s*2px;[^}]*\}/,
    )
  })

  it("keeps the edgeJoin output handle easy to drag while visually quiet", () => {
    expect(CSS).toMatch(
      /\.react-flow__handle\.edge-join-output-handle\.edge-join-handle--suppress-hover::after\s*\{[^}]*left:\s*calc\(50% \+ 12px\);[^}]*width:\s*28px;[^}]*height:\s*28px;[^}]*\}/,
    )
  })

  it("permanently draws the pale-accent inside half of each connector's hover circle", () => {
    expect(CSS).toMatch(
      /\.react-flow__handle:hover,\s*\.react-flow__handle\.output-origin-handle::before,\s*\.react-flow__handle\.input-origin-handle::before\s*\{[^}]*width:\s*10px !important;[^}]*height:\s*10px !important;[^}]*box-sizing:\s*border-box;[^}]*border:\s*2px solid var\(--bg-elevated\) !important;[^}]*border-radius:\s*50%;[^}]*\}/,
    )
    expect(CSS).toMatch(
      /\.react-flow__handle\.output-origin-handle::before,\s*\.react-flow__handle\.input-origin-handle::before\s*\{[^}]*left:\s*50%;[^}]*transform:\s*translate\(-50%, -50%\);[^}]*background:\s*color-mix\(in srgb, currentColor 70%, var\(--text-primary\)\);[^}]*pointer-events:\s*none;[^}]*\}/,
    )
    expect(CSS).toMatch(
      /\.react-flow__handle\.output-origin-handle::before\s*\{[^}]*clip-path:\s*inset\(0 50% 0 0\);[^}]*\}/,
    )
    expect(CSS).toMatch(
      /\.react-flow__handle\.input-origin-handle::before\s*\{[^}]*clip-path:\s*inset\(0 0 0 50%\);[^}]*\}/,
    )
    expect(CSS).toMatch(
      /\.react-flow__handle\.output-origin-handle:hover,\s*\.react-flow__handle\.input-origin-handle:hover\s*\{[^}]*width:\s*2px !important;[^}]*height:\s*2px !important;[^}]*background:\s*transparent !important;[^}]*border:\s*none !important;[^}]*\}/,
    )
    expect(CSS).toMatch(
      /\.react-flow__handle\.output-origin-handle:hover::before,\s*\.react-flow__handle\.input-origin-handle:hover::before\s*\{[^}]*clip-path:\s*inset\(0\);[^}]*background:\s*currentColor;[^}]*\}/,
    )
  })
})
