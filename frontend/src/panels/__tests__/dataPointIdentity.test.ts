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
