import type { SimpleEdge, SimpleNode } from "../panels/editors/_shared"
import type { FactorLevelOrder } from "../panels/optimiser/ratebookFactorTables"
import { edgeInputName } from "./apiInputPorts"
import { NODE_TYPES } from "./nodeTypes"

type CollectLevelsOptions = { includeDefault?: boolean }

export type BandingZeroLevelIssue = {
  outputColumn: string
}

export type BandingClassification = {
  levels: Record<string, string[]>
  configuredOutputs: string[]
  zeroLevelOutputs: string[]
  zeroLevelIssues: BandingZeroLevelIssue[]
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  if (value === null || typeof value !== "object") return false
  const prototype = Object.getPrototypeOf(value)
  return prototype === Object.prototype || prototype === null
}

function nonblankString(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null
}

function emptyClassification(): BandingClassification {
  return { levels: {}, configuredOutputs: [], zeroLevelOutputs: [], zeroLevelIssues: [] }
}

/** Safely classify a persisted Banding factor container without treating drafts as factors. */
export function classifyBandingFactors(
  factors: unknown,
  options: CollectLevelsOptions = {},
): BandingClassification {
  if (!Array.isArray(factors)) return emptyClassification()

  const configuredOutputs: string[] = []
  const levelSets: Record<string, Set<string>> = {}
  for (const factor of factors) {
    if (!isPlainObject(factor)) continue
    const banding = factor.banding
    if (banding !== "continuous" && banding !== "categorical" && banding !== "breakpoints") continue
    const outputColumn = nonblankString(factor.outputColumn)
    if (!outputColumn) continue

    if (!levelSets[outputColumn]) {
      configuredOutputs.push(outputColumn)
      levelSets[outputColumn] = new Set()
    }
    const levels = levelSets[outputColumn]
    if (Array.isArray(factor.rules)) {
      for (const rule of factor.rules) {
        if (!isPlainObject(rule)) continue
        const level = nonblankString(banding === "breakpoints" ? rule.label : rule.assignment)
        if (level) levels.add(level)
      }
    }
    if (options.includeDefault && factor.default != null) {
      const defaultValue = nonblankString(factor.default)
      if (defaultValue) levels.add(defaultValue)
    }
  }

  const levels: Record<string, string[]> = {}
  const zeroLevelOutputs: string[] = []
  for (const outputColumn of configuredOutputs) {
    const outputLevels = [...levelSets[outputColumn]]
    if (outputLevels.length) levels[outputColumn] = outputLevels
    else zeroLevelOutputs.push(outputColumn)
  }
  return {
    levels,
    configuredOutputs,
    zeroLevelOutputs,
    zeroLevelIssues: zeroLevelOutputs.map(outputColumn => ({ outputColumn })),
  }
}

/** Classify one Banding node. Non-Banding nodes and malformed configs are drafts. */
export function classifyBandingNode(
  node: SimpleNode | undefined,
  options: CollectLevelsOptions = {},
): BandingClassification {
  if (!node || node.data.nodeType !== NODE_TYPES.BANDING) return emptyClassification()
  const config = node.data.config
  if (!isPlainObject(config)) return emptyClassification()
  return classifyBandingFactors(config.factors, options)
}

/** Classify all Banding nodes, aggregating configured outputs in first-seen order. */
export function classifyBandingLevels(
  allNodes: SimpleNode[],
  options: CollectLevelsOptions = {},
): BandingClassification {
  const configuredOutputs: string[] = []
  const levelSets: Record<string, Set<string>> = {}
  for (const node of allNodes) {
    const classification = classifyBandingNode(node, options)
    for (const outputColumn of classification.configuredOutputs) {
      if (!levelSets[outputColumn]) {
        configuredOutputs.push(outputColumn)
        levelSets[outputColumn] = new Set()
      }
      for (const level of classification.levels[outputColumn] ?? []) levelSets[outputColumn].add(level)
    }
  }
  const levels: Record<string, string[]> = {}
  const zeroLevelOutputs: string[] = []
  for (const outputColumn of configuredOutputs) {
    const outputLevels = [...levelSets[outputColumn]]
    if (outputLevels.length) levels[outputColumn] = outputLevels
    else zeroLevelOutputs.push(outputColumn)
  }
  return { levels, configuredOutputs, zeroLevelOutputs, zeroLevelIssues: zeroLevelOutputs.map(outputColumn => ({ outputColumn })) }
}

/** Extract factor column -> level names from a single banding node. */
export function extractBandingLevelsForNode(allNodes: SimpleNode[], nodeId: string, options: CollectLevelsOptions = {}): Record<string, string[]> {
  return classifyBandingNode(allNodes.find(node => node.id === nodeId), options).levels
}

/** Extract factor column -> ordered display levels, including defaults. */
export function extractBandingLevelOrderForNode(allNodes: SimpleNode[], nodeId: string): Record<string, string[]> {
  return extractBandingLevelsForNode(allNodes, nodeId, { includeDefault: true })
}

/** Extract factor column -> level names from ALL banding nodes. */
export function extractBandingLevels(allNodes: SimpleNode[]): Record<string, string[]> {
  return classifyBandingLevels(allNodes).levels
}

function configuredBandingSourceName(node: SimpleNode | undefined): string | null {
  const config = node?.data.config
  if (!isPlainObject(config)) return null
  const value = config.banding_source
  return typeof value === "string" && value.length > 0 ? value : null
}

function configuredDirectBandingInput(
  nodeId: string,
  configuredName: string,
  allNodes: SimpleNode[],
  edges: SimpleEdge[],
): SimpleNode | undefined {
  const nodeMap = new Map(allNodes.map(node => [node.id, node]))
  const matches = edges
    .filter(edge => edge.target === nodeId)
    .flatMap((edge) => {
      const source = nodeMap.get(edge.source)
      if (!source || source.data.nodeType !== NODE_TYPES.BANDING) return []
      return edgeInputName(edge, source) === configuredName ? [source] : []
    })
  return matches.length === 1 ? matches[0] : undefined
}

/** Resolve one exact configured incoming-edge name, with defaults last. */
export function bandingLevelOrderForOptimiser(nodeId: string, allNodes: SimpleNode[], edges: SimpleEdge[]): FactorLevelOrder {
  const optimiserNode = allNodes.find(node => node.id === nodeId)
  const configuredName = configuredBandingSourceName(optimiserNode)
  if (!configuredName) return {}
  const bandingInput = configuredDirectBandingInput(nodeId, configuredName, allNodes, edges)
  return bandingInput ? classifyBandingNode(bandingInput, { includeDefault: true }).levels : {}
}
