/**
 * What a request that carries the document sends (CGEN-11, CGEN-13; specs/codegen high-level,
 * "Which parts a request reads").
 *
 * A node has two parts: its steps (the step list with the code it renders, `steps` and `code`)
 * and its settings (every other key of its config). The browser has lost what JavaScript
 * cannot hold (`1.0` is `1`, an integer past 2^53 its nearest double), so the server reads a
 * part from a request only when the editor edited it. Every part as the saved baseline holds
 * it is sent with the saved form the server gave for it, which the browser keeps exactly: the
 * code saved for the steps (`editor.saved_code`, and as the node's code), or for a node saved
 * with unfinished steps the list itself (`editor.saved_steps`), and the settings' JSON text
 * (`editor.saved_settings`). Edited settings go with the saved ones they began from
 * (`editor.settings_edited_from`), from which the server keeps the spelling of every number the
 * edit left as it was (CGEN-09). Steps render against their node's inputs, so a node whose steps
 * name its inputs keeps its steps' saved form only while those inputs are as saved too. The
 * saved forms live in the baseline only: the live nodes never carry one, so no copy goes
 * stale, and only this module puts them on a request.
 */
import type { Edge, Node } from "@xyflow/react"
import type { GraphPayload } from "../api/types"
import type { MovedNode } from "../generated/api-contracts.generated"
import { isSubmodelDefinition, type SubmodelDefinition } from "../types/node"
import { canonicalJson } from "./canonicalJson"
import { toCanonicalGraphPayload } from "./graphSnapshot"
import { isSteppedConfig, steppedSurfaceAllowsInputReferences } from "./polarsStepInputs"

/** The saved form of each part a save read from its request, as the server answers it. */
export interface SavedNodeParts {
  submodel: string | null
  node_id: string
  code?: string | null
  steps?: string | null
  settings?: string | null
}

/** The saved form of a node's parts that are as the baseline holds them. */
export interface SavedForms {
  saved_code?: string
  saved_steps?: string
  saved_settings?: string
  settings_edited_from?: string
}

interface Document {
  nodes: readonly Node[]
  edges?: readonly Edge[]
  submodels?: Record<string, unknown>
}

const SAVED_FORM_KEYS = ["saved_code", "saved_steps", "saved_settings", "settings_edited_from"] as const

function nodeConfig(node: Node): Record<string, unknown> {
  const config = (node.data as { config?: unknown } | undefined)?.config
  return config !== null && typeof config === "object" && !Array.isArray(config) ? config as Record<string, unknown> : {}
}

function editorState(node: Node): Record<string, unknown> {
  const editor = (node.data as { editor?: unknown } | undefined)?.editor
  return editor !== null && typeof editor === "object" && !Array.isArray(editor) ? editor as Record<string, unknown> : {}
}

/** A config's settings: every key but its steps'. */
function settingsOf(config: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(Object.entries(config).filter(([key]) => key !== "steps" && key !== "code"))
}

function definitionNodes(submodels: Record<string, unknown> | undefined, id: string): readonly Node[] | null {
  const definition = submodels?.[id]
  return isSubmodelDefinition(definition, id) ? (definition.graph.nodes as Node[]) : null
}

function heldNode(baseline: Document | null, submodel: string | null, nodeId: string): Node | null {
  if (baseline === null) return null
  const nodes = submodel === null ? baseline.nodes : definitionNodes(baseline.submodels, submodel)
  return nodes?.find((candidate) => candidate.id === nodeId) ?? null
}

/** Where a node sits: a graph's scope (a definition's id, or null) and the node's id. */
interface Address {
  submodel: string | null
  nodeId: string
}

function asAddress(value: unknown): Address | null {
  if (value === null || typeof value !== "object") return null
  const { submodel, nodeId } = value as { submodel?: unknown; nodeId?: unknown }
  return (submodel === null || typeof submodel === "string") && typeof nodeId === "string"
    ? { submodel, nodeId }
    : null
}

/** What a node records of its saved baseline: its identity once a graph change has moved it
 * (`data._savedAddress`), and, for a copy one made, the identities of what it was copied
 * from, nearest first (`data._savedOrigins`). */
interface Recorded {
  address: Address | null
  origins: Address[]
}

