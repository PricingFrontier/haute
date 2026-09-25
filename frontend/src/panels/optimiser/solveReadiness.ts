/**
 * Whether an optimiser node can be solved, and why not.
 *
 * One set of rules for every solve entry point — the Solve pane's Optimise,
 * Ctrl+Enter and the result preview's Re-run — so none can submit a solve the
 * others would refuse. Each issue names the pane that fixes it.
 */

import { edgeInputName } from "../../utils/apiInputPorts"
import { configField } from "../../utils/configField"
import { NODE_TYPES } from "../../utils/nodeTypes"
import type { SimpleEdge, SimpleNode } from "../editors"
import type { SolveIssue } from "./OptimiserSolveStatus"

/** The solver's frontier workload cap: steps ** constraints solves (mirrors the backend). */
export const FRONTIER_COMPUTE_LIMIT = 10_000

export const COLUMN_MAPPINGS = [
  { key: "quote_id", label: "Quote ID" },
  { key: "scenario_index", label: "Scenario Index" },
  { key: "scenario_value", label: "Scenario Value" },
] as const

/** One connected incoming edge, identified by its exact executable input name. */
export type InputNodeInfo = { name: string; label: string; nodeType: string; sourceNodeId: string }

/** Every node directly feeding the optimiser, by executable input name. */
export function findInputNodes(
  nodeId: string,
  allNodes: SimpleNode[],
  edges: SimpleEdge[],
  submodels?: Record<string, unknown>,
): InputNodeInfo[] {
  const nodeMap = new Map(allNodes.map(n => [n.id, n]))
  return edges.filter((edge) => edge.target === nodeId)
    .map((edge) => {
      const source = nodeMap.get(edge.source)
      if (!source) return null
      const name = edgeInputName(edge, source, submodels)
      return { name, label: name, nodeType: source.data.nodeType, sourceNodeId: source.id }
    })
    .filter((item): item is InputNodeInfo => item !== null)
}

/** The optimiser's selectors resolved against its connected inputs. */
export type OptimiserInputs = {
  inputNodes: InputNodeInfo[]
  bandingNodes: InputNodeInfo[]
  dataInput: string
  malformedDataInput: boolean
  missingExplicitDataInput: boolean
  hasResolvableDataInput: boolean
  bandingSource: string
  malformedBandingSource: boolean
  selectedBandingNode: InputNodeInfo | undefined
  missingExplicitBandingSource: boolean
  /** The explicit source, else the sole direct Banding input when none is set. */
  effectiveBandingNode: InputNodeInfo | undefined
}

export function resolveOptimiserInputs(
  nodeId: string,
  config: Record<string, unknown>,
  allNodes: SimpleNode[],
  edges: SimpleEdge[],
  submodels?: Record<string, unknown>,
): OptimiserInputs {
  const inputNodes = nodeId ? findInputNodes(nodeId, allNodes, edges, submodels) : []
  const bandingNodes = inputNodes.filter((node) => node.nodeType === NODE_TYPES.BANDING)

  // A present non-string selector is malformed: it stays visibly invalid and is
  // never treated as absent (which would allow single-input inference).
  const rawDataInput = config.data_input
  const malformedDataInput = rawDataInput !== undefined && rawDataInput !== null && typeof rawDataInput !== "string"
  const dataInput = typeof rawDataInput === "string" ? rawDataInput : ""
  const selectedDataInput = inputNodes.find((input) => input.name === dataInput)
  const missingExplicitDataInput = malformedDataInput || (!!dataInput && !selectedDataInput)
  const hasResolvableDataInput = !malformedDataInput
    && (!!selectedDataInput || (!dataInput && inputNodes.length === 1))

  const rawBandingSource = config.banding_source
  const malformedBandingSource = rawBandingSource !== undefined && rawBandingSource !== null && typeof rawBandingSource !== "string"
  const bandingSource = typeof rawBandingSource === "string" ? rawBandingSource : ""
  const selectedBandingNode = bandingNodes.find((node) => node.name === bandingSource)
  const missingExplicitBandingSource = malformedBandingSource || (!!bandingSource && !selectedBandingNode)
  const effectiveBandingNode = selectedBandingNode
    ?? (!malformedBandingSource && !bandingSource && bandingNodes.length === 1 ? bandingNodes[0] : undefined)

  return {
    inputNodes,
    bandingNodes,
    dataInput,
    malformedDataInput,
    missingExplicitDataInput,
    hasResolvableDataInput,
    bandingSource,
    malformedBandingSource,
    selectedBandingNode,
    missingExplicitBandingSource,
    effectiveBandingNode,
  }
}

