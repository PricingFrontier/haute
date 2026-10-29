import { describe, it, expect } from "vitest"
import {
  findTargetStep,
  collapsePassthroughs,
  isSourceLikeTraceStep,
} from "../traceGrouping"
import { isTraceGeneratedColumnOriginType, isTraceSourceNodeType } from "../../../trace/traceOrigins"
import type { TraceStep } from "../../../types/trace"

function makeStep(overrides: Partial<TraceStep> = {}): TraceStep {
  return {
    node_id: "n1",
    node_name: "Transform 1",
    node_type: "polars",
    schema_diff: {
      columns_added: [],
      columns_removed: [],
      columns_modified: [],
      columns_passed: ["age"],
    },
    input_values: { age: 25 },
    output_values: { age: 25, premium: 100 },
    topological_rank: 0,
    column_relevant: true,
    contributed_columns: [], derivations: [],
    ...overrides,
  }
}

// ---------------------------------------------------------------------------
// findTargetStep
// ---------------------------------------------------------------------------
describe("findTargetStep", () => {
  it("prefers the step the lineage says computed the value over a later table sharing the column", () => {
    const addsQuoteId = { columns_added: ["quote_id"], columns_removed: [], columns_modified: [], columns_passed: [] }
    const steps = [
      makeStep({ node_id: "nb_batch_input", node_type: "dataInput", schema_diff: addsQuoteId, contributed_columns: ["quote_id"] }),
      // A joined-in table also holds quote_id, but the left join kept the base row's value.
      makeStep({ node_id: "premiums", node_type: "dataInput", schema_diff: addsQuoteId, column_relevant: false }),
      makeStep({
        node_id: "apply",
        schema_diff: { columns_added: [], columns_removed: [], columns_modified: [], columns_passed: ["quote_id"] },
      }),
    ]

    expect(findTargetStep(steps, "quote_id")?.node_id).toBe("nb_batch_input")
  })


  it("finds the step where traced column is in columns_added", () => {
    const steps = [
      makeStep({ node_id: "n1", node_name: "Source", node_type: "dataInput" }),
      makeStep({
        node_id: "n2",
        node_name: "Creator",
        schema_diff: {
          columns_added: ["premium"],
          columns_removed: [],
          columns_modified: [],
          columns_passed: ["age"],
        },
      }),
    ]
    const result = findTargetStep(steps, "premium")
    expect(result).not.toBeNull()
    expect(result!.node_id).toBe("n2")
    expect(result!.node_name).toBe("Creator")
  })

  it("finds the step where traced column is in columns_modified", () => {
    const steps = [
      makeStep({
        node_id: "n1",
        node_name: "Source",
        node_type: "dataInput",
        schema_diff: {
          columns_added: ["premium"],
          columns_removed: [],
          columns_modified: [],
          columns_passed: [],
        },
      }),
      makeStep({
        node_id: "n2",
        node_name: "Modifier",
        schema_diff: {
          columns_added: [],
          columns_removed: [],
          columns_modified: ["premium"],
          columns_passed: ["age"],
        },
      }),
    ]
    const result = findTargetStep(steps, "premium")
    expect(result).not.toBeNull()
    expect(result!.node_id).toBe("n2")
  })

  it("returns the LAST step that creates/modifies the column (not the first)", () => {
    const steps = [
      makeStep({
        node_id: "n1",
        node_name: "Creator",
        schema_diff: {
          columns_added: ["premium"],
          columns_removed: [],
          columns_modified: [],
          columns_passed: [],
        },
      }),
      makeStep({
        node_id: "n2",
        node_name: "First Modifier",
        schema_diff: {
          columns_added: [],
          columns_removed: [],
          columns_modified: ["premium"],
          columns_passed: ["age"],
        },
      }),
      makeStep({
        node_id: "n3",
        node_name: "Last Modifier",
        schema_diff: {
          columns_added: [],
          columns_removed: [],
          columns_modified: ["premium"],
          columns_passed: ["age"],
        },
      }),
    ]
    const result = findTargetStep(steps, "premium")
    expect(result).not.toBeNull()
    expect(result!.node_id).toBe("n3")
    expect(result!.node_name).toBe("Last Modifier")
  })

  it("returns null when column is null", () => {
    const steps = [
      makeStep({
        node_id: "n1",
        schema_diff: {
          columns_added: ["premium"],
          columns_removed: [],
          columns_modified: [],
          columns_passed: [],
        },
      }),
    ]
    const result = findTargetStep(steps, null)
    expect(result).toBeNull()
  })

  it("returns null when no step creates the column", () => {
    const steps = [
      makeStep({
        node_id: "n1",
        schema_diff: {
          columns_added: [],
          columns_removed: [],
          columns_modified: [],
          columns_passed: ["age", "premium"],
        },
      }),
      makeStep({
        node_id: "n2",
        schema_diff: {
          columns_added: ["other_col"],
          columns_removed: [],
          columns_modified: [],
          columns_passed: ["age", "premium"],
        },
      }),
    ]
    const result = findTargetStep(steps, "premium")
    expect(result).toBeNull()
  })

  it("returns null for empty steps array", () => {
    const result = findTargetStep([], "premium")
    expect(result).toBeNull()
  })

  it("returns null when column is undefined", () => {
    const steps = [
      makeStep({
        node_id: "n1",
        schema_diff: {
          columns_added: ["premium"],
          columns_removed: [],
          columns_modified: [],
          columns_passed: [],
        },
      }),
    ]
    const result = findTargetStep(steps, undefined)
    expect(result).toBeNull()
  })

  it("returns LAST modifier when multiple steps add then modify the column", () => {
    const steps = [
      makeStep({
        node_id: "n1",
        node_name: "Creator",
        schema_diff: {
          columns_added: ["premium"],
          columns_removed: [],
          columns_modified: [],
          columns_passed: [],
        },
      }),
      makeStep({
        node_id: "n2",
        node_name: "Modifier A",
        schema_diff: {
          columns_added: [],
          columns_removed: [],
          columns_modified: ["premium"],
          columns_passed: ["age"],
        },
      }),
      makeStep({
        node_id: "n3",
        node_name: "Passthrough",
        schema_diff: {
          columns_added: [],
          columns_removed: [],
          columns_modified: [],
          columns_passed: ["premium", "age"],
        },
      }),
      makeStep({
        node_id: "n4",
        node_name: "Modifier B",
        schema_diff: {
          columns_added: [],
          columns_removed: [],
          columns_modified: ["premium"],
          columns_passed: ["age"],
        },
      }),
    ]
    const result = findTargetStep(steps, "premium")
    expect(result).not.toBeNull()
    expect(result!.node_id).toBe("n4")
    expect(result!.node_name).toBe("Modifier B")
  })

  it("prefers final step with usable expression when primary step has opaque expression", () => {
    const steps = [
      makeStep({
        node_id: "n1",
        node_name: "Creator",
        schema_diff: {
          columns_added: ["premium"],
          columns_removed: [],
          columns_modified: [],
          columns_passed: [],
        },
        expression: { expression_text: "", expression_type: "opaque", referenced_columns: [] },
      }),
      makeStep({
        node_id: "n2",
        node_name: "Final",
        schema_diff: {
          columns_added: [],
          columns_removed: [],
          columns_modified: [],
          columns_passed: ["premium"],
        },
        expression: { expression_text: "col('age') * 2", expression_type: "polars", referenced_columns: ["age"] },
      }),
    ]
    const result = findTargetStep(steps, "premium")
    expect(result).not.toBeNull()
    expect(result!.node_id).toBe("n2")
  })
})

