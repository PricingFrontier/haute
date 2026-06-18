import { describe, it, expect } from "vitest"
import type { Node } from "@xyflow/react"

import { diffPipelineNodes } from "../graphDiff"

function node(id: string, over: { label?: string; nodeType?: string; config?: unknown; position?: { x: number; y: number } } = {}): Node {
  return {
    id,
    position: over.position ?? { x: 0, y: 0 },
    data: { label: over.label ?? id, nodeType: over.nodeType ?? "polars", config: over.config ?? {} },
  } as Node
}

describe("diffPipelineNodes", () => {
  it("flags a node only in the new graph as added", () => {
    const d = diffPipelineNodes([node("a")], [node("a"), node("b")])
    expect([...d.added]).toEqual(["b"])
    expect([...d.removed]).toEqual([])
    expect([...d.changed]).toEqual([])
  })

  it("flags a node only in the old graph as removed", () => {
    const d = diffPipelineNodes([node("a"), node("b")], [node("a")])
    expect([...d.removed]).toEqual(["b"])
    expect([...d.added]).toEqual([])
  })

  it("flags a node whose config changed", () => {
    const d = diffPipelineNodes(
      [node("a", { config: { expr: "x" } })],
      [node("a", { config: { expr: "y" } })],
    )
    expect([...d.changed]).toEqual(["a"])
    expect([...d.added]).toEqual([])
    expect([...d.removed]).toEqual([])
  })

  it("flags a node whose type changed", () => {
    const d = diffPipelineNodes([node("a", { nodeType: "polars" })], [node("a", { nodeType: "sql" })])
    expect([...d.changed]).toEqual(["a"])
  })

  it("does NOT flag a node that only moved (same content, different position)", () => {
    const d = diffPipelineNodes(
      [node("a", { position: { x: 0, y: 0 } })],
      [node("a", { position: { x: 500, y: 300 } })],
    )
    expect([...d.changed]).toEqual([])
    expect([...d.added]).toEqual([])
    expect([...d.removed]).toEqual([])
  })

  it("treats config equal regardless of key order (canonicalised)", () => {
    const d = diffPipelineNodes(
      [node("a", { config: { x: 1, y: 2 } })],
      [node("a", { config: { y: 2, x: 1 } })],
    )
    expect([...d.changed]).toEqual([])
  })

  it("handles a combined add + remove + change", () => {
    const d = diffPipelineNodes(
      [node("keep"), node("gone"), node("edit", { config: { v: 1 } })],
      [node("keep"), node("new"), node("edit", { config: { v: 2 } })],
    )
    expect([...d.added]).toEqual(["new"])
    expect([...d.removed]).toEqual(["gone"])
    expect([...d.changed]).toEqual(["edit"])
  })
})
