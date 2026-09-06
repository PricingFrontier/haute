import type { Edge, Node } from "@xyflow/react"

import type { SimpleEdge, SimpleNode } from "../panels/editors/_shared"
import { isSubmodelInstanceConfig } from "../types/node"
import {
  applyApiInputConfigChange,
  edgeInputName,
  incomingEdgeInputNames,
} from "./apiInputPorts"
import { attachEditorEdgeIdentities } from "./editorIdentities"
import { NODE_TYPES } from "./nodeTypes"

type RenamePair = { from: string; to: string }

type RenameGraphScope = {
  nodes: Node[]
  edges: Edge[]
  submodels: Record<string, unknown>
}

type AffectedRenameTarget = {
  scope: RenameGraphScope
  target: Node
  incomingScope: RenameGraphScope
  incomingTargetId: string
  pairs: RenamePair[]
}

type AffectedTargets = Map<RenameGraphScope, Map<string, AffectedRenameTarget>>
type MappingChanges = Map<RenameGraphScope, Map<string, Record<string, unknown>>>

export type PreparedNodeUpdate = {
  ok: true
  nodeId: string
  data: Record<string, unknown>
  nodes: Node[]
  edges: Edge[]
  submodels: Record<string, unknown>
  removed: Array<{ edge: Edge; sourceHandle: string | null }>
}

export type NodeUpdatePlanFailure = { ok: false; error: string }
export type PrepareNodeUpdateResult = PreparedNodeUpdate | NodeUpdatePlanFailure

export type PrepareNodeUpdateInput = {
  nodeId: string
  data: Record<string, unknown>
  refreshSourceIdentity: boolean
  readOnly: boolean
  graph: { nodes: Node[]; edges: Edge[] }
  submodels: Record<string, unknown>
  reservedApiInputFrameLabels: ReadonlySet<string>
}

type EdgeReconciliation = {
  ok: true
  edges: Edge[]
  rebound: Array<{ edge: Edge; from: string; to: string }>
  removed: Array<{ edge: Edge; sourceHandle: string | null }>
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}

function remapRecordKeys(
  value: unknown,
  renames: readonly RenamePair[],
): { value: unknown; collision?: string } {
  if (!isRecord(value) || renames.length === 0) return { value }
  const renameByFrom = new Map(renames.map(({ from, to }) => [from, to]))
  const next: Record<string, unknown> = {}
  for (const [key, entry] of Object.entries(value)) {
    const nextKey = renameByFrom.get(key) ?? key
    if (Object.hasOwn(next, nextKey)) return { value, collision: nextKey }
    next[nextKey] = entry
  }
  return { value: next }
}

function remapRecordValues(value: unknown, renames: readonly RenamePair[]): unknown {
  if (!isRecord(value) || renames.length === 0) return value
  const renameByFrom = new Map(renames.map(({ from, to }) => [from, to]))
  let changed = false
  const next = Object.fromEntries(
    Object.entries(value).map(([key, entry]) => {
      if (typeof entry !== "string") return [key, entry]
      const replacement = renameByFrom.get(entry)
      if (replacement === undefined) return [key, entry]
      changed = true
      return [key, replacement]
    }),
  )
  return changed ? next : value
}

