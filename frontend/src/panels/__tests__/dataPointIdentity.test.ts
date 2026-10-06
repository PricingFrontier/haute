import { describe, expect, it } from "vitest"
import type { SimpleEdge, SimpleNode } from "../editors"
import { buildNodeDataCacheIdentity } from "../dataPointIdentity"

const source: SimpleNode = {
  id: "source_1", type: "dataInput",
  data: { label: "Source", description: "", nodeType: "dataInput", config: { path: "claims.parquet" } },
}
const explore: SimpleNode = {
  id: "explore_1", type: "explore",
  data: { label: "Explore", description: "", nodeType: "explore", config: { code: "df = df" } },
}
const downstream: SimpleNode = {
  id: "output_1", type: "dataOutput",
  data: { label: "Output", description: "", nodeType: "dataOutput", config: { path: "out.parquet" } },
}
const edges: SimpleEdge[] = [
  { id: "source-explore", source: "source_1", target: "explore_1" },
  { id: "explore-output", source: "explore_1", target: "output_1" },
]

function identity(node = explore, allNodes = [source, node, downstream], preamble = "import polars as pl") {
  return buildNodeDataCacheIdentity({ node, allNodes, edges, preamble })
}

describe("buildExploreCacheIdentity", () => {
  // Ported from main's cacheIdentity test when this module was renamed: the
  // behaviour it covers — authored steps counting, their generated code not —
  // lives in dataAffectingConfig here.
  it.each([
    ["the Explore node itself", "explore"],
    ["a stepped ancestor", "source"],
  ])("ignores generated step code but not the steps themselves, on %s", (_label, which) => {
    const steps = [{ id: "l", kind: "limit", n: 2 }]
    const stepped = (config: Record<string, unknown>) =>
      which === "explore"
        ? { ...explore, data: { ...explore.data, config } }
        : { ...source, data: { ...source.data, config: { path: "claims.parquet", ...config } } }

    const authored = stepped({ steps, code: "" })
    const nodes = (node: SimpleNode) =>
      which === "explore" ? [source, node, downstream] : [node, explore, downstream]
    const target = (node: SimpleNode) => (which === "explore" ? node : explore)

    // The render endpoint filling in the generated code is not a data change.
    const rendered = stepped({ steps, code: "df = df.head(2)", _steps_error: "" })
    expect(identity(target(rendered), nodes(rendered))).toEqual(
      identity(target(authored), nodes(authored)),
    )

    // Editing the steps is.
    const edited = stepped({ steps: [{ id: "l", kind: "limit", n: 3 }], code: "" })
    expect(identity(target(edited), nodes(edited))).not.toEqual(
      identity(target(authored), nodes(authored)),
    )
  })

  it("excludes display config and downstream graph changes", () => {
    const displayOnly = {
      ...explore,
      data: {
        ...explore.data,
        config: {
          ...explore.data.config,
          overview: { schema: true },
          pivot_formulas: [{ id: "formula_1", expression: 'pl.col("paid").sum()' }],
          pivots: [],
          charts: [],
        },
      },
    }
    const changedDownstream = {
      ...downstream,
      data: { ...downstream.data, config: { path: "changed.parquet" } },
    }

    expect(identity(displayOnly, [source, displayOnly, changedDownstream])).toEqual(identity())
  })

  it("changes for upstream code, Explore code, and preamble changes", () => {
    const changedSource = {
      ...source,
      data: { ...source.data, config: { path: "renewals.parquet" } },
    }
    const changedExplore = {
      ...explore,
      data: { ...explore.data, config: { code: "df = df.filter(pl.col('premium') > 0)" } },
    }

    expect(identity(changedExplore)).not.toEqual(identity())
    expect(identity(explore, [changedSource, explore, downstream])).not.toEqual(identity())
    expect(identity(explore, [source, explore, downstream], "import pandas as pd")).not.toEqual(identity())
  })
})

