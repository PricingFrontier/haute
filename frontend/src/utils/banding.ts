import type { BandingFactor } from "../types/banding"
import type { SimpleEdge, SimpleNode } from "../panels/editors/_shared"
import type { FactorLevelOrder } from "../panels/optimiser/ratebookFactorTables"
import { NODE_TYPES } from "./nodeTypes"

type BandingRule = BandingFactor["rules"][number]
type CollectLevelsOptions = { includeDefault?: boolean }

function getRuleLevel(rule: BandingRule): string | null {
  if ("assignment" in rule) return rule.assignment || null
  return rule.label || null
}

/** Collect rule output values from a list of banding factors into level sets. */
function collectLevels(
  factors: BandingFactor[],
  target: Record<string, Set<string>>,
  options: CollectLevelsOptions = {},
): void {
  for (const f of factors) {
    if (!f.outputColumn) continue
    if (!target[f.outputColumn]) target[f.outputColumn] = new Set()
    const levels = target[f.outputColumn]
    for (const r of f.rules || []) {
      const level = getRuleLevel(r)
      if (level) levels.add(level)
    }
    if (options.includeDefault && f.default != null) {
      const defaultValue = String(f.default)
      if (defaultValue) levels.add(defaultValue)
    }
  }
}

/** Convert level sets to arrays, dropping empty entries. */
function levelSetsToRecord(sets: Record<string, Set<string>>): Record<string, string[]> {
  const levels: Record<string, string[]> = {}
  for (const [col, s] of Object.entries(sets)) {
    if (s.size > 0) levels[col] = [...s]
  }
  return levels
}

/** Parse factors from a banding node's config, or return null if invalid. */
function parseBandingFactors(node: SimpleNode): BandingFactor[] | null {
  if (node.data.nodeType !== NODE_TYPES.BANDING) return null
  const cfg = (node.data.config || {}) as Record<string, unknown>
  const factors = cfg.factors as BandingFactor[] | undefined
  return Array.isArray(factors) ? factors : null
}

/** Extract factor column -> level names from a single banding node. */
export function extractBandingLevelsForNode(
  allNodes: SimpleNode[],
  nodeId: string,
  options: CollectLevelsOptions = {},
): Record<string, string[]> {
  const node = allNodes.find(n => n.id === nodeId)
  if (!node) return {}
  const factors = parseBandingFactors(node)
  if (!factors) return {}
  const sets: Record<string, Set<string>> = {}
  collectLevels(factors, sets, options)
  return levelSetsToRecord(sets)
}

/** Extract factor column -> ordered display levels, including defaults. */
export function extractBandingLevelOrderForNode(
  allNodes: SimpleNode[],
  nodeId: string,
): Record<string, string[]> {
  return extractBandingLevelsForNode(allNodes, nodeId, { includeDefault: true })
}

/** Extract factor column -> level names from ALL banding nodes. */
export function extractBandingLevels(
  allNodes: SimpleNode[],
): Record<string, string[]> {
  const sets: Record<string, Set<string>> = {}
  for (const n of allNodes) {
    const factors = parseBandingFactors(n)
    if (!factors) continue
    collectLevels(factors, sets)
  }
  return levelSetsToRecord(sets)
}

function configuredBandingSourceId(node: SimpleNode | undefined): string | null {
  const config = node?.data.config
  if (!config || typeof config !== "object") return null
  const value = (config as Record<string, unknown>).banding_source
  if (typeof value !== "string") return null
  const trimmed = value.trim()
  return trimmed || null
}

function singleDirectBandingInputId(
  nodeId: string,
  allNodes: SimpleNode[],
  edges: SimpleEdge[],
): string | null {
  const sourceIds = new Set(edges.filter(edge => edge.target === nodeId).map(edge => edge.source))
  const bandingInputs = allNodes.filter(node => (
    sourceIds.has(node.id) && node.data.nodeType === NODE_TYPES.BANDING
  ))
  return bandingInputs.length === 1 ? bandingInputs[0].id : null
}

/** Resolve the banding source for an optimiser node and return its level
 *  ordering (rules first, default last). Falls back to a single directly-
 *  connected banding input when no explicit source is configured. */
export function bandingLevelOrderForOptimiser(
  nodeId: string,
  allNodes: SimpleNode[],
  edges: SimpleEdge[],
): FactorLevelOrder {
  const optimiserNode = allNodes.find(node => node.id === nodeId)
  const bandingSourceId = configuredBandingSourceId(optimiserNode)
    ?? singleDirectBandingInputId(nodeId, allNodes, edges)
  return bandingSourceId ? extractBandingLevelOrderForNode(allNodes, bandingSourceId) : {}
}
