import { describe, expect, it } from "vitest"
import type { SimpleEdge, SimpleNode } from "../../editors"
import { buildExploreCacheIdentity } from "../cacheIdentity"

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
  return buildExploreCacheIdentity({ node, allNodes, edges, preamble })
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
})
