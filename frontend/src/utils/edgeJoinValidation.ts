import {
  EDGE_JOIN_BASE_CONFIG_KEY,
  EDGE_JOIN_BASE_HANDLE,
  EDGE_JOIN_JOIN_CONFIG_KEY,
  EDGE_JOIN_JOIN_HANDLE,
  edgeJoinCanonicalTargetHandle,
} from "./edgeJoinRoles"
import { NODE_TYPE_META, NODE_TYPES } from "./nodeTypes"

export type EdgeJoinColumnInfo = {
  name: string
  dtype: string
}

export type EdgeJoinValidationNode = {
  id: string
  data?: {
    label?: string
    nodeType?: string
    config?: Record<string, unknown>
    _columns?: EdgeJoinColumnInfo[]
    [key: string]: unknown
  }
}

export type EdgeJoinValidationEdge = {
  id: string
  source: string
  target: string
  targetHandle?: string | null
}

export type EdgeJoinConnectedInput = {
  sourceId: string
  label: string
}

export type EdgeJoinAnalysis = {
  diagnostics: string[]
  incomingEdges: EdgeJoinValidationEdge[]
  baseRoleEdges: EdgeJoinValidationEdge[]
  joinRoleEdges: EdgeJoinValidationEdge[]
  baseRoleEdge?: EdgeJoinValidationEdge
  joinRoleEdge?: EdgeJoinValidationEdge
  connectedInputs: EdgeJoinConnectedInput[]
  baseRoleInput: string
  joinRoleInput: string
  baseInput: string
  joinInput: string
  how: string
  suffix: string
  onKeys: string[]
  leftKeys: string[]
  rightKeys: string[]
  coalesce: string
  validate: string
  maintainOrder: string
  baseColumns: EdgeJoinColumnInfo[]
  joinColumns: EdgeJoinColumnInfo[]
  commonColumns: EdgeJoinColumnInfo[]
}

export type EdgeJoinValidationIssue = {
  node: EdgeJoinValidationNode
  analysis: EdgeJoinAnalysis
}

const EDGE_JOIN_DEFAULT_CONFIG = NODE_TYPE_META[NODE_TYPES.EDGE_JOIN].defaultConfig

export const EDGE_JOIN_HOW_VALUES = [
  "left",
  "inner",
  "full",
  "right",
  "semi",
  "anti",
  "cross",
] as const

