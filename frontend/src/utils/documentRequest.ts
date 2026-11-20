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
 * (`editor.saved_settings`). Steps render against their node's inputs, so a node whose steps
 * name its inputs keeps its steps' saved form only while those inputs are as saved too. The
 * saved forms live in the baseline only: the live nodes never carry one, so no copy goes
 * stale, and only this module puts them on a request.
 */
import type { Edge, Node } from "@xyflow/react"
import type { GraphPayload } from "../api/types"
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
}

interface Document {
  nodes: readonly Node[]
  edges?: readonly Edge[]
  submodels?: Record<string, unknown>
}

const SAVED_FORM_KEYS = ["saved_code", "saved_steps", "saved_settings"] as const

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

/**
 * What the node `nodeId` of the graph `submodel` names in `document` is rendered against: the
 * edges into it, by source and handles, and in a definition the input ports that reach it.
 */
function inputsOf(document: Document, submodel: string | null, nodeId: string): string {
  let edges: readonly Edge[] = document.edges ?? []
  let ports: string[] = []
  if (submodel !== null) {
    const definition = document.submodels?.[submodel]
    if (!isSubmodelDefinition(definition, submodel)) return canonicalJson(null)
    edges = definition.graph.edges as readonly Edge[]
    ports = definition.inputPorts
      .filter((port) => port.targets.some((target) => target.nodeId === nodeId))
      .map((port) => port.name)
      .sort()
  }
  const incoming = edges
    .filter((edge) => edge.target === nodeId)
    .map((edge) => [edge.source, edge.sourceHandle ?? "", edge.targetHandle ?? ""])
    .sort((a, b) => canonicalJson(a).localeCompare(canonicalJson(b)))
  return canonicalJson([incoming, ports])
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
): SavedForms {
  const held = heldNode(baseline, submodel, node.id)
  if (baseline === null || held === null) return {}
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
      || inputsOf(document, submodel, node.id) === inputsOf(baseline, submodel, node.id)
    )
  ) {
    if (typeof saved.saved_code === "string") forms.saved_code = saved.saved_code
    else if (typeof saved.saved_steps === "string") forms.saved_steps = saved.saved_steps
  }
  if (typeof saved.saved_settings === "string" && canonicalJson(settingsOf(config)) === canonicalJson(settingsOf(heldConfig))) {
    forms.saved_settings = saved.saved_settings
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
export function withSavedForms<T extends Document>(document: T, baseline: Document | null): T {
  return mapNodes(document, (submodel, node) => withForms(node, savedFormsFor(baseline, document, submodel, node)))
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
  return withSavedForms(toCanonicalGraphPayload(input), baseline) satisfies GraphPayload
}

/** `document` with each part in `saved`, which a save read from its request, given the saved form it wrote. */
export function withSavedParts<T extends Document>(document: T, saved: readonly SavedNodeParts[]): T {
  if (saved.length === 0) return document
  const written = new Map(saved.map((entry) => [canonicalJson([entry.submodel, entry.node_id]), entry]))
  return mapNodes(document, (submodel, node) => {
    const entry = written.get(canonicalJson([submodel, node.id]))
    if (entry === undefined) return node
    const forms: SavedForms = {}
    const kept = editorState(node)
    if (typeof kept.saved_code === "string") forms.saved_code = kept.saved_code
    if (typeof kept.saved_steps === "string") forms.saved_steps = kept.saved_steps
    if (typeof kept.saved_settings === "string") forms.saved_settings = kept.saved_settings
    // A node's steps have one saved form: its code, or the list it was saved unfinished with.
    if (typeof entry.code === "string") {
      forms.saved_code = entry.code
      delete forms.saved_steps
    }
    if (typeof entry.steps === "string") {
      forms.saved_steps = entry.steps
      delete forms.saved_code
    }
    if (typeof entry.settings === "string") forms.saved_settings = entry.settings
    return withForms(node, forms)
  })
}