function recordedOf(node: Node): Recorded {
  const data = node.data as { _savedAddress?: unknown; _savedOrigins?: unknown } | undefined
  const origins = Array.isArray(data?._savedOrigins) ? data._savedOrigins : []
  return {
    address: asAddress(data?._savedAddress),
    origins: origins.map(asAddress).filter((origin): origin is Address => origin !== null),
  }
}

function withRecorded(node: Node, recorded: Recorded): Node {
  const data: Record<string, unknown> = { ...node.data }
  delete data._savedAddress
  delete data._savedOrigins
  if (recorded.address !== null) data._savedAddress = recorded.address
  if (recorded.origins.length > 0) data._savedOrigins = recorded.origins
  return { ...node, data }
}

function nodeTypeOf(node: Node): string {
  return String((node.data as { nodeType?: unknown } | undefined)?.nodeType)
}

/** A node's identity: where it was when the document was loaded, or, for a copy a graph
 * change made, where the copy was made. The place it records once a graph change has moved
 * it (`data._savedAddress`), else where it is. A node keeps it through every move, save and
 * Undo, and so does each node of a baseline a save makes from the document. Two nodes never
 * share one: a copy takes its own. */
function identityOf(submodel: string | null, node: Node, address: Address | null = recordedOf(node).address): string {
  const identity = address ?? { submodel, nodeId: node.id }
  return placeKey(identity.submodel, identity.nodeId)
}

type BaselineIndex = Map<string, { held: Node; address: Address }[]>

/** Each node of `baseline` by its identity, with where it is. */
function baselineIndex(baseline: Document): BaselineIndex {
  const index: BaselineIndex = new Map()
  mapNodes(baseline, (submodel, held) => {
    const key = identityOf(submodel, held)
    const entries = index.get(key) ?? []
    entries.push({ held, address: { submodel, nodeId: held.id } })
    index.set(key, entries)
    return held
  })
  return index
}

/**
 * The node of `baseline` that `node`, of the graph `submodel` names, is saved as, and where it
 * is: the baseline's node of the same type and identity, the one at the node's own place if
 * there are two, else, for a copy not saved since it was made, the node it was copied from.
 * Identity, not place, so a node moved by a graph change is found before a save and after one,
 * and after an Undo past one.
 */
function baselineNodeFor(
  baseline: Document | null,
  submodel: string | null,
  node: Node,
  recorded: Recorded = recordedOf(node),
  index?: BaselineIndex,
): { held: Node; address: Address } | null {
  if (baseline === null) return null
  const held = index ?? baselineIndex(baseline)
  const ofType = (key: string) => (held.get(key) ?? []).filter((entry) => nodeTypeOf(entry.held) === nodeTypeOf(node))
  const own = ofType(identityOf(submodel, node, recorded.address))
  const found = own.find(({ address }) => address.submodel === submodel && address.nodeId === node.id) ?? own[0]
  if (found !== undefined) return found
  for (const origin of recorded.origins) {
    const copied = ofType(placeKey(origin.submodel, origin.nodeId))[0]
    if (copied !== undefined) return copied
  }
  return null
}

/**
 * What the node `nodeId` of the graph `submodel` names in `document` is rendered against: the
 * edges into it, by source and handles, and in a definition the input ports that reach it.
 */
function inputsOf(document: Document, submodel: string | null, nodeId: string): string {
  let edges: readonly Edge[] = document.edges ?? []
  let ports: string[] = []
  if (submodel !== null) {
    const definition = document.submodels?.[submodel]
    if (!isSubmodelDefinition(definition, submodel)) return ""
    edges = definition.graph.edges as readonly Edge[]
    ports = definition.inputPorts
      .filter((port) => port.targets.some((target) => target.nodeId === nodeId))
      .map((port) => port.name)
      .sort()
  }
  const incoming = edges
    .filter((edge) => edge.target === nodeId)
    .map((edge) => JSON.stringify([edge.source, edge.sourceHandle ?? "", edge.targetHandle ?? ""]))
    .sort()
  return JSON.stringify([incoming, ports])
}

/**
 * The saved form of each part of `node`, of the graph `submodel` names in `document`, that is
 * as `baseline` holds it: none for a node the baseline does not hold, or holds without a saved
 * form. A node whose steps name its inputs keeps its steps' only while those are the
 * baseline's too: the code saved for its steps was rendered against them.
 */
