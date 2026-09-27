import type { TraceColumnDerivation, TraceColumnSource, TraceStep } from "../types/trace"
import { isTraceSourceNodeType } from "./traceOrigins"
import { evaluatedValue, notComputableNote } from "./traceHelpers"

/**
 * How one value in a traced value's lineage was calculated: the step's formula
 * evaluated on the traced row, a source it was loaded from, or a rule (a model,
 * an optimiser, a scenario grid) that computed it, with the values it read as
 * children. Built from each step's `derivations`, which say exactly which
 * node computed every value a column read.
 */
export type DerivationKind =
  /** The step's code computed it with a formula. */
  | "formula"
  /** A source node loaded it. */
  | "loaded"
  /** A node's rule computed it: a model, an optimiser, a scenario grid. */
  | "rule"
  /** A value a node's rule generated before its code rewrote the column. */
  | "before_code"
  /** The node that computed it is not in the trace. */
  | "untraced"
  /** The trace found no node that computed it. */
  | "unresolved"

export interface DerivationTreeNode {
  /** Unique within the tree, for React keys. */
  key: string
  column: string
  kind: DerivationKind
  nodeId: string | null
  nodeName: string | null
  nodeType: string | null
  /** The step's 1-based position in the trace, as its card is numbered. */
  stepNumber: number | null
  expressionText: string | null
  substitutedText: string | null
  value: unknown
  /** Why the value or its inputs are not shown in full, when they are not. */
  note: string | null
  children: DerivationTreeNode[]
}

/** Deeper derivations are cut off with a note rather than drawn. */
export const DERIVATION_TREE_MAX_DEPTH = 12

interface StepLookup {
  step: TraceStep
  number: number
}

function stepLookup(steps: TraceStep[]): Map<string, StepLookup> {
  return new Map(steps.map((step, index) => [step.node_id, { step, number: index + 1 }]))
}

function derivationOf(step: TraceStep, column: string): TraceColumnDerivation | null {
  return step.derivations.find((derivation) => derivation.column === column) ?? null
}

function derivationNote(derivation: TraceColumnDerivation): string | null {
  if (derivation.error) return derivation.error
  const notComputable = notComputableNote(derivation)
  if (notComputable) return notComputable
  if (derivation.reads === null) return "Which inputs it read could not be followed."
  return null
}

/**
 * The derivation of the value *source* names, and of every value it read.
 * A value reached twice on one path (which a pipeline cannot produce) or
 * beyond the depth limit is shown without its inputs, with a note.
 */
export function buildDerivationTree(
  steps: TraceStep[],
  source: TraceColumnSource,
  maxDepth: number = DERIVATION_TREE_MAX_DEPTH,
): DerivationTreeNode {
  const byId = stepLookup(steps)

  function build(
    current: TraceColumnSource,
    path: ReadonlySet<string>,
    key: string,
    depth: number,
    alternativeCount: number,
  ): DerivationTreeNode {
    const found = byId.get(current.node_id)
    const alternatives = alternativeCount > 1 ? `One of ${alternativeCount} nodes this value may have come from.` : null
    const base = {
      key,
      column: current.column,
      nodeId: current.node_id,
      nodeName: found?.step.node_name ?? current.node_id,
      nodeType: found?.step.node_type ?? null,
      stepNumber: found?.number ?? null,
      expressionText: null,
      substitutedText: null,
      children: [],
    }
    if (!found) {
      return { ...base, kind: "untraced", value: undefined, note: alternatives ?? "This node is not in the trace." }
    }
    if (current.before_code) {
      return {
        ...base,
        kind: "before_code",
        value: undefined,
        note: "The value the node generated before its code rewrote the column.",
      }
    }
    const derivation = derivationOf(found.step, current.column)
    if (!derivation) {
      return {
        ...base,
        kind: "unresolved",
        value: found.step.output_values[current.column],
        note: alternatives ?? "The trace does not explain how this node computed it.",
      }
    }
    const kind: DerivationKind = derivation.expression_text
      ? "formula"
      : isTraceSourceNodeType(found.step.node_type)
        ? "loaded"
        : "rule"
    const pathKey = `${current.node_id}:${current.column}`
    const cutOff = path.has(pathKey)
      ? "Already shown above on this path."
      : depth >= maxDepth
        ? "Its inputs are too deep to show here."
        : null
    const node: DerivationTreeNode = {
      ...base,
      kind,
      expressionText: derivation.expression_text,
      substitutedText: derivation.substituted_text,
      value: evaluatedValue(derivation, found.step.output_values[current.column]),
      note: [alternatives, cutOff ?? derivationNote(derivation)].filter(Boolean).join(" ") || null,
    }
    if (cutOff || !derivation.reads) return node
    const nextPath = new Set(path).add(pathKey)
    node.children = derivation.reads.flatMap((read, readIndex) => {
      if (read.sources.length === 0) {
        return [{
          key: `${key}.${readIndex}`,
          column: read.column,
          kind: "unresolved" as const,
          nodeId: null,
          nodeName: null,
          nodeType: null,
          stepNumber: null,
          expressionText: null,
          substitutedText: null,
          value: undefined,
          note: "The trace found no node that computed this value.",
          children: [],
        }]
      }
      return read.sources.map((readSource, sourceIndex) =>
        build(readSource, nextPath, `${key}.${readIndex}.${sourceIndex}`, depth + 1, read.sources.length),
      )
    })
    return node
  }

  return build(source, new Set(), "0", 0, 1)
}

/** The node that computed the value a step read as *column*, with the step's reads. */
export function derivationSourcesFor(
  step: TraceStep | null | undefined,
  computedColumn: string,
  readColumn: string,
): TraceColumnSource[] {
  if (!step) return []
  const derivation = derivationOf(step, computedColumn)
  return derivation?.reads?.find((read) => read.column === readColumn)?.sources ?? []
}

const RULE_VERBS: Record<string, string> = {
  modelScore: "predicted by",
  optimiserApply: "chosen by",
  scenarioExpander: "generated by",
  banding: "banded by",
  ratingStep: "rated by",
}

function stepLabel(node: DerivationTreeNode): string {
  const name = node.nodeName ?? node.nodeId ?? ""
  return node.stepNumber == null ? name : `${name}, step ${node.stepNumber}`
}

/** Where the row's value came from, as its trailing "(…)" label. */
export function derivationSourceLabel(node: DerivationTreeNode): string | null {
  switch (node.kind) {
    case "formula":
      return stepLabel(node)
    case "loaded":
      return `loaded by ${stepLabel(node)}`
    case "rule":
      return `${RULE_VERBS[node.nodeType ?? ""] ?? "computed by"} ${stepLabel(node)}`
    case "before_code":
      return `generated by ${stepLabel(node)}`
    case "untraced":
      return `from ${stepLabel(node)}`
    case "unresolved":
      return node.nodeId ? stepLabel(node) : null
  }
}
