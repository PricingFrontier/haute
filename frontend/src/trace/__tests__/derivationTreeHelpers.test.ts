import { describe, expect, it } from "vitest"
import type { TraceColumnDerivation, TraceColumnRead, TraceStep } from "../../types/trace"
import {
  buildDerivationTree,
  derivationSourcesFor,
  substitutionRestatesValue,
  type DerivationTreeNode,
} from "../derivationTreeHelpers"

function derivation(
  column: string,
  resultValue: unknown,
  reads: TraceColumnRead[] | null,
  formula?: { expression: string; substituted: string },
): TraceColumnDerivation {
  return {
    column,
    expression_text: formula?.expression ?? null,
    substituted_text: formula?.substituted ?? null,
    result_value: resultValue,
    not_computable_reason: null,
    result_source: null,
    reads,
    error: null,
    error_type: null,
  }
}

function read(column: string, ...sources: Array<[string, string]>): TraceColumnRead {
  return {
    column,
    sources: sources.map(([node_id, sourceColumn]) => ({ node_id, column: sourceColumn, before_code: false })),
  }
}

function step(node_id: string, node_type: string, derivations: TraceColumnDerivation[]): TraceStep {
  return {
    node_id,
    node_name: node_id,
    node_type,
    schema_diff: { columns_added: [], columns_removed: [], columns_modified: [], columns_passed: [] },
    input_values: {},
    output_values: Object.fromEntries(derivations.map((d) => [d.column, d.result_value])),
    topological_rank: 0,
    column_relevant: true,
    contributed_columns: derivations.map((d) => d.column),
    derivations,
  }
}

/** The demo's shape: a loaded premium, a burn cost, scenarios rescaling the premium, a model, an optimiser. */
const demoSteps: TraceStep[] = [
  step("premiums", "dataInput", [derivation("premium", 528.09, [])]),
  step("fill_na", "polars", [
    derivation("BurnCost", 528.09, [read("premium", ["premiums", "premium"])], {
      expression: "premium",
      substituted: "528.09",
    }),
  ]),
  step("scenarios", "scenarioExpander", [
    derivation(
      "premium",
      792.135,
      [read("premium", ["premiums", "premium"]), read("price_adjustment", ["scenarios", "price_adjustment"])],
      { expression: "premium * price_adjustment", substituted: "528.09 * 1.5" },
    ),
    derivation("price_adjustment", 1.5, []),
    derivation(
      "profit",
      264.045,
      [read("BurnCost", ["fill_na", "BurnCost"]), read("premium", ["scenarios", "premium"])],
      { expression: "premium - BurnCost", substituted: "792.135 - 528.09" },
    ),
  ]),
  step("conversion_scoring", "modelScore", [
    derivation("conversion_prediction", 0.0044, [read("diff_to_market", ["scenarios", "diff_to_market"])]),
  ]),
  step("apply_optimiser", "optimiserApply", [
    derivation("optimal_premium", 1.5, [
      read("conversion_prediction", ["conversion_scoring", "conversion_prediction"]),
      read("profit", ["scenarios", "profit"]),
    ]),
  ]),
]

function outline(node: DerivationTreeNode, depth = 0): string[] {
  const where = node.nodeName ? ` @${node.nodeName}(${node.stepNumber ?? "-"})` : ""
  const line = `${"  ".repeat(depth)}${node.column} [${node.kind}]${where} = ${String(node.value)}`
  return [line, ...node.children.flatMap((child) => outline(child, depth + 1))]
}

describe("buildDerivationTree", () => {
  it("follows the optimiser's objective down to the loaded values", () => {
    const [source] = derivationSourcesFor(demoSteps[4], "optimal_premium", "profit")
    const tree = buildDerivationTree(demoSteps, source)

    expect(outline(tree)).toEqual([
      "profit [formula] @scenarios(3) = 264.045",
      "  BurnCost [formula] @fill_na(2) = 528.09",
      "    premium [loaded] @premiums(1) = 528.09",
      "  premium [formula] @scenarios(3) = 792.135",
      "    premium [loaded] @premiums(1) = 528.09",
      "    price_adjustment [rule] @scenarios(3) = 1.5",
    ])
    expect(tree.expressionText).toBe("premium - BurnCost")
    expect(tree.substitutedText).toBe("792.135 - 528.09")
  })

  it("shows a model's prediction as computed by its rule, with the features it read", () => {
    const [source] = derivationSourcesFor(demoSteps[4], "optimal_premium", "conversion_prediction")
    const tree = buildDerivationTree(demoSteps, source)

    expect(tree.kind).toBe("rule")
    expect(tree.stepNumber).toBe(4)
    // diff_to_market is not among the scenarios step's derivations here.
    expect(tree.children.map((child) => [child.column, child.kind])).toEqual([["diff_to_market", "unresolved"]])
  })

  it("keeps what the trace could not follow visible, each with a note", () => {
    const steps = [
      step("calc", "polars", [
        derivation("y", 2, [read("x", ["gone", "x"]), read("orphan"), read("either", ["a", "either"], ["b", "either"])], {
          expression: "x + orphan + either",
          substituted: "1 + ? + ?",
        }),
      ]),
      step("a", "polars", [derivation("either", 1, null, { expression: "f(z)", substituted: "f(3)" })]),
    ]
    const tree = buildDerivationTree(steps, { node_id: "calc", column: "y", before_code: false })

    const [untraced, orphan, fromA, fromB] = tree.children
    expect([untraced.kind, untraced.note]).toEqual(["untraced", "This node is not in the trace."])
    expect([orphan.kind, orphan.nodeId]).toEqual(["unresolved", null])
    expect(orphan.note).toMatch(/no node/)
    expect(fromA.kind).toBe("formula")
    expect(fromA.note).toBe(
      "One of 2 nodes this value may have come from. Which inputs it read could not be followed.",
    )
    expect(fromB.kind).toBe("untraced")
    expect(fromB.note).toBe("One of 2 nodes this value may have come from.")
  })

  it("shows a value generated before the node's code rewrote it as a leaf", () => {
    const tree = buildDerivationTree(demoSteps, { node_id: "scenarios", column: "premium", before_code: true })

    expect(tree.kind).toBe("before_code")
    expect(tree.children).toEqual([])
    expect(tree.note).toMatch(/before its code/)
  })

  it("cuts a derivation off at the depth limit", () => {
    const [source] = derivationSourcesFor(demoSteps[4], "optimal_premium", "profit")
    const tree = buildDerivationTree(demoSteps, source, 1)

    expect(tree.children.map((child) => [child.column, child.children.length, child.note])).toEqual([
      ["BurnCost", 0, "Its inputs are too deep to show here."],
      ["premium", 0, "Its inputs are too deep to show here."],
    ])
  })
})

describe("substitutionRestatesValue", () => {
  it.each([
    ["180.0", 180, true],
    ["528.09", 528.0900268554688, true],
    ["-2.5e-3", -0.0025, true],
    ["792.135 - 528.09", 264.045, false],
    ["528.09", 529, false],
    ['"south"', "south", false],
    [null, 180, false],
  ])("%s restating %s is %s", (substituted, value, expected) => {
    expect(substitutionRestatesValue(substituted, value)).toBe(expected)
  })
})