export function savedFormsFor(
  baseline: Document | null,
  document: Document,
  submodel: string | null,
  node: Node,
  recorded: Recorded = recordedOf(node),
  index?: BaselineIndex,
): SavedForms {
  const found = baselineNodeFor(baseline, submodel, node, recorded, index)
  if (baseline === null || found === null) return {}
  const { held, address } = found
  const config = nodeConfig(node)
  const heldConfig = nodeConfig(held)
  const saved = editorState(held)
  const forms: SavedForms = {}
  const nodeType = String((node.data as { nodeType?: unknown }).nodeType)
  if (
    isSteppedConfig(nodeType, config)
    && canonicalJson(config.steps) === canonicalJson(heldConfig.steps)
    && (
      !steppedSurfaceAllowsInputReferences(nodeType)
      || inputsOf(document, submodel, node.id) === inputsOf(baseline, address.submodel, address.nodeId)
    )
  ) {
    if (typeof saved.saved_code === "string") forms.saved_code = saved.saved_code
    else if (typeof saved.saved_steps === "string") forms.saved_steps = saved.saved_steps
  }
  if (typeof saved.saved_settings === "string") {
    if (canonicalJson(settingsOf(config)) === canonicalJson(settingsOf(heldConfig))) forms.saved_settings = saved.saved_settings
    else forms.settings_edited_from = saved.saved_settings
  }
  return forms
}

/**
 * The code the baseline saved for `steps` on the node `nodeId` of the graph `submodel` names,
 * or null when it does not hold the node, holds other steps for it, or holds no saved code:
 * the steps were edited.
 */
export function savedCodeForSteps(
  baseline: Document | null,
  submodel: string | null,
  nodeId: string,
  steps: unknown,
): string | null {
  const held = heldNode(baseline, submodel, nodeId)
  if (held === null || canonicalJson(nodeConfig(held).steps) !== canonicalJson(steps)) return null
  const saved = editorState(held).saved_code
  return typeof saved === "string" ? saved : null
}

function withForms(node: Node, forms: SavedForms): Node {
  const editor = Object.fromEntries(
    Object.entries(editorState(node)).filter(([key]) => !(SAVED_FORM_KEYS as readonly string[]).includes(key)),
  )
  const hasEditor = (node.data as { editor?: unknown }).editor !== undefined
  const nextEditor = { ...editor, ...forms }
  const config = nodeConfig(node)
  const data: Record<string, unknown> = { ...node.data }
  if (hasEditor || Object.keys(forms).length > 0) data.editor = nextEditor
  if (forms.saved_code !== undefined && config.code !== forms.saved_code) data.config = { ...config, code: forms.saved_code }
  return { ...node, data }
}

function mapNodes<T extends Document>(document: T, give: (submodel: string | null, node: Node) => Node): T {
  const nodes = document.nodes.map((node) => give(null, node))
  if (document.submodels === undefined) return { ...document, nodes }
  const submodels = Object.fromEntries(
    Object.entries(document.submodels).map(([id, definition]) => {
      if (!isSubmodelDefinition(definition, id)) return [id, definition]
      const graph = { ...definition.graph, nodes: (definition.graph.nodes as Node[]).map((node) => give(id, node)) }
      return [id, { ...definition, graph } satisfies SubmodelDefinition]
    }),
  )
  return { ...document, nodes, submodels }
}

/**
 * `document` with each part of each node that is as `baseline` holds it given its saved form,
 * and no other saved form: unedited steps carry the code saved for them, as the node's code
 * too, never the live node's own copy, a cache Undo and Redo restore with whatever rendering
 * it held. What a save sends and keeps as its next baseline.
 */
export function withSavedForms<T extends Document>(
  document: T,
  baseline: Document | null,
  recorded: ReadonlyMap<string, Recorded> = new Map(),
): T {
  const index = baseline === null ? undefined : baselineIndex(baseline)
  return mapNodes(document, (submodel, node) => withForms(
    node,
    savedFormsFor(baseline, document, submodel, node, recorded.get(placeKey(submodel, node.id)) ?? recordedOf(node), index),
  ))
}

function placeKey(submodel: string | null, nodeId: string): string {
  return canonicalJson([submodel, nodeId])
}