export function analyzeEdgeJoinNode({
  nodeId,
  config,
  nodes,
  edges,
}: {
  nodeId: string
  config: Record<string, unknown>
  nodes: EdgeJoinValidationNode[]
  edges: EdgeJoinValidationEdge[]
}): EdgeJoinAnalysis {
  const diagnostics: string[] = []
  const nodeMap = new Map(nodes.map((node) => [node.id, node]))
  const incomingEdges = edges.filter((edge) => edge.target === nodeId)
  const baseRoleEdges = incomingEdges.filter(
    (edge) => edgeJoinCanonicalTargetHandle(edge.targetHandle) === EDGE_JOIN_BASE_HANDLE,
  )
  const joinRoleEdges = incomingEdges.filter(
    (edge) => edgeJoinCanonicalTargetHandle(edge.targetHandle) === EDGE_JOIN_JOIN_HANDLE,
  )
  const baseRoleEdge = baseRoleEdges[0]
  const joinRoleEdge = joinRoleEdges[0]
  const baseRoleInput = baseRoleEdge?.source ?? ""
  const joinRoleInput = joinRoleEdge?.source ?? ""

  const connectedInputs = uniqueConnectedInputs(incomingEdges, nodeMap)
  const baseInput = readOptionalString(config[EDGE_JOIN_BASE_CONFIG_KEY], EDGE_JOIN_BASE_CONFIG_KEY, diagnostics)
  const joinInput = readOptionalString(config[EDGE_JOIN_JOIN_CONFIG_KEY], EDGE_JOIN_JOIN_CONFIG_KEY, diagnostics)
  const how = readJoinHow(config.how, diagnostics)
  const suffix = readOptionalString(config.suffix, "suffix", diagnostics) || String(EDGE_JOIN_DEFAULT_CONFIG.suffix)
  const onKeys = readKeyList(config.on, "on", diagnostics)
  const leftKeys = readKeyList(config.leftOn, "leftOn", diagnostics)
  const rightKeys = readKeyList(config.rightOn, "rightOn", diagnostics)
  const coalesce = readCoalesce(config.coalesce, diagnostics)
  const validate = readAdvancedString(config.validate, "validate", diagnostics)
  const maintainOrder = readAdvancedString(config.maintainOrder, "maintainOrder", diagnostics)

  addRoleDiagnostics({
    diagnostics,
    baseInput,
    joinInput,
    incomingEdges,
    baseRoleEdges,
    joinRoleEdges,
    baseRoleEdge,
    joinRoleEdge,
    connectedInputs,
    nodeMap,
  })

  const hasSameConfig = onKeys.length > 0
  const hasPairedConfig = leftKeys.length > 0 || rightKeys.length > 0
  const hasSameValues = onKeys.some(Boolean)
  const hasPairedValues = leftKeys.some(Boolean) || rightKeys.some(Boolean)
  const baseColumns = getColumns(nodeMap.get(baseRoleInput || baseInput))
  const joinColumns = getColumns(nodeMap.get(joinRoleInput || joinInput))
  const commonColumns = commonColumnOptions(baseColumns, joinColumns)

  if (hasSameConfig && hasPairedConfig) {
    diagnostics.push("Choose either same-name keys or paired base/join keys, not both.")
  }
  if (how === "cross" && (hasSameConfig || hasPairedConfig)) {
    diagnostics.push("Cross joins must not configure join keys.")
  }
  if (how !== "cross" && !hasSameValues && !hasPairedValues) {
    diagnostics.push("Non-cross joins need join keys.")
  }
  if (hasPairedConfig && leftKeys.length !== rightKeys.length) {
    diagnostics.push("leftOn and rightOn must contain the same number of keys.")
  }
  if ([...onKeys, ...leftKeys, ...rightKeys].some((key) => key === "")) {
    diagnostics.push("Join key rows cannot be blank.")
  }
  addColumnDiagnostics(diagnostics, "Base", leftKeys, baseColumns)
  addColumnDiagnostics(diagnostics, "Join", rightKeys, joinColumns)
  addColumnDiagnostics(diagnostics, "Same-name", onKeys, commonColumns)

  return {
    diagnostics: dedupe(diagnostics),
    incomingEdges,
    baseRoleEdges,
    joinRoleEdges,
    baseRoleEdge,
    joinRoleEdge,
    connectedInputs,
    baseRoleInput,
    joinRoleInput,
    baseInput,
    joinInput,
    how,
    suffix,
    onKeys,
    leftKeys,
    rightKeys,
    coalesce,
    validate,
    maintainOrder,
    baseColumns,
    joinColumns,
    commonColumns,
  }
}

export function findFirstInvalidEdgeJoin(
  nodes: EdgeJoinValidationNode[],
  edges: EdgeJoinValidationEdge[],
): EdgeJoinValidationIssue | null {
  for (const node of nodes) {
    if (node.data?.nodeType !== NODE_TYPES.EDGE_JOIN) continue
    const analysis = analyzeEdgeJoinNode({
      nodeId: node.id,
      config: node.data.config ?? {},
      nodes,
      edges,
    })
    if (analysis.diagnostics.length > 0) return { node, analysis }
  }
  return null
}

export function formatEdgeJoinValidationIssue(issue: EdgeJoinValidationIssue): string {
  const label = issue.node.data?.label || issue.node.id
  return `${label}: ${issue.analysis.diagnostics[0]}`
}

function readOptionalString(value: unknown, field: string, diagnostics: string[]): string {
  if (value == null || value === "") return ""
  if (typeof value === "string") return value
  diagnostics.push(`${field} must be a string.`)
  return ""
}

function readJoinHow(value: unknown, diagnostics: string[]): string {
  const defaultHow = String(EDGE_JOIN_DEFAULT_CONFIG.how)
  if (value == null || value === "") return defaultHow
  if (typeof value !== "string") {
    diagnostics.push("how must be a string.")
    return defaultHow
  }
  if (!EDGE_JOIN_HOW_VALUES.some((option) => option === value)) {
    diagnostics.push("how must be one of left, inner, full, right, semi, anti, or cross.")
  }
  return value
}

function readAdvancedString(value: unknown, field: string, diagnostics: string[]): string {
  if (value == null || value === "") return ""
  if (typeof value === "string") return value
  diagnostics.push(`${field} must be a string.`)
  return ""
}

function readCoalesce(value: unknown, diagnostics: string[]): string {
  if (value == null || value === "") return ""
  if (value === true) return "true"
  if (value === false) return "false"
  diagnostics.push("coalesce must be true, false, or unset.")
  return ""
}

