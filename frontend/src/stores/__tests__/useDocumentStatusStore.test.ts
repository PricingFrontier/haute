import { describe, expect, it } from "vitest"
import useDocumentStatusStore, { documentReadOnlyReason } from "../useDocumentStatusStore"
import { parsePipelineEditorDocument } from "../../types/pipelineDocument"

const loaded = parsePipelineEditorDocument({ document_kind:"haute.pipeline_editor_document",schema_version:1,load_status:"ready",pipeline_name:null,pipeline_description:null,preamble:null,preserved_blocks:[],source_file:"main.py",source_revision:"r1",source_text:"x",sources:["live"],active_source:"live",source_selection_trusted:true,has_authored_content:false,nodes:[],edges:[],unresolved_connections:[],submodels:null,diagnostics:[],diagnostics_omitted:2,capabilities:{can_mutate:true,can_save:true,can_execute:true,can_preview:true,can_manage_submodels:true,can_repair:false,reserved_api_input_frame_labels:[]} })

describe("useDocumentStatusStore", () => {
  it("atomically loads authoritative document state and resets it", () => {
    useDocumentStatusStore.getState().loadDocumentStatus(loaded, false)
    expect(useDocumentStatusStore.getState().graphSynchronized).toBe(false)
    useDocumentStatusStore.getState().setGraphSynchronized(true)
    expect(useDocumentStatusStore.getState()).toMatchObject({ loadStatus:"ready", sourceRevision:"r1", sourceText:"x", diagnosticsOmitted:2, capabilities:{can_save:true} })
    useDocumentStatusStore.getState().reset()
    expect(useDocumentStatusStore.getState()).toMatchObject({ loadStatus:null, sourceText:"", capabilities:null })
  })

  it("tracks a live source-only canvas reference separately from current document state", () => {
    const sourceOnly = { ...loaded, load_status: "source_only" as const, source_revision: "r2" }
    useDocumentStatusStore.getState().loadLiveDocumentStatus(sourceOnly, {
      kind: "last_renderable",
      sourceRevision: "r1",
      loadStatus: "ready",
    }, false)

    expect(useDocumentStatusStore.getState()).toMatchObject({
      loadStatus: "source_only",
      sourceRevision: "r2",
      retainedCanvas: {
        kind: "last_renderable",
        sourceRevision: "r1",
        loadStatus: "ready",
      },
      graphSynchronized: false,
    })

    useDocumentStatusStore.getState().loadDocumentStatus(loaded)
    expect(useDocumentStatusStore.getState().retainedCanvas).toBeNull()
  })

  it("blocks a retained canvas for a live system failure and clears on recovery", () => {
    useDocumentStatusStore.getState().loadDocumentStatus(loaded)

    useDocumentStatusStore.getState().setSystemFailure(
      "Pipeline document could not be loaded.",
    )

    expect(useDocumentStatusStore.getState()).toMatchObject({
      systemFailure: "Pipeline document could not be loaded.",
      graphSynchronized: false,
    })

    useDocumentStatusStore.getState().loadDocumentStatus(loaded)
    expect(useDocumentStatusStore.getState().systemFailure).toBeNull()
  })

  it("explains read-only state by its actual cause, not always diagnostics", () => {
    // Mutable capabilities with an unsynchronized canvas: an external change
    // is pending, and blaming diagnostics would mislead.
    useDocumentStatusStore.getState().loadDocumentStatus(loaded, false)
    expect(documentReadOnlyReason()).toMatch(/changed on disk/)

    // Degraded capabilities: the diagnostics wording is correct.
    const degraded = {
      ...loaded,
      load_status: "degraded" as const,
      capabilities: { ...loaded.capabilities, can_mutate: false, can_save: false },
    }
    useDocumentStatusStore.getState().loadDocumentStatus(degraded, true)
    expect(documentReadOnlyReason()).toMatch(/load diagnostics/)

    // A live document-transport failure names itself, not diagnostics.
    useDocumentStatusStore.getState().loadDocumentStatus(loaded)
    useDocumentStatusStore.getState().setSystemFailure("boom")
    expect(documentReadOnlyReason()).toMatch(/could not be loaded/)
    useDocumentStatusStore.getState().reset()
  })
})
