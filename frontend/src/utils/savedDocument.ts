/**
 * Whether a document read back holds exactly what a save submitted: the test that
 * confirms a save whose answer was lost.
 */
import { serializeSnapshot } from "./graphSnapshot"
import { constantDrafts, type GlobalConstantDraft } from "./globalConstants"
import { normalizeEdges } from "./graphHelpers"
import type { GraphSnapshot } from "../stores/useGraphStore"
import { adaptPipelineEditorDocument, type PipelineEditorDocument } from "../types/pipelineDocument"

/** What a save sent, beside the snapshot it marks saved. */
export interface SubmittedSave {
  sourceFile: string
  pipelineName: string
  description: string
  snapshot: GraphSnapshot
  /** False when the constants file failed to load: the save sent none and the server kept it. */
  constantsSent: boolean
  preservedBlocks: readonly string[]
  sources: readonly string[]
  activeSource: string
}

function sameList(a: readonly string[], b: readonly string[]): boolean {
  return a.length === b.length && a.every((item, index) => item === b[index])
}

type Graph = Pick<GraphSnapshot, "nodes" | "edges">

/**
 * The name the file gives a node: the function name the server resolved for its
 * label. A read gives it as both the node's id and its label, while the canvas
 * may hold a provisional id, an id from before a rename, or a display label
 * ("Transform 3" persists as `Transform_3`).
 */
function persistedName(node: Graph["nodes"][number]): string {
  const data = node.data ?? {}
  if (typeof data._functionName === "string" && data._functionName !== "") return data._functionName
  return typeof data.label === "string" && data.label !== "" ? data.label : node.id
}

/**
 * The graph as the file holds it: each node known, and labelled, by its persisted
 * name, nodes in name order, and edges in their targets' name order. That sort is
 * stable, so a node's inputs keep their order, which the file keeps (an External
 * File hook's first input is its frame); nothing else about edge order is saved.
 */
function asPersisted(graph: Graph): Graph {
  const names = new Map(graph.nodes.map((node) => [node.id, persistedName(node)]))
  const name = (id: string) => names.get(id) ?? id
  const byName = (a: string, b: string) => (a < b ? -1 : a > b ? 1 : 0)
  return {
    nodes: graph.nodes
      .map((node) => ({ ...node, id: name(node.id), data: { ...node.data, label: name(node.id) } }))
      .sort((a, b) => byName(a.id, b.id)),
    edges: graph.edges
      .map((edge) => ({
        ...edge,
        id: `${name(edge.source)}:${edge.sourceHandle ?? ""}->${name(edge.target)}:${edge.targetHandle ?? ""}`,
        source: name(edge.source),
        target: name(edge.target),
      }))
      .sort((a, b) => byName(a.target, b.target)),
  }
}

/**
 * The document is the saved file and its persisted authored state equals the
 * submitted save's, compared as the dirty state compares them (`serializeSnapshot`:
 * nodes with their layout, edges, preamble, submodels, constants) as the file holds
 * it (`asPersisted`), together with the pipeline's name and description, the preserved
 * blocks, sources and active source. Transient execution and UI
 * fields take no part; the revision proves nothing, so it is not compared.
 */
export function documentHoldsSave(document: PipelineEditorDocument, submitted: SubmittedSave): boolean {
  if (document.source_file !== submitted.sourceFile) return false
  const adapted = adaptPipelineEditorDocument(document)
  const noConstants: GlobalConstantDraft[] = []
  const read = serializeSnapshot({
    ...asPersisted({ nodes: adapted.nodes, edges: normalizeEdges(adapted.edges) }),
    preamble: document.preamble ?? "",
    submodels: adapted.submodels,
    globalConstants: submitted.constantsSent ? constantDrafts(document.global_constants) : noConstants,
  })
  const sent = serializeSnapshot({
    ...submitted.snapshot,
    ...asPersisted(submitted.snapshot),
    globalConstants: submitted.constantsSent ? submitted.snapshot.globalConstants : noConstants,
  })
  return read === sent
    && (document.pipeline_name ?? "") === submitted.pipelineName
    && (document.pipeline_description ?? "") === submitted.description
    && sameList(document.preserved_blocks, submitted.preservedBlocks)
    && sameList(document.sources, submitted.sources)
    && document.active_source === submitted.activeSource
}
