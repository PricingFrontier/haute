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

/**
 * The document is the saved file and its persisted authored state equals the
 * submitted save's, compared as the dirty state compares them (`serializeSnapshot`:
 * nodes with their layout, edges, preamble, submodels, constants), together with
 * the preserved blocks, sources and active source. Transient execution and UI
 * fields take no part; the revision proves nothing, so it is not compared.
 */
export function documentHoldsSave(document: PipelineEditorDocument, submitted: SubmittedSave): boolean {
  if (document.source_file !== submitted.sourceFile) return false
  const adapted = adaptPipelineEditorDocument(document)
  const noConstants: GlobalConstantDraft[] = []
  const read = serializeSnapshot({
    nodes: adapted.nodes,
    edges: normalizeEdges(adapted.edges),
    preamble: document.preamble ?? "",
    submodels: adapted.submodels,
    globalConstants: submitted.constantsSent ? constantDrafts(document.global_constants) : noConstants,
  })
  const sent = serializeSnapshot({
    ...submitted.snapshot,
    globalConstants: submitted.constantsSent ? submitted.snapshot.globalConstants : noConstants,
  })
  return read === sent
    && sameList(document.preserved_blocks, submitted.preservedBlocks)
    && sameList(document.sources, submitted.sources)
    && document.active_source === submitted.activeSource
}