function readKeyList(value: unknown, field: string, diagnostics: string[]): string[] {
  if (value == null || value === "") return []
  if (typeof value === "string") return [value]
  if (Array.isArray(value)) {
    if (!value.every((item) => typeof item === "string")) {
      diagnostics.push(`${field} must be a string or list of strings.`)
    }
    return value.filter((item): item is string => typeof item === "string")
  }
  diagnostics.push(`${field} must be a string or list of strings.`)
  return []
}

function getColumns(node: EdgeJoinValidationNode | undefined): EdgeJoinColumnInfo[] {
  return node?.data?._columns ?? []
}

function commonColumnOptions(
  baseColumns: EdgeJoinColumnInfo[],
  joinColumns: EdgeJoinColumnInfo[],
): EdgeJoinColumnInfo[] {
  if (baseColumns.length === 0 || joinColumns.length === 0) return []
  const joinNames = new Set(joinColumns.map((column) => column.name))
  return baseColumns.filter((column) => joinNames.has(column.name))
}

function uniqueConnectedInputs(
  edges: EdgeJoinValidationEdge[],
  nodeMap: Map<string, EdgeJoinValidationNode>,
): EdgeJoinConnectedInput[] {
  const seen = new Set<string>()
  const inputs: EdgeJoinConnectedInput[] = []
  for (const edge of edges) {
    if (seen.has(edge.source)) continue
    seen.add(edge.source)
    inputs.push({
      sourceId: edge.source,
      label: nodeLabel(edge.source, nodeMap),
    })
  }
  return inputs
}

function addRoleDiagnostics({
  diagnostics,
  baseInput,
  joinInput,
  incomingEdges,
  baseRoleEdges,
  joinRoleEdges,
  baseRoleEdge,
  joinRoleEdge,
  connectedInputs,
  nodeMap,
}: {
  diagnostics: string[]
  baseInput: string
  joinInput: string
  incomingEdges: EdgeJoinValidationEdge[]
  baseRoleEdges: EdgeJoinValidationEdge[]
  joinRoleEdges: EdgeJoinValidationEdge[]
  baseRoleEdge?: EdgeJoinValidationEdge
  joinRoleEdge?: EdgeJoinValidationEdge
  connectedInputs: EdgeJoinConnectedInput[]
  nodeMap: Map<string, EdgeJoinValidationNode>
}) {
  if (incomingEdges.length !== 2) {
    diagnostics.push(`Edge joins need exactly two connected inputs; found ${incomingEdges.length}.`)
  }
  if (baseRoleEdges.length !== 1) {
    diagnostics.push("Connect exactly one input to the base handle.")
  }
  if (joinRoleEdges.length !== 1) {
    diagnostics.push("Connect exactly one input to the join handle.")
  }
  if (!baseInput) diagnostics.push("baseInput must be configured.")
  if (!joinInput) diagnostics.push("joinInput must be configured.")
  if (baseInput && joinInput && baseInput === joinInput) {
    diagnostics.push("baseInput and joinInput must be distinct.")
  }

  const connectedIds = new Set(connectedInputs.map((input) => input.sourceId))
  if (baseInput && !connectedIds.has(baseInput)) {
    diagnostics.push(`Base Input ${baseInput} is not connected to this edge join.`)
  }
  if (joinInput && !connectedIds.has(joinInput)) {
    diagnostics.push(`Join Input ${joinInput} is not connected to this edge join.`)
  }
  if (baseInput && baseRoleEdge && baseInput !== baseRoleEdge.source) {
    diagnostics.push(
      `Base Input is set to ${baseInput}, but the connected base handle is ${nodeLabel(baseRoleEdge.source, nodeMap)}.`,
    )
  }
  if (joinInput && joinRoleEdge && joinInput !== joinRoleEdge.source) {
    diagnostics.push(
      `Join Input is set to ${joinInput}, but the connected join handle is ${nodeLabel(joinRoleEdge.source, nodeMap)}.`,
    )
  }
}

function nodeLabel(nodeId: string, nodeMap: Map<string, EdgeJoinValidationNode>): string {
  return nodeMap.get(nodeId)?.data?.label ?? nodeId
}

function addColumnDiagnostics(
  diagnostics: string[],
  label: string,
  keys: string[],
  columns: EdgeJoinColumnInfo[],
) {
  if (columns.length === 0) return
  const columnNames = new Set(columns.map((column) => column.name))
  for (const key of keys) {
    if (key && !columnNames.has(key)) {
      diagnostics.push(`${label} key ${key} is not in the current upstream columns.`)
    }
  }
}

function dedupe(values: string[]): string[] {
  return Array.from(new Set(values))
}