describe("source-like trace steps", () => {
  it("agrees with the canonical source-only node classification", () => {
    for (const nodeType of ["dataInput", "apiInput", "constant", "scenarioExpander", "polars"]) {
      expect(isSourceLikeTraceStep(makeStep({ node_type: nodeType }))).toBe(isTraceSourceNodeType(nodeType))
    }
  })

  it("keeps scenario expanders as generated-column origins rather than source-only nodes", () => {
    expect(isTraceSourceNodeType("scenarioExpander")).toBe(false)
    expect(isTraceGeneratedColumnOriginType("scenarioExpander")).toBe(true)
  })
})

// ---------------------------------------------------------------------------
// collapsePassthroughs
// ---------------------------------------------------------------------------
describe("collapsePassthroughs", () => {
  it("identifies pass-through steps (column in columns_passed, not added/modified)", () => {
    const steps = [
      makeStep({
        node_id: "s1",
        node_name: "Source",
        node_type: "dataInput",
        schema_diff: {
          columns_added: ["premium"],
          columns_removed: [],
          columns_modified: [],
          columns_passed: [],
        },
      }),
      makeStep({
        node_id: "p1",
        node_name: "Passthrough",
        node_type: "polars",
        schema_diff: {
          columns_added: [],
          columns_removed: [],
          columns_modified: [],
          columns_passed: ["premium", "age"],
        },
      }),
      makeStep({
        node_id: "t1",
        node_name: "Final",
        node_type: "polars",
        schema_diff: {
          columns_added: [],
          columns_removed: [],
          columns_modified: ["premium"],
          columns_passed: ["age"],
        },
      }),
    ]
    const result = collapsePassthroughs(steps, "premium")

    // The passthrough step should be collapsed
    const flatSteps = result.flatMap((entry) =>
      "collapsed" in entry ? entry.collapsed : [entry],
    )
    expect(flatSteps.some((s) => s.node_id === "p1")).toBe(true)
  })

  it("consecutive passthroughs collapsed into one group", () => {
    const steps = [
      makeStep({
        node_id: "s1",
        node_name: "Source",
        node_type: "dataInput",
        schema_diff: {
          columns_added: ["premium"],
          columns_removed: [],
          columns_modified: [],
          columns_passed: [],
        },
      }),
      makeStep({
        node_id: "p1",
        node_name: "Pass 1",
        node_type: "polars",
        schema_diff: {
          columns_added: [],
          columns_removed: [],
          columns_modified: [],
          columns_passed: ["premium"],
        },
      }),
      makeStep({
        node_id: "p2",
        node_name: "Pass 2",
        node_type: "polars",
        schema_diff: {
          columns_added: [],
          columns_removed: [],
          columns_modified: [],
          columns_passed: ["premium"],
        },
      }),
      makeStep({
        node_id: "t1",
        node_name: "Final",
        node_type: "polars",
        schema_diff: {
          columns_added: [],
          columns_removed: [],
          columns_modified: ["premium"],
          columns_passed: ["age"],
        },
      }),
    ]
    const result = collapsePassthroughs(steps, "premium")

    // Should have 3 entries: source, collapsed group, final
    const collapsedEntries = result.filter((e) => "collapsed" in e)
    expect(collapsedEntries).toHaveLength(1)
    expect((collapsedEntries[0] as { collapsed: TraceStep[] }).collapsed).toHaveLength(2)
  })

  it("non-consecutive passthroughs: separate collapsed groups", () => {
    const steps = [
      makeStep({
        node_id: "s1",
        node_name: "Source",
        node_type: "dataInput",
        schema_diff: {
          columns_added: ["premium"],
          columns_removed: [],
          columns_modified: [],
          columns_passed: [],
        },
      }),
      makeStep({
        node_id: "p1",
        node_name: "Pass 1",
        node_type: "polars",
        schema_diff: {
          columns_added: [],
          columns_removed: [],
          columns_modified: [],
          columns_passed: ["premium"],
        },
      }),
      makeStep({
        node_id: "m1",
        node_name: "Modifier",
        node_type: "polars",
        schema_diff: {
          columns_added: [],
          columns_removed: [],
          columns_modified: ["premium"],
          columns_passed: ["age"],
        },
      }),
      makeStep({
        node_id: "p2",
        node_name: "Pass 2",
        node_type: "polars",
        schema_diff: {
          columns_added: [],
          columns_removed: [],
          columns_modified: [],
          columns_passed: ["premium"],
        },
      }),
    ]
    const result = collapsePassthroughs(steps, "premium")

    const collapsedEntries = result.filter((e) => "collapsed" in e)
    expect(collapsedEntries).toHaveLength(2)
  })

  it("step that creates the column is NOT collapsed", () => {
    const steps = [
      makeStep({
        node_id: "s1",
        node_name: "Creator",
        node_type: "dataInput",
        schema_diff: {
          columns_added: ["premium"],
          columns_removed: [],
          columns_modified: [],
          columns_passed: [],
        },
      }),
      makeStep({
        node_id: "p1",
        node_name: "Pass",
        node_type: "polars",
        schema_diff: {
          columns_added: [],
          columns_removed: [],
          columns_modified: [],
          columns_passed: ["premium"],
        },
      }),
    ]
    const result = collapsePassthroughs(steps, "premium")

    // Creator should be a standalone entry, not collapsed
    const nonCollapsed = result.filter((e) => !("collapsed" in e))
    expect(nonCollapsed.some((e) => (e as TraceStep).node_id === "s1")).toBe(true)
  })

  it("step that modifies the column is NOT collapsed", () => {
    const steps = [
      makeStep({
        node_id: "s1",
        node_name: "Source",
        node_type: "dataInput",
        schema_diff: {
          columns_added: ["premium"],
          columns_removed: [],
          columns_modified: [],
          columns_passed: [],
        },
      }),
      makeStep({
        node_id: "m1",
        node_name: "Modifier",
        node_type: "polars",
        schema_diff: {
          columns_added: [],
          columns_removed: [],
          columns_modified: ["premium"],
          columns_passed: ["age"],
        },
      }),
    ]
    const result = collapsePassthroughs(steps, "premium")

    // No collapsed groups since neither step is a passthrough
    const collapsedEntries = result.filter((e) => "collapsed" in e)
    expect(collapsedEntries).toHaveLength(0)
  })

  it("source step is NOT collapsed", () => {
    const steps = [
      makeStep({
        node_id: "s1",
        node_name: "Source",
        node_type: "dataInput",
        schema_diff: {
          columns_added: [],
          columns_removed: [],
          columns_modified: [],
          columns_passed: ["premium"],
        },
      }),
      makeStep({
        node_id: "t1",
        node_name: "Transform",
        node_type: "polars",
        schema_diff: {
          columns_added: [],
          columns_removed: [],
          columns_modified: ["premium"],
          columns_passed: [],
        },
      }),
    ]
    const result = collapsePassthroughs(steps, "premium")

    // Source should not be collapsed even if column is only in columns_passed
    const nonCollapsed = result.filter((e) => !("collapsed" in e))
    expect(nonCollapsed.some((e) => (e as TraceStep).node_type === "dataInput")).toBe(true)
  })

  it("all steps are pass-through: all collapsed except first and last", () => {
    const steps = [
      makeStep({
        node_id: "p1",
        node_name: "Pass 1",
        node_type: "polars",
        schema_diff: {
          columns_added: [],
          columns_removed: [],
          columns_modified: [],
          columns_passed: ["premium"],
        },
      }),
      makeStep({
        node_id: "p2",
        node_name: "Pass 2",
        node_type: "polars",
        schema_diff: {
          columns_added: [],
          columns_removed: [],
          columns_modified: [],
          columns_passed: ["premium"],
        },
      }),
      makeStep({
        node_id: "p3",
        node_name: "Pass 3",
        node_type: "polars",
        schema_diff: {
          columns_added: [],
          columns_removed: [],
          columns_modified: [],
          columns_passed: ["premium"],
        },
      }),
      makeStep({
        node_id: "p4",
        node_name: "Pass 4",
        node_type: "polars",
        schema_diff: {
          columns_added: [],
          columns_removed: [],
          columns_modified: [],
          columns_passed: ["premium"],
        },
      }),
    ]
    const result = collapsePassthroughs(steps, "premium")

    // First and last should be preserved, middle ones collapsed
    const nonCollapsed = result.filter((e) => !("collapsed" in e)) as TraceStep[]
    expect(nonCollapsed.length).toBeGreaterThanOrEqual(2)
    expect(nonCollapsed[0].node_id).toBe("p1")
    expect(nonCollapsed[nonCollapsed.length - 1].node_id).toBe("p4")
  })

  it("no passthroughs: nothing collapsed", () => {
    const steps = [
      makeStep({
        node_id: "s1",
        node_name: "Source",
        node_type: "dataInput",
        schema_diff: {
          columns_added: ["premium"],
          columns_removed: [],
          columns_modified: [],
          columns_passed: [],
        },
      }),
      makeStep({
        node_id: "m1",
        node_name: "Modifier",
        node_type: "polars",
        schema_diff: {
          columns_added: [],
          columns_removed: [],
          columns_modified: ["premium"],
          columns_passed: ["age"],
        },
      }),
    ]
    const result = collapsePassthroughs(steps, "premium")

    const collapsedEntries = result.filter((e) => "collapsed" in e)
    expect(collapsedEntries).toHaveLength(0)
    expect(result).toHaveLength(2)
  })

  it("all passthrough: first and last preserved, middle collapsed", () => {
    const steps = [
      makeStep({
        node_id: "p1",
        node_name: "Pass A",
        node_type: "polars",
        schema_diff: { columns_added: [], columns_removed: [], columns_modified: [], columns_passed: ["premium"] },
      }),
      makeStep({
        node_id: "p2",
        node_name: "Pass B",
        node_type: "polars",
        schema_diff: { columns_added: [], columns_removed: [], columns_modified: [], columns_passed: ["premium"] },
      }),
      makeStep({
        node_id: "p3",
        node_name: "Pass C",
        node_type: "polars",
        schema_diff: { columns_added: [], columns_removed: [], columns_modified: [], columns_passed: ["premium"] },
      }),
    ]
    const result = collapsePassthroughs(steps, "premium")

    const nonCollapsed = result.filter((e) => !("collapsed" in e)) as TraceStep[]
    expect(nonCollapsed).toHaveLength(2)
    expect(nonCollapsed[0].node_id).toBe("p1")
    expect(nonCollapsed[1].node_id).toBe("p3")

    const collapsedEntries = result.filter((e) => "collapsed" in e)
    expect(collapsedEntries).toHaveLength(1)
    expect((collapsedEntries[0] as { collapsed: TraceStep[] }).collapsed).toHaveLength(1)
    expect((collapsedEntries[0] as { collapsed: TraceStep[] }).collapsed[0].node_id).toBe("p2")
  })

  it("source steps (dataInput type) are NOT collapsed even when passthrough", () => {
    const steps = [
      makeStep({
        node_id: "ds1",
        node_name: "Data Source",
        node_type: "dataInput",
        schema_diff: { columns_added: [], columns_removed: [], columns_modified: [], columns_passed: ["premium"] },
      }),
      makeStep({
        node_id: "p1",
        node_name: "Pass",
        node_type: "polars",
        schema_diff: { columns_added: [], columns_removed: [], columns_modified: [], columns_passed: ["premium"] },
      }),
      makeStep({
        node_id: "m1",
        node_name: "Modifier",
        node_type: "polars",
        schema_diff: { columns_added: [], columns_removed: [], columns_modified: ["premium"], columns_passed: [] },
      }),
    ]
    const result = collapsePassthroughs(steps, "premium")

    const nonCollapsed = result.filter((e) => !("collapsed" in e)) as TraceStep[]
    expect(nonCollapsed.some((s) => s.node_id === "ds1")).toBe(true)
  })

  it("apiInput source type is NOT collapsed", () => {
    const steps = [
      makeStep({
        node_id: "api1",
        node_name: "API Input",
        node_type: "apiInput",
        schema_diff: { columns_added: [], columns_removed: [], columns_modified: [], columns_passed: ["premium"] },
      }),
      makeStep({
        node_id: "t1",
        node_name: "Transform",
        node_type: "polars",
        schema_diff: { columns_added: [], columns_removed: [], columns_modified: ["premium"], columns_passed: [] },
      }),
    ]
    const result = collapsePassthroughs(steps, "premium")

    const nonCollapsed = result.filter((e) => !("collapsed" in e)) as TraceStep[]
    expect(nonCollapsed.some((s) => s.node_id === "api1")).toBe(true)
  })

  it("dependency-story mode collapses unpreserved source-like steps", () => {
    const steps = [
      makeStep({
        node_id: "ds1",
        node_name: "Unrelated Source",
        node_type: "dataInput",
        schema_diff: { columns_added: [], columns_removed: [], columns_modified: [], columns_passed: ["premium"] },
      }),
      makeStep({
        node_id: "p1",
        node_name: "Unrelated Pass",
        node_type: "polars",
        schema_diff: { columns_added: [], columns_removed: [], columns_modified: [], columns_passed: ["premium"] },
      }),
      makeStep({
        node_id: "m1",
        node_name: "Target",
        node_type: "polars",
        schema_diff: { columns_added: [], columns_removed: [], columns_modified: ["premium"], columns_passed: [] },
      }),
    ]
    const result = collapsePassthroughs(steps, "premium", new Set(["m1"]), {
      collapseUnpreserved: true,
    })

    const collapsedEntries = result.filter((e) => "collapsed" in e)
    expect(collapsedEntries).toHaveLength(1)
    expect((collapsedEntries[0] as { collapsed: TraceStep[] }).collapsed.map((s) => s.node_id)).toEqual([
      "ds1",
      "p1",
    ])

    const nonCollapsed = result.filter((e) => !("collapsed" in e)) as TraceStep[]
    expect(nonCollapsed.map((s) => s.node_id)).toEqual(["m1"])
  })

  it("step with column not mentioned at all is treated as passthrough", () => {
    const steps = [
      makeStep({
        node_id: "s1",
        node_name: "Source",
        node_type: "dataInput",
        schema_diff: { columns_added: ["premium"], columns_removed: [], columns_modified: [], columns_passed: [] },
      }),
      makeStep({
        node_id: "u1",
        node_name: "Unrelated",
        node_type: "polars",
        schema_diff: { columns_added: ["other"], columns_removed: [], columns_modified: [], columns_passed: [] },
      }),
      makeStep({
        node_id: "t1",
        node_name: "Final",
        node_type: "polars",
        schema_diff: { columns_added: [], columns_removed: [], columns_modified: ["premium"], columns_passed: [] },
      }),
    ]
    const result = collapsePassthroughs(steps, "premium")

    const collapsedEntries = result.filter((e) => "collapsed" in e)
    expect(collapsedEntries).toHaveLength(1)
    expect((collapsedEntries[0] as { collapsed: TraceStep[] }).collapsed[0].node_id).toBe("u1")
  })

  it("empty steps returns empty array", () => {
    const result = collapsePassthroughs([], "premium")
    expect(result).toEqual([])
  })
})