describe("buildNodeDataCacheIdentity for a consumer that reads its input", () => {
  const withConfig = (node: SimpleNode, config: Record<string, unknown>): SimpleNode => ({
    ...node,
    data: { ...node.data, config },
  })
  const consumerIdentity = (node: SimpleNode) =>
    buildNodeDataCacheIdentity({
      node,
      allNodes: [source, node],
      edges: [{ id: "source-consumer", source: "source_1", target: node.id }],
    })

  const banding: SimpleNode = {
    id: "banding_1", type: "banding",
    data: { label: "Banding", description: "", nodeType: "banding", config: {} },
  }
  const factor = { banding: "categorical", column: "cover", outputColumn: "cover_band", default: null }

  it("keeps a Banding node's identity through edits to its rules, but not its column", () => {
    const base = consumerIdentity(withConfig(banding, { factors: [{ ...factor, rules: [] }] }))
    const ruled = withConfig(banding, {
      factors: [{ ...factor, outputColumn: "renamed", rules: [{ value: "comp", assignment: "C" }] }],
    })
    expect(consumerIdentity(ruled)).toEqual(base)

    const otherColumn = withConfig(banding, { factors: [{ ...factor, column: "region", rules: [] }] })
    expect(consumerIdentity(otherColumn)).not.toEqual(base)
  })

  it("keeps a Rating Step node's identity through edits to its entries, but not its factors", () => {
    const rating: SimpleNode = {
      id: "rating_1", type: "ratingStep",
      data: { label: "Rating", description: "", nodeType: "ratingStep", config: {} },
    }
    const table = (factors: string[], value: string) => ({
      factors, outputColumn: "rate", entries: [{ age: "young", value }],
    })
    const base = consumerIdentity(withConfig(rating, { tables: [table(["age"], "1.0")] }))
    expect(consumerIdentity(withConfig(rating, { tables: [table(["age"], "1.2")] }))).toEqual(base)
    expect(consumerIdentity(withConfig(rating, { tables: [table(["age", "region"], "1.0")] }))).not.toEqual(base)
  })

  it("still changes when an upstream Banding node's rules change", () => {
    const upstream = withConfig(banding, { factors: [{ ...factor, rules: [{ value: "a", assignment: "A" }] }] })
    const edited = withConfig(banding, { factors: [{ ...factor, rules: [{ value: "b", assignment: "B" }] }] })
    const reader = (band: SimpleNode) =>
      buildNodeDataCacheIdentity({
        node: explore,
        allNodes: [source, band, explore],
        edges: [
          { id: "source-banding", source: "source_1", target: "banding_1" },
          { id: "banding-explore", source: "banding_1", target: "explore_1" },
        ],
      })
    expect(reader(edited)).not.toEqual(reader(upstream))
  })
})

describe("buildNodeDataCacheIdentity execution dependencies", () => {
  const original: SimpleNode = {
    id: "original_1", type: "polars",
    data: { label: "Original", description: "", nodeType: "polars", config: { code: "df = df.head(5)" } },
  }
  const instance: SimpleNode = {
    id: "instance_1", type: "polars",
    data: { label: "Instance", description: "", nodeType: "polars", config: { instanceOf: "original_1" } },
  }
  const consumer: SimpleNode = {
    id: "banding_1", type: "banding",
    data: { label: "Banding", description: "", nodeType: "banding", config: { factors: [] } },
  }
  // The original is not upstream of the consumer: only the instance is.
  const instanceEdges: SimpleEdge[] = [
    { id: "source-instance", source: "source_1", target: "instance_1" },
    { id: "instance-banding", source: "instance_1", target: "banding_1" },
  ]

  function instanceIdentity(originalNode = original) {
    return buildNodeDataCacheIdentity({
      node: consumer,
      allNodes: [source, originalNode, instance, consumer],
      edges: instanceEdges,
      preamble: "",
    })
  }

  it("covers the configuration an instance actually executes with", () => {
    const identityNodes = instanceIdentity().nodes as { id: string }[]

    expect(identityNodes.map((entry) => entry.id)).toContain("original_1")
  })

  it("changes when the original of an upstream instance changes", () => {
    const edited: SimpleNode = {
      ...original,
      data: { ...original.data, config: { code: "df = df.head(50)" } },
    }

    expect(JSON.stringify(instanceIdentity(edited))).not.toBe(JSON.stringify(instanceIdentity()))
  })

  it("changes when an edge moves to another port of the same producer", () => {
    const base = buildNodeDataCacheIdentity({
      node: consumer,
      allNodes: [source, consumer],
      edges: [{ id: "source-banding", source: "source_1", target: "banding_1", sourceHandle: "orders" }],
      preamble: "",
    })
    const moved = buildNodeDataCacheIdentity({
      node: consumer,
      allNodes: [source, consumer],
      edges: [{ id: "source-banding", source: "source_1", target: "banding_1", sourceHandle: "claims" }],
      preamble: "",
    })

    expect(JSON.stringify(moved)).not.toBe(JSON.stringify(base))
  })
})