/** What each node of `document` that a graph change moved or made records of its baseline. */
function recordsIn(document: Document): Map<string, Recorded> {
  const found = new Map<string, Recorded>()
  mapNodes(document, (submodel, node) => {
    const recorded = recordedOf(node)
    if (recorded.address !== null || recorded.origins.length > 0) found.set(placeKey(submodel, node.id), recorded)
    return node
  })
  return found
}

/**
 * `document`, a graph change's answer, with each node keeping its identity and what it records
 * of its baseline (`identityOf`), which the answer, built on the server, does not carry. For a
 * node of the same type in `before`, the document the change was asked of:
 * - a node the change left in place keeps what it had;
 * - a node it `moved` is the node it was, so it takes that node's identity (the place it moved
 *   from, unless it had moved before);
 * - a node it copied, whose original the answer still holds where it was (a dissolve of one
 *   occurrence of a definition others use), is another node: its identity is its own place,
 *   and it records the identity of what it was copied from, so it finds that node's saved
 *   forms until a save holds its own.
 * An address, never a copy of a form: `savedFormsFor` finds a node's baseline node by it.
 */
export function withSavedAddresses<T extends Document>(document: T, moved: readonly MovedNode[], before: Document): T {
  const arrived = new Map(moved.map((move) => [placeKey(move.to_submodel, move.to_node_id), move]))
  return mapNodes(document, (submodel, node) => {
    const move = arrived.get(placeKey(submodel, node.id))
    const fromPlace = move === undefined
      ? { submodel, nodeId: node.id }
      : { submodel: move.from_submodel, nodeId: move.from_node_id }
    const from = heldNode(before, fromPlace.submodel, fromPlace.nodeId)
    const had: Recorded = from !== null && nodeTypeOf(from) === nodeTypeOf(node)
      ? recordedOf(from)
      : { address: null, origins: [] }
    if (move === undefined) return withRecorded(node, had)
    const identity = had.address ?? fromPlace
    const original = heldNode(document, fromPlace.submodel, fromPlace.nodeId)
    const copied = original !== null && nodeTypeOf(original) === nodeTypeOf(node)
    return withRecorded(node, copied
      ? { address: null, origins: [identity, ...had.origins] }
      : { address: identity, origins: had.origins })
  })
}

/** `document` with no saved form on any node: the live document's copy. */
export function withoutSavedForms<T extends Document>(document: T): T {
  return mapNodes(document, (_submodel, node) => withForms(node, {}))
}

/**
 * The one way a request that carries the document builds it: the canonical payload
 * (`toCanonicalGraphPayload`, which takes every saved form off), each part as `baseline` holds
 * it with its saved form. A request whose answer the editor adopts as an edit (a transform of
 * the graph) passes no baseline, so the server reads every part and hands back what it was
 * sent.
 */
export function documentRequestGraph(
  input: Parameters<typeof toCanonicalGraphPayload>[0],
  baseline: Document | null,
): ReturnType<typeof toCanonicalGraphPayload> {
  // The canonical payload leaves out the editor's own fields, a moved node's saved address
  // among them, so each node's address is read from the input.
  return withSavedForms(toCanonicalGraphPayload(input), baseline, recordsIn(input as Document)) satisfies GraphPayload
}

/** `document` with each part in `saved`, which a save read from its request, given the saved form it wrote. */
export function withSavedParts<T extends Document>(document: T, saved: readonly SavedNodeParts[]): T {
  if (saved.length === 0) return document
  const written = new Map(saved.map((entry) => [canonicalJson([entry.submodel, entry.node_id]), entry]))
  return mapNodes(document, (submodel, node) => {
    const entry = written.get(canonicalJson([submodel, node.id]))
    if (entry === undefined) return node
    const kept = editorState(node) as SavedForms
    // A node's steps have one saved form: its code, or the list it was saved unfinished with.
    const steps: SavedForms = typeof entry.code === "string"
      ? { saved_code: entry.code }
      : typeof entry.steps === "string"
        ? { saved_steps: entry.steps }
        : { saved_code: kept.saved_code, saved_steps: kept.saved_steps }
    const forms: SavedForms = { ...steps, saved_settings: entry.settings ?? kept.saved_settings }
    return withForms(node, Object.fromEntries(Object.entries(forms).filter(([, form]) => typeof form === "string")))
  })
}
