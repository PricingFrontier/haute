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
 * The graph with each node known by its name, as the file names it: an editor
 * id that predates a rename, or a new node's provisional id, is not the id a
 * read of the file gives it. Nodes and edges are ordered by that identity.
 */
function byAuthoredName(graph: Graph): Graph {
  const names = new Map(graph.nodes.map((node) => [
    node.id,
    typeof node.data?.label === "string" && node.data.label !== "" ? node.data.label : node.id,
  ]))
  const name = (id: string) => names.get(id) ?? id
  const byId = <T extends { id: string }>(a: T, b: T) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0)
  return {
    nodes: graph.nodes.map((node) => ({ ...node, id: name(node.id) })).sort(byId),
    edges: graph.edges
      .map((edge) => ({
        ...edge,
        id: `${name(edge.source)}:${edge.sourceHandle ?? ""}->${name(edge.target)}:${edge.targetHandle ?? ""}`,
        source: name(edge.source),
        target: name(edge.target),
      }))
      .sort(byId),
  }
}

/**
 * The document is the saved file and its persisted authored state equals the
 * submitted save's, compared as the dirty state compares them (`serializeSnapshot`:
 * nodes with their layout, edges, preamble, submodels, constants), each node known
 * by its name, together with the pipeline's name and description, the preserved
 * blocks, sources and active source. Transient execution and UI
 * fields take no part; the revision proves nothing, so it is not compared.
 */
export function documentHoldsSave(document: PipelineEditorDocument, submitted: SubmittedSave): boolean {
  if (document.source_file !== submitted.sourceFile) return false
  const adapted = adaptPipelineEditorDocument(document)
  const noConstants: GlobalConstantDraft[] = []
  const read = serializeSnapshot({
    ...byAuthoredName({ nodes: adapted.nodes, edges: normalizeEdges(adapted.edges) }),
    preamble: document.preamble ?? "",
    submodels: adapted.submodels,
    globalConstants: submitted.constantsSent ? constantDrafts(document.global_constants) : noConstants,
  })
  const sent = serializeSnapshot({
    ...submitted.snapshot,
    ...byAuthoredName(submitted.snapshot),
    globalConstants: submitted.constantsSent ? submitted.snapshot.globalConstants : noConstants,
  })
  return read === sent
    && (document.pipeline_name ?? "") === submitted.pipelineName
    && (document.pipeline_description ?? "") === submitted.description
    && sameList(document.preserved_blocks, submitted.preservedBlocks)
    && sameList(document.sources, submitted.sources)
    && document.active_source === submitted.activeSource
}