function reconcileSourceEdges({
  nodeId,
  data,
  previousNode,
  graph,
  submodels,
  refreshSourceIdentity,
  reservedApiInputFrameLabels,
}: Omit<PrepareNodeUpdateInput, "readOnly"> & { previousNode: Node }): EdgeReconciliation | NodeUpdatePlanFailure {
  if (data.nodeType === NODE_TYPES.API_INPUT) {
    const config = (data.config ?? {}) as Record<string, unknown>
    const previousConfig = ((previousNode.data as Record<string, unknown>).config ?? {}) as Record<string, unknown>
    const result = applyApiInputConfigChange({
      nodeId,
      prevConfig: previousConfig,
      nextConfig: config,
      edges: graph.edges,
      reservedLabels: reservedApiInputFrameLabels,
    })
    if (!refreshSourceIdentity) {
      return { ok: true, edges: result.edges, rebound: result.rebound, removed: result.removed }
    }
    const nextNode = { ...previousNode, data }
    const refreshedById = new Map(
      attachEditorEdgeIdentities(
        result.edges.filter((edge) => edge.source === nodeId),
        [nextNode],
      ).map((edge) => [edge.id, edge]),
    )
    return {
      ok: true,
      edges: result.edges.map((edge) => refreshedById.get(edge.id) ?? edge),
      rebound: result.rebound,
      removed: result.removed,
    }
  }

  if (previousNode.data.label === data.label) {
    return { ok: true, edges: graph.edges, rebound: [], removed: [] }
  }
  if (!refreshSourceIdentity) {
    return {
      ok: false,
      error: "Node labels must be resolved by the editor identity service before commit.",
    }
  }

  const outgoing = graph.edges.filter((edge) => edge.source === nodeId)
  const refreshed = attachEditorEdgeIdentities(outgoing, [{ ...previousNode, data }])
  const refreshedById = new Map(refreshed.map((edge) => [edge.id, edge]))
  const rebound = outgoing
    .map((edge, index) => {
      const nextInputName = refreshed[index]?.data?._inputName
      if (typeof nextInputName !== "string" || nextInputName.length === 0) {
        throw new Error(`Identity resolver omitted the input name for edge ${edge.id}`)
      }
      return {
        edge,
        from: edgeInputName(
          edge as unknown as SimpleEdge,
          previousNode as unknown as SimpleNode,
          submodels,
        ),
        to: nextInputName,
      }
    })
    .filter((change) => change.from !== change.to)
  return {
    ok: true,
    edges: graph.edges.map((edge) => refreshedById.get(edge.id) ?? edge),
    rebound,
    removed: [],
  }
}

function collectAffectedTargets(
  rootScope: RenameGraphScope,
  rebound: readonly { edge: Edge; from: string; to: string }[],
): AffectedTargets {
  const nodeById = new Map(rootScope.nodes.map((node) => [node.id, node]))
  const affectedByScope: AffectedTargets = new Map()
  for (const change of rebound) {
    const target = nodeById.get(change.edge.target)
    if (!target) throw new Error(`Cannot derive rename target ${change.edge.target}`)
    if (target.data.nodeType === NODE_TYPES.SUBMODEL) {
      if (!isSubmodelInstanceConfig(target.data.config)) {
        throw new Error(`Submodel instance ${target.id} has malformed identity config`)
      }
      // Public port ids are immutable definition-owned input names, so an
      // external frame rename changes only the parent edge binding.
      continue
    }
    const targets = affectedByScope.get(rootScope) ?? new Map<string, AffectedRenameTarget>()
    const affected = targets.get(target.id) ?? {
      scope: rootScope,
      target,
      incomingScope: rootScope,
      incomingTargetId: target.id,
      pairs: [],
    }
    if (!affected.pairs.some((pair) => pair.from === change.from && pair.to === change.to)) {
      affected.pairs.push({ from: change.from, to: change.to })
    }
    targets.set(target.id, affected)
    affectedByScope.set(rootScope, targets)
  }
  return affectedByScope
}

function applyConfigMapping(
  changes: MappingChanges,
  scope: RenameGraphScope,
  node: Node,
  field: "input_scenario_map" | "inputMapping" | "data_input" | "banding_source" | "ratebook_input",
  pairs: readonly RenamePair[],
  keys: boolean,
): NodeUpdatePlanFailure | null {
  const scopeChanges = changes.get(scope) ?? new Map<string, Record<string, unknown>>()
  const config = scopeChanges.get(node.id) ?? ((node.data.config ?? {}) as Record<string, unknown>)
  if (keys) {
    const mapped = remapRecordKeys(config[field], pairs)
    if (mapped.collision !== undefined) {
      return {
        ok: false,
        error: `Target "${String(node.data.label ?? node.id)}" already has an input named "${mapped.collision}".`,
      }
    }
    if (mapped.value === config[field]) return null
    scopeChanges.set(node.id, { ...config, [field]: mapped.value })
  } else if (field === "inputMapping") {
    const mappedValue = remapRecordValues(config[field], pairs)
    if (mappedValue === config[field]) return null
    scopeChanges.set(node.id, { ...config, [field]: mappedValue })
  } else {
    const current = config[field]
    if (typeof current !== "string") return null
    const replacement = new Map(pairs.map(({ from, to }) => [from, to])).get(current)
    if (replacement === undefined) return null
    scopeChanges.set(node.id, { ...config, [field]: replacement })
  }
  changes.set(scope, scopeChanges)
  return null
}

