import { describe, expect, it } from "vitest"

import { nodeTypes } from "../nodeTypeRegistry"
import { NODE_TYPES } from "../nodeTypes"

describe("nodeTypeRegistry", () => {
  // A node type missing from this registry renders as React Flow's unstyled
  // default box on the canvas (no card, no chip, no handles wiring) — found
  // live when dataInput/dataOutput first landed without entries. Every wire
  // name must map to a component.
  it("registers a component for every NODE_TYPES value", () => {
    const registered = new Set(Object.keys(nodeTypes))
    const missing = Object.values(NODE_TYPES).filter((t) => !registered.has(t))
    expect(missing).toEqual([])
  })

  it("has no entries for unknown node types", () => {
    const known = new Set(Object.values(NODE_TYPES) as string[])
    const unknown = Object.keys(nodeTypes).filter((t) => !known.has(t))
    expect(unknown).toEqual([])
  })
})