export type SolveReadiness = {
  issues: SolveIssue[]
  canSolve: boolean
  /** The setup is solvable apart from the frontier ranges Auto range fills. */
  canAutoRange: boolean
}

/**
 * Every reason the node cannot be solved. Mapped columns are checked only once
 * the input's columns are known (`dataInputColumns` non-empty).
 */
export function optimiserSolveReadiness(
  config: Record<string, unknown>,
  inputs: OptimiserInputs,
  dataInputColumns: readonly { name: string }[],
): SolveReadiness {
  const mode = configField(config, "mode", "online")
  const objective = configField(config, "objective", "")
  const factorColumns = configField<string[][]>(config, "factor_columns", [])
  const constraints = configField<Record<string, Record<string, number>>>(config, "constraints", {})
  const frontierEnabled = configField(config, "frontier_enabled", false)
  const frontierSteps = configField(config, "frontier_steps", 15)
  const frontierRanges = configField<Record<string, { min?: number; max?: number }>>(config, "frontier_ranges", {})

  const issues: SolveIssue[] = []
  if (!inputs.hasResolvableDataInput) {
    issues.push({
      pane: "data",
      label: "Data",
      message: inputs.inputNodes.length === 0
        ? "Connect an input that provides the objectives and constraints."
        : inputs.malformedDataInput
          ? "The Objectives & Constraints input must be an input name."
          : inputs.missingExplicitDataInput
            ? "The selected Objectives & Constraints input is not connected."
            : "Select the Objectives & Constraints input.",
    })
  }
  if (!objective) {
    issues.push({ pane: "data", label: "Data", message: "Choose the objective column to maximise." })
  }
  if (mode === "ratebook") {
    if (!inputs.selectedBandingNode) {
      issues.push({
        pane: "factors",
        label: "Factors",
        message: inputs.bandingNodes.length === 0
          ? "Connect a Banding node to define the rating factors."
          : "Select a connected Rating Factor Source.",
      })
    } else if (factorColumns.length === 0) {
      issues.push({ pane: "factors", label: "Factors", message: "Select at least one rating factor." })
    }
  }
  // A mapping names a column the input must have; an unset mapping falls back
  // to its default name, so the message names the column actually used.
  if (inputs.hasResolvableDataInput && dataInputColumns.length > 0) {
    const inputColumnNames = new Set(dataInputColumns.map((column) => column.name))
    for (const mapping of COLUMN_MAPPINGS) {
      const column = configField(config, mapping.key, mapping.key)
      if (!column) {
        issues.push({ pane: "data", label: "Data", message: `Choose the ${mapping.label} column.` })
      } else if (!inputColumnNames.has(column)) {
        issues.push({
          pane: "data",
          label: "Data",
          message: `${mapping.label} uses "${column}", which the input does not have.`,
        })
      }
    }
  }
  // Auto range needs only a solvable setup: it exists to fill the frontier
  // ranges the checks below report as missing.
  const canAutoRange = issues.length === 0
  const constraintNames = Object.keys(constraints)
  if (frontierEnabled && constraintNames.length > 0) {
    for (const name of constraintNames) {
      const range = frontierRanges[name]
      const min = typeof range?.min === "number" && Number.isFinite(range.min) ? range.min : undefined
      const max = typeof range?.max === "number" && Number.isFinite(range.max) ? range.max : undefined
      if (min === undefined || max === undefined) {
        issues.push({
          pane: "constraints",
          label: "Constraints",
          message: `Set both ends of the frontier range for ${name}.`,
        })
      } else if (min >= max) {
        issues.push({
          pane: "constraints",
          label: "Constraints",
          message: `The frontier range for ${name} needs a minimum below its maximum.`,
        })
      }
    }
    const frontierSolves = frontierSteps ** constraintNames.length
    if (frontierSolves > FRONTIER_COMPUTE_LIMIT) {
      issues.push({
        pane: "constraints",
        label: "Constraints",
        message: `The frontier would run ${frontierSolves.toLocaleString()} solves; the limit is ${FRONTIER_COMPUTE_LIMIT.toLocaleString()}. Reduce the steps per constraint.`,
      })
    }
  }
  return { issues, canSolve: issues.length === 0, canAutoRange }
}