function targetInputCollision(affected: AffectedRenameTarget): string | null {
  const names = incomingEdgeInputNames({
    targetNodeId: affected.target.id,
    boundaryNodeId: affected.incomingTargetId,
    nodes: affected.incomingScope.nodes as unknown as SimpleNode[],
    edges: affected.incomingScope.edges as unknown as SimpleEdge[],
    submodels: affected.incomingScope.submodels,
  })
  if (affected.scope !== affected.incomingScope) {
    names.push(...incomingEdgeInputNames({
      targetNodeId: affected.target.id,
      nodes: affected.scope.nodes as unknown as SimpleNode[],
      edges: affected.scope.edges as unknown as SimpleEdge[],
      submodels: affected.scope.submodels,
    }))
  }
  const seen = new Set<string>()
  for (const name of names) {
    if (seen.has(name)) return name
    seen.add(name)
  }
  return null
}

function isCodedOrdinaryTransform(node: Node): boolean {
  if (node.data.nodeType !== NODE_TYPES.POLARS) return false
  const config = (node.data.config ?? {}) as Record<string, unknown>
  if ("instanceOf" in config) return false
  return typeof config.code === "string" && config.code.trim().length > 0
}

/**
 * A coded ordinary transform references its inputs by name in `config.code`.
 * A rename never edits that code: it records the logical→edge binding on the
 * transform's `inputMapping` instead, so the generated parameter names and the
 * code stay exactly as authored while the edge carries the new name.
 */
function preserveCodedTransformBindings(
  changes: MappingChanges,
  affected: AffectedRenameTarget,
): NodeUpdatePlanFailure | null {
  const { scope, target, pairs } = affected
  if (!isCodedOrdinaryTransform(target)) return null
  const edgeCollision = targetInputCollision(affected)
  const label = String(target.data.label ?? target.id)
  if (edgeCollision !== null) {
    return { ok: false, error: `Target "${label}" already has an input named "${edgeCollision}".` }
  }
  const scopeChanges = changes.get(scope) ?? new Map<string, Record<string, unknown>>()
  const config = scopeChanges.get(target.id) ?? ((target.data.config ?? {}) as Record<string, unknown>)
  const mapping: Record<string, string> = {}
  if (isRecord(config.inputMapping)) {
    for (const [logical, current] of Object.entries(config.inputMapping)) {
      if (typeof current === "string") mapping[logical] = current
    }
  }
  for (const { from, to } of pairs) {
    if (Object.values(mapping).includes(to)) continue // a logical name already follows this edge
    if (Object.hasOwn(mapping, from)) {
      return { ok: false, error: `Target "${label}" already has an input named "${from}".` }
    }
    if (Object.hasOwn(mapping, to)) {
      return { ok: false, error: `Target "${label}" already has an input named "${to}".` }
    }
    mapping[from] = to
  }
  for (const [logical, current] of Object.entries(mapping)) {
    if (logical === current) delete mapping[logical]
  }
  // The post-rename edge names resolve to logical names through the mapping;
  // two edges resolving to one logical name would be an unbound or ambiguous
  // input at run time, so refuse before anything mutates.
  const logicalByEdge = new Map(Object.entries(mapping).map(([logical, current]) => [current, logical]))
  const edgeNames = incomingEdgeInputNames({
    targetNodeId: target.id,
    boundaryNodeId: affected.incomingTargetId,
    nodes: affected.incomingScope.nodes as unknown as SimpleNode[],
    edges: affected.incomingScope.edges as unknown as SimpleEdge[],
    submodels: affected.incomingScope.submodels,
  })
  if (affected.scope !== affected.incomingScope) {
    edgeNames.push(...incomingEdgeInputNames({
      targetNodeId: target.id,
      nodes: affected.scope.nodes as unknown as SimpleNode[],
      edges: affected.scope.edges as unknown as SimpleEdge[],
      submodels: affected.scope.submodels,
    }))
  }
  const seen = new Set<string>()
  for (const edgeName of edgeNames) {
    const logical = logicalByEdge.get(edgeName) ?? edgeName
    if (seen.has(logical)) {
      return { ok: false, error: `Target "${label}" already has an input named "${logical}".` }
    }
    seen.add(logical)
  }
  const nextConfig: Record<string, unknown> = { ...config }
  if (Object.keys(mapping).length === 0) delete nextConfig.inputMapping
  else nextConfig.inputMapping = mapping
  const unchanged =
    JSON.stringify(nextConfig.inputMapping ?? null) === JSON.stringify(config.inputMapping ?? null)
  if (unchanged) return null
  scopeChanges.set(target.id, nextConfig)
  changes.set(scope, scopeChanges)
  return null
}

