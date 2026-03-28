import { describe, it, expect } from "vitest"
import {
  findTargetStep,
  groupTraceSteps,
  collapsePassthroughs,
  buildFlowChain,
} from "../traceGrouping"
import type { TraceResult, TraceStep } from "../../../types/trace"

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
    column_relevant: true,
    execution_ms: 5.2,
    ...overrides,
  }
}

function makeTrace(overrides: Partial<TraceResult> = {}): TraceResult {
  return {
    target_node_id: "n2",
    row_index: 0,
    column: "premium",
    output_value: 42.5,
    steps: [
      makeStep({ node_id: "n1", node_name: "Source", node_type: "source" }),
      makeStep({
        node_id: "n2",
        node_name: "Calc",
        node_type: "polars",
        schema_diff: {
          columns_added: ["premium"],
          columns_removed: [],
          columns_modified: [],
          columns_passed: ["age"],
        },
        output_values: { age: 25, premium: 42.5 },
      }),
    ],
    row_id_column: "quote_id",
    row_id_value: "Q001",
    total_nodes_in_pipeline: 5,
    nodes_in_trace: 2,
    execution_ms: 12.3,
    ...overrides,
  }
}

// ---------------------------------------------------------------------------
// findTargetStep
// ---------------------------------------------------------------------------
describe("findTargetStep", () => {
  it("finds the step where traced column is in columns_added", () => {
    const steps = [
      makeStep({ node_id: "n1", node_name: "Source", node_type: "source" }),
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
        node_type: "source",
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
})

// ---------------------------------------------------------------------------
// groupTraceSteps
// ---------------------------------------------------------------------------
describe("groupTraceSteps", () => {
  it("groups source nodes together", () => {
    const steps = [
      makeStep({ node_id: "s1", node_name: "CSV Source", node_type: "source" }),
      makeStep({ node_id: "s2", node_name: "DB Source", node_type: "source" }),
      makeStep({ node_id: "t1", node_name: "Transform", node_type: "polars" }),
    ]
    const groups = groupTraceSteps(steps, "premium")

    const sourceGroup = groups.find((g) =>
      g.steps.some((s) => s.node_type === "source"),
    )
    expect(sourceGroup).toBeDefined()
    expect(sourceGroup!.steps).toHaveLength(2)
    expect(sourceGroup!.steps[0].node_id).toBe("s1")
    expect(sourceGroup!.steps[1].node_id).toBe("s2")
  })

  it("groups polars transform nodes together", () => {
    const steps = [
      makeStep({ node_id: "s1", node_name: "Source", node_type: "source" }),
      makeStep({ node_id: "t1", node_name: "Transform A", node_type: "polars" }),
      makeStep({ node_id: "t2", node_name: "Transform B", node_type: "polars" }),
    ]
    const groups = groupTraceSteps(steps, "premium")

    const polarsGroup = groups.find((g) =>
      g.steps.some((s) => s.node_type === "polars"),
    )
    expect(polarsGroup).toBeDefined()
    expect(polarsGroup!.steps).toHaveLength(2)
  })

  it("keeps execution order within groups", () => {
    const steps = [
      makeStep({ node_id: "t1", node_name: "First", node_type: "polars", execution_ms: 1 }),
      makeStep({ node_id: "t2", node_name: "Second", node_type: "polars", execution_ms: 2 }),
      makeStep({ node_id: "t3", node_name: "Third", node_type: "polars", execution_ms: 3 }),
    ]
    const groups = groupTraceSteps(steps, "premium")

    const polarsGroup = groups.find((g) =>
      g.steps.some((s) => s.node_type === "polars"),
    )
    expect(polarsGroup).toBeDefined()
    expect(polarsGroup!.steps[0].node_name).toBe("First")
    expect(polarsGroup!.steps[1].node_name).toBe("Second")
    expect(polarsGroup!.steps[2].node_name).toBe("Third")
  })

  it("non-adjacent same-type nodes become separate groups", () => {
    const steps = [
      makeStep({ node_id: "t1", node_name: "Transform A", node_type: "polars" }),
      makeStep({ node_id: "r1", node_name: "Rating", node_type: "rating" }),
      makeStep({ node_id: "t2", node_name: "Transform B", node_type: "polars" }),
    ]
    const groups = groupTraceSteps(steps, "premium")

    // Should be 3 groups: polars, rating, polars
    expect(groups).toHaveLength(3)
    expect(groups[0].steps[0].node_type).toBe("polars")
    expect(groups[1].steps[0].node_type).toBe("rating")
    expect(groups[2].steps[0].node_type).toBe("polars")
  })

  it("single-step groups: no wrapper needed", () => {
    const steps = [
      makeStep({ node_id: "s1", node_name: "Source", node_type: "source" }),
      makeStep({ node_id: "t1", node_name: "Transform", node_type: "polars" }),
      makeStep({ node_id: "r1", node_name: "Rating", node_type: "rating" }),
    ]
    const groups = groupTraceSteps(steps, "premium")

    // Each type is different, so each is its own group
    expect(groups).toHaveLength(3)
    for (const group of groups) {
      expect(group.steps).toHaveLength(1)
    }
  })

  it("mixed types: sources, transforms, scoring in separate groups", () => {
    const steps = [
      makeStep({ node_id: "s1", node_name: "Source", node_type: "source" }),
      makeStep({ node_id: "s2", node_name: "Source 2", node_type: "source" }),
      makeStep({ node_id: "t1", node_name: "Transform", node_type: "polars" }),
      makeStep({ node_id: "m1", node_name: "Model", node_type: "model_score" }),
    ]
    const groups = groupTraceSteps(steps, "premium")

    expect(groups).toHaveLength(3)
    expect(groups[0].steps.every((s) => s.node_type === "source")).toBe(true)
    expect(groups[1].steps.every((s) => s.node_type === "polars")).toBe(true)
    expect(groups[2].steps.every((s) => s.node_type === "model_score")).toBe(true)
  })

  it("marks group as 'primary' when it contains the column-creating step", () => {
    const steps = [
      makeStep({ node_id: "s1", node_name: "Source", node_type: "source" }),
      makeStep({
        node_id: "t1",
        node_name: "Creator",
        node_type: "polars",
        schema_diff: {
          columns_added: ["premium"],
          columns_removed: [],
          columns_modified: [],
          columns_passed: ["age"],
        },
      }),
      makeStep({ node_id: "r1", node_name: "Rating", node_type: "rating" }),
    ]
    const groups = groupTraceSteps(steps, "premium")

    const primaryGroup = groups.find((g) => g.primary)
    expect(primaryGroup).toBeDefined()
    expect(primaryGroup!.steps.some((s) => s.node_id === "t1")).toBe(true)

    // Non-primary groups should not be marked
    const nonPrimary = groups.filter((g) => !g.primary)
    expect(nonPrimary.length).toBe(2)
  })

  it("empty steps array: returns empty groups", () => {
    const groups = groupTraceSteps([], "premium")
    expect(groups).toEqual([])
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
        node_type: "source",
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
        node_type: "source",
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
        node_type: "source",
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
        node_type: "source",
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
        node_type: "source",
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
        node_type: "source",
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
    expect(nonCollapsed.some((e) => (e as TraceStep).node_type === "source")).toBe(true)
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
        node_type: "source",
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
})

// ---------------------------------------------------------------------------
// buildFlowChain
// ---------------------------------------------------------------------------
describe("buildFlowChain", () => {
  it("builds chain from source to target for traced column", () => {
    const trace = makeTrace()
    const chain = buildFlowChain(trace.steps, "premium")

    expect(chain.length).toBeGreaterThanOrEqual(1)
    // Should include the step that creates premium
    expect(chain.some((entry) => entry.step.node_name === "Calc")).toBe(true)
  })

  it("only includes steps where column_relevant=true", () => {
    const steps = [
      makeStep({
        node_id: "s1",
        node_name: "Source",
        node_type: "source",
        column_relevant: true,
      }),
      makeStep({
        node_id: "irr",
        node_name: "Irrelevant",
        node_type: "polars",
        column_relevant: false,
      }),
      makeStep({
        node_id: "t1",
        node_name: "Target",
        node_type: "polars",
        column_relevant: true,
        schema_diff: {
          columns_added: ["premium"],
          columns_removed: [],
          columns_modified: [],
          columns_passed: ["age"],
        },
      }),
    ]
    const chain = buildFlowChain(steps, "premium")

    expect(chain.every((entry) => entry.step.column_relevant)).toBe(true)
    expect(chain.some((entry) => entry.step.node_name === "Irrelevant")).toBe(false)
  })

  it("marks origin step (where column first appears in columns_added)", () => {
    const steps = [
      makeStep({
        node_id: "s1",
        node_name: "Source",
        node_type: "source",
        schema_diff: {
          columns_added: [],
          columns_removed: [],
          columns_modified: [],
          columns_passed: ["age"],
        },
      }),
      makeStep({
        node_id: "c1",
        node_name: "Creator",
        node_type: "polars",
        schema_diff: {
          columns_added: ["premium"],
          columns_removed: [],
          columns_modified: [],
          columns_passed: ["age"],
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
    const chain = buildFlowChain(steps, "premium")

    const originEntry = chain.find((e) => e.isOrigin)
    expect(originEntry).toBeDefined()
    expect(originEntry!.step.node_id).toBe("c1")
  })

  it("marks target step (last step / target node)", () => {
    const steps = [
      makeStep({
        node_id: "s1",
        node_name: "Source",
        node_type: "source",
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
    const chain = buildFlowChain(steps, "premium")

    const targetEntry = chain.find((e) => e.isTarget)
    expect(targetEntry).toBeDefined()
    expect(targetEntry!.step.node_id).toBe("t1")
  })

  it("includes expression summary on steps that have calculation", () => {
    const steps = [
      makeStep({
        node_id: "s1",
        node_name: "Source",
        node_type: "source",
        schema_diff: {
          columns_added: ["age"],
          columns_removed: [],
          columns_modified: [],
          columns_passed: [],
        },
      }),
      makeStep({
        node_id: "c1",
        node_name: "Calculator",
        node_type: "polars",
        schema_diff: {
          columns_added: ["premium"],
          columns_removed: [],
          columns_modified: [],
          columns_passed: ["age"],
        },
        expression: {
          expression_text: "col('age') * 2",
          expression_type: "polars",
          referenced_columns: ["age"],
        },
        calculation: {
          substituted_text: "25 * 2",
          result_value: 50,
          input_values: { age: 25 },
        },
      }),
    ]
    const chain = buildFlowChain(steps, "premium")

    const calcEntry = chain.find((e) => e.step.node_id === "c1")
    expect(calcEntry).toBeDefined()
    expect(calcEntry!.expressionSummary).toBeDefined()
    expect(calcEntry!.expressionSummary).toContain("col('age') * 2")
  })

  it("steps without the column are excluded", () => {
    const steps = [
      makeStep({
        node_id: "s1",
        node_name: "Source",
        node_type: "source",
        column_relevant: true,
        schema_diff: {
          columns_added: ["premium"],
          columns_removed: [],
          columns_modified: [],
          columns_passed: [],
        },
      }),
      makeStep({
        node_id: "u1",
        node_name: "Unrelated",
        node_type: "polars",
        column_relevant: true,
        schema_diff: {
          columns_added: ["other"],
          columns_removed: [],
          columns_modified: [],
          columns_passed: [],
        },
      }),
      makeStep({
        node_id: "t1",
        node_name: "Final",
        node_type: "polars",
        column_relevant: true,
        schema_diff: {
          columns_added: [],
          columns_removed: [],
          columns_modified: ["premium"],
          columns_passed: ["other"],
        },
      }),
    ]
    const chain = buildFlowChain(steps, "premium")

    // "Unrelated" doesn't have "premium" in any schema_diff field, so it should be excluded
    expect(chain.some((e) => e.step.node_id === "u1")).toBe(false)
    expect(chain.some((e) => e.step.node_id === "s1")).toBe(true)
    expect(chain.some((e) => e.step.node_id === "t1")).toBe(true)
  })

  it("returns empty chain when column is not found in any step", () => {
    const steps = [
      makeStep({
        node_id: "s1",
        node_name: "Source",
        node_type: "source",
        schema_diff: {
          columns_added: ["age"],
          columns_removed: [],
          columns_modified: [],
          columns_passed: [],
        },
      }),
    ]
    const chain = buildFlowChain(steps, "nonexistent")
    expect(chain).toHaveLength(0)
  })
})