function collectMappingChanges(
  rootScope: RenameGraphScope,
  affectedByScope: AffectedTargets,
): MappingChanges | NodeUpdatePlanFailure {
  const changes: MappingChanges = new Map()
  for (const targets of affectedByScope.values()) {
    for (const affected of targets.values()) {
      if (affected.target.data.nodeType === NODE_TYPES.LIVE_SWITCH) {
        const failure = applyConfigMapping(
          changes,
          affected.scope,
          affected.target,
          "input_scenario_map",
          affected.pairs,
          true,
        )
        if (failure) return failure
      }
      const failure = applyConfigMapping(
        changes,
        affected.scope,
        affected.target,
        "inputMapping",
        affected.pairs,
        false,
      )
      if (failure) return failure
      const bindingFailure = preserveCodedTransformBindings(changes, affected)
      if (bindingFailure) return bindingFailure
      for (const field of ["data_input", "banding_source", "ratebook_input"] as const) {
        const scalarFailure = applyConfigMapping(changes, affected.scope, affected.target, field, affected.pairs, false)
        if (scalarFailure) return scalarFailure
      }
    }
  }

  const instanceScopes = new Set<RenameGraphScope>([rootScope, ...affectedByScope.keys()])
  for (const targets of affectedByScope.values()) {
    for (const affected of targets.values()) {
      for (const scope of instanceScopes) {
        for (const node of scope.nodes) {
          const config = (node.data.config ?? {}) as Record<string, unknown>
          if (config.instanceOf !== affected.target.id) continue
          const failure = applyConfigMapping(
            changes,
            scope,
            node,
            "inputMapping",
            affected.pairs,
            true,
          )
          if (failure) return failure
        }
      }
    }
  }
  return changes
}

function applyMappingChanges(changes: MappingChanges): void {
  for (const [scope, scopeChanges] of changes) {
    const mappedNodes = scope.nodes.map((node) => {
      const config = scopeChanges.get(node.id)
      return config ? { ...node, data: { ...node.data, config } } : node
    })
    scope.nodes.splice(0, scope.nodes.length, ...mappedNodes)
  }
}

function findInputCollision(affectedByScope: AffectedTargets): NodeUpdatePlanFailure | null {
  for (const targets of affectedByScope.values()) {
    for (const affected of targets.values()) {
      const collision = targetInputCollision(affected)
      if (collision !== null) {
        return {
          ok: false,
          error: `Target "${String(affected.target.data.label ?? affected.target.id)}" already has an input named "${collision}".`,
        }
      }
    }
  }
  return null
}

/**
 * Computes a complete selected-node graph update without mutating the supplied
 * graph, registry, or store. The caller owns request identity and commit.
 */
export function prepareNodeUpdate(input: PrepareNodeUpdateInput): PrepareNodeUpdateResult {
  if (input.readOnly) return { ok: false, error: "This pipeline document is read-only." }
  const previousNode = input.graph.nodes.find((node) => node.id === input.nodeId)
  if (!previousNode) {
    return { ok: false, error: `Cannot update missing node "${input.nodeId}".` }
  }

  const edgeResult = reconcileSourceEdges({ ...input, previousNode })
  if (!edgeResult.ok) return edgeResult

  const tentativeSubmodels = structuredClone(input.submodels)
  const rootScope: RenameGraphScope = {
    nodes: input.graph.nodes.map((node) => (
      node.id === input.nodeId ? { ...node, data: input.data } : node
    )),
    edges: edgeResult.edges,
    submodels: tentativeSubmodels,
  }
  const affectedByScope = collectAffectedTargets(rootScope, edgeResult.rebound)
  const mappingChanges = collectMappingChanges(rootScope, affectedByScope)
  if ("ok" in mappingChanges) return mappingChanges
  applyMappingChanges(mappingChanges)
  const collision = findInputCollision(affectedByScope)
  if (collision) return collision

  return {
    ok: true,
    nodeId: input.nodeId,
    data: input.data,
    nodes: rootScope.nodes,
    edges: rootScope.edges,
    submodels: tentativeSubmodels,
    removed: edgeResult.removed,
  }
}
