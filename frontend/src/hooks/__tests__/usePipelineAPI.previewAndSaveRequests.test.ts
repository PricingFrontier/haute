/**
 * usePipelineAPI preview and save requests: a failed preview shows an error unless it was
 * aborted or superseded, concurrent saves take turns and a save whose answer was lost is
 * held and sent again first by the next Save, under the same operation, a fresh cached preview is shown without a request
 * while a stale, re-sourced or re-limited one is refetched, and a new preview aborts the one in
 * flight.
 */
import { makeSavePipelineResponse } from "../../test-utils/factories"
import type { PreviewNodeResponse } from "../../api/types"
import { makePreviewResponse } from "../../test-utils/factories"
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { renderHook, cleanup, act, waitFor } from "@testing-library/react"
import type { Node } from "@xyflow/react"
import usePipelineAPI from "../usePipelineAPI"
import useToastStore from "../../stores/useToastStore"
import useSettingsStore from "../../stores/useSettingsStore"
import useGraphStore from "../../stores/useGraphStore"
import useNodeResultsStore from "../../stores/useNodeResultsStore"
import useNodeDataStore from "../../stores/useNodeDataStore"
import useDocumentStatusStore from "../../stores/useDocumentStatusStore"
import { pendingSavesSettled } from "../../stores/pendingSaves"

vi.mock("../../api/client", async (importOriginal) => {
  const client = await importOriginal<typeof import("../../api/client")>()
  return {
    loadPipeline: vi.fn(),
    loadPipelineDocument: vi.fn(),
    previewInputs: vi.fn(async () => ({ input_node_ids: [], node_addresses: {} })),
    previewNode: vi.fn(),
    previewRecoveryNode: vi.fn(),
    savePipeline: vi.fn(),
    // The client's own ApiError, by which the keyed path decides what an answer settles.
    ApiError: client.ApiError,
  }
})

vi.mock("../../utils/buildGraph", () => ({
  documentGraphPayload: vi.fn(() => ({ nodes: [], edges: [], preamble: "" })),
}))

vi.mock("../../utils/makePreviewData", () => ({
  makePreviewData: vi.fn((nodeId: string, label: string, opts: Record<string, unknown>) => ({
    nodeId,
    nodeLabel: label,
    status: opts.status || "ok",
    row_count: opts.row_count ?? 0,
    column_count: opts.column_count ?? 0,
    columns: opts.columns ?? [],
    preview: opts.preview ?? [],
    error: opts.error ?? null,
    timing_ms: opts.timing_ms ?? 0,
    memory_bytes: opts.memory_bytes ?? 0,
    timings: opts.timings ?? [],
    memory: opts.memory ?? [],
    schema_warnings: opts.schema_warnings ?? [],
  })),
}))

import { loadPipeline, loadPipelineDocument, previewNode, savePipeline, ApiError } from "../../api/client"
import { makeNode } from "../../test-utils/factories"
import { NODE_TYPES } from "../../utils/nodeTypes"
import { makeLoadedPipeline, makePipelineEditorDocument } from "../../testSupport/pipelineDocumentFixture"
const mockLoad = vi.mocked(loadPipeline)
const mockPreview = vi.mocked(previewNode)
const mockSave = vi.mocked(savePipeline)

async function advanceTimers(ms: number) {
  await act(async () => {
    vi.advanceTimersByTime(ms)
    await Promise.resolve()
    await Promise.resolve()
  })
}

function makeParams(overrides: Partial<Parameters<typeof usePipelineAPI>[0]> = {}) {
  return {
    selectedNode: null as Node | null,
    activeSubmodelIdentity: null,
    setNodes: vi.fn(),
    setNodesRaw: vi.fn(),
    setEdgesRaw: vi.fn(),
    setPreamble: vi.fn(),
    pipelineNameRef: { current: "test" },
    descriptionRef: { current: "" },
    sourceFileRef: { current: "test.py" },
    sourceRevisionRef: { current: "revision-test" },
    preservedBlocksRef: { current: [] as string[] },
    nodeIdCounter: { current: 0 },
    ...overrides,
  }
}

describe("usePipelineAPI - preview and save requests", () => {
  beforeEach(() => {
    vi.useRealTimers()
    useSettingsStore.setState({ rowLimit: 1000, activeSource: "live", sources: ["live"] })
    useGraphStore.setState({
      nodes: [],
      edges: [],
      preamble: "",
      lastSavedSnapshot: null,
      undoStack: [],
      redoStack: [],
      structuralVersion: 0,
      structuralFingerprint: "nodes:||edges:||preamble:\"\"",
    })
    useNodeResultsStore.setState({ previews: {}, columnCache: {} })
    mockLoad.mockReset()
    mockPreview.mockReset()
    mockSave.mockReset()
  })

  afterEach(() => {
    vi.useRealTimers()
    cleanup()
    vi.restoreAllMocks()
  })

  // ────────────────────────────────────────────────────────────────
  // 1. Preview error handling — ApiError vs AbortError
  // ────────────────────────────────────────────────────────────────

  describe("preview error handling", () => {
    it("shows error preview when previewNode rejects with ApiError", async () => {
      // Catches: if the catch block doesn't distinguish ApiError from
      // AbortError, genuine server errors would be silently swallowed,
      // leaving the user staring at a loading spinner forever.
      mockLoad.mockResolvedValue(makeLoadedPipeline({ nodes: [], edges: [] }))

      const apiErr = new (ApiError as unknown as new (msg: string) => Error)("Column 'x' not found")
      mockPreview.mockRejectedValue(apiErr)

      const params = makeParams()
      const { result } = renderHook(() => usePipelineAPI(params))
      await waitFor(() => expect(result.current.loading).toBe(false))

      const node = makeNode("n1")

      act(() => {
        result.current.fetchPreview(node)
      })

      // fetchPreview debounces by 200ms — use real timers and just wait
      await waitFor(() => {
        expect(result.current.previewData?.status).toBe("error")
        expect(result.current.previewData?.error).toBe("Column 'x' not found")
      }, { timeout: 2000 })
    })

    it("does NOT show error preview when request is aborted (AbortError)", async () => {
      // Catches: if AbortError is treated as a real error, every time the
      // user clicks a different node (which aborts the previous request),
      // they'd see a brief red error flash.
      mockLoad.mockResolvedValue(makeLoadedPipeline({ nodes: [], edges: [] }))

      const abortErr = new DOMException("The operation was aborted.", "AbortError")
      mockPreview.mockRejectedValue(abortErr)

      const params = makeParams()
      const { result } = renderHook(() => usePipelineAPI(params))
      await waitFor(() => expect(result.current.loading).toBe(false))

      const node = makeNode("n1")

      vi.useFakeTimers()

      act(() => {
        result.current.fetchPreview(node)
      })

      // Wait for debounce + rejection to settle
      await advanceTimers(200)
      expect(mockPreview).toHaveBeenCalledTimes(1)

      // AbortError should be ignored — previewData should still be "loading", NOT "error"
      expect(result.current.previewData?.status).not.toBe("error")
    })

    it("does NOT show error preview when the backend supersedes an obsolete preview", async () => {
      mockLoad.mockResolvedValue(makeLoadedPipeline({ nodes: [], edges: [] }))

      // Reworded on purpose: the code, not the message, says the preview was superseded.
      const superseded = { error_code: "request_superseded", message: "A newer preview of this node replaced this one" }
      mockPreview.mockRejectedValue(
        new ApiError("HTTP 409", 409, JSON.stringify(superseded), { detail: superseded }, superseded),
      )

      const params = makeParams()
      const { result } = renderHook(() => usePipelineAPI(params))
      await waitFor(() => expect(result.current.loading).toBe(false))

      vi.useFakeTimers()

      act(() => {
        result.current.fetchPreview(makeNode("n1"))
      })

      await advanceTimers(200)
      expect(mockPreview).toHaveBeenCalledTimes(1)
      expect(result.current.previewData?.status).not.toBe("error")
    })

    it("shows a 409 that only mentions supersession as a preview error", async () => {
      mockLoad.mockResolvedValue(makeLoadedPipeline({ nodes: [], edges: [] }))

      // No code: a worker stopped for another reason is a failure the panel shows.
      mockPreview.mockRejectedValue(
        new ApiError("HTTP 409", 409, "Interactive execution was stopped with reason 'superseded'"),
      )

      const params = makeParams()
      const { result } = renderHook(() => usePipelineAPI(params))
      await waitFor(() => expect(result.current.loading).toBe(false))

      vi.useFakeTimers()

      act(() => {
        result.current.fetchPreview(makeNode("n1"))
      })

      await advanceTimers(200)
      expect(mockPreview).toHaveBeenCalledTimes(1)
      expect(result.current.previewData?.status).toBe("error")
    })
  })

  // ────────────────────────────────────────────────────────────────
  // 2. Concurrent saves
  // ────────────────────────────────────────────────────────────────

  describe("concurrent saves", () => {
    it("both saves complete independently (no save dedup)", async () => {
      // Catches: if handleSave had a guard preventing concurrent saves,
      // rapid Ctrl+S presses might silently skip the second save. The
      // current implementation does NOT deduplicate — both calls go through.
      mockLoad.mockResolvedValue(makeLoadedPipeline({ nodes: [], edges: [] }))

      let saveCallCount = 0
      mockSave.mockImplementation(() => {
        saveCallCount++
        return Promise.resolve(makeSavePipelineResponse({ file: `pricing_${saveCallCount}.py`, pipeline_name: "pricing", source_revision: "revision-test", document_fingerprint: "fingerprint-saved" }))
      })

      const params = makeParams()

      const { result } = renderHook(() => usePipelineAPI(params))
      await waitFor(() => expect(result.current.loading).toBe(false))
      useGraphStore.setState({ nodes: [makeNode("n1")], edges: [] })

      // Fire two saves without awaiting the first
      await act(async () => {
        result.current.handleSave()
        result.current.handleSave()
      })

      await waitFor(() => {
        expect(saveCallCount).toBe(2)
      })
    })

    it("adopts the saved document's fingerprint, and holds live-sync frames until the save answers", async () => {
      mockLoad.mockResolvedValue(makeLoadedPipeline({ nodes: [], edges: [] }))
      let answer!: (response: Awaited<ReturnType<typeof savePipeline>>) => void
      mockSave.mockReturnValueOnce(new Promise((resolve) => { answer = resolve }))
      const params = makeParams()
      const { result } = renderHook(() => usePipelineAPI(params))
      await waitFor(() => expect(result.current.loading).toBe(false))
      useGraphStore.setState({ nodes: [makeNode("n1")], edges: [] })
      const sourceFile = useDocumentStatusStore.getState().sourceFile
      expect(pendingSavesSettled(sourceFile)).toBeNull()

      let saving!: Promise<boolean>
      act(() => { saving = result.current.handleSave() })
      const held = pendingSavesSettled(sourceFile)
      expect(held).not.toBeNull()
      await act(async () => {
        answer(makeSavePipelineResponse({ file: "pricing.py", pipeline_name: "pricing", source_revision: "revision-saved", document_fingerprint: "fingerprint-saved" }))
        await saving
        await held
      })

      expect(useDocumentStatusStore.getState()).toMatchObject({
        sourceRevision: "revision-saved",
        documentFingerprint: "fingerprint-saved",
      })
      expect(pendingSavesSettled(sourceFile)).toBeNull()
    })

    describe("whose answer was lost", () => {
      const onDisk = () => makePipelineEditorDocument({
        source_file: "rating/main.py",
        source_revision: "revision-test",
        pipeline_name: "test",
        nodes: [makeNode("n1")],
        preserved_blocks: ["# kept"],
      })
      const written = (revision: string) => ({
        file: "rating/main.py",
        pipeline_name: "test",
        source_revision: revision,
        document_fingerprint: `fingerprint-${revision}`,
      } as Awaited<ReturnType<typeof savePipeline>>)
      const sent = (call: number) => mockSave.mock.calls[call][0]

      /** The editor loaded, and a save whose answer `first` loses: it resolves the hook. */
      async function afterALostSave(first: unknown = new TypeError("Failed to fetch")) {
        mockLoad.mockResolvedValue({ document: onDisk(), documentFingerprint: "fingerprint-loaded" })
        mockSave.mockRejectedValueOnce(first)
        const params = makeParams()
        const { result } = renderHook(() => usePipelineAPI(params))
        await waitFor(() => expect(result.current.loading).toBe(false))
        let saved!: boolean
        await act(async () => { saved = await result.current.handleSave() })
        expect(saved).toBe(false)
        expect(mockSave).toHaveBeenCalledTimes(1)
        return result
      }
      const save = async (result: { current: ReturnType<typeof usePipelineAPI> }) => {
        let saved!: boolean
        await act(async () => { saved = await result.current.handleSave() })
        return saved
      }
      const typeSince = () => act(() => {
        useGraphStore.getState().setNodes((nodes) => [...nodes, makeNode("n2")])
      })

      it("is reported failed and held: the next Save sends it again exactly, under its operation", async () => {
        const result = await afterALostSave()
        expect(useToastStore.getState().toasts.at(-1)).toMatchObject({ type: "error", text: "Failed to save pipeline: Failed to fetch" })

        mockSave.mockResolvedValueOnce(written("revision-written"))
        expect(await save(result)).toBe(true)

        expect(mockSave).toHaveBeenCalledTimes(2)
        expect(sent(0).operation).toMatch(/^save-/)
        expect(sent(1)).toEqual(sent(0))
        expect(loadPipelineDocument).not.toHaveBeenCalled()
        expect(useDocumentStatusStore.getState()).toMatchObject({
          sourceRevision: "revision-written",
          documentFingerprint: "fingerprint-revision-written",
        })
        expect(useGraphStore.getState().dirty).toBe(false)
        expect(useToastStore.getState().toasts.at(-1)).toMatchObject({ type: "success", text: "Saved → rating/main.py" })
      })

      it.each([
        ["no answer", new TypeError("Failed to fetch")],
        ["a server failure", new ApiError("HTTP 500", 500, "Operation failed. Check the server logs for details.")],
        ["a gateway's page", new ApiError("HTTP 504", 504, "Gateway Timeout")],
      ])("after %s, the next Save lands it and saves what was typed since on the revision it answered", async (_case, lost) => {
        const result = await afterALostSave(lost)
        typeSince()

        mockSave.mockResolvedValueOnce(written("revision-written"))
        mockSave.mockResolvedValueOnce(written("revision-since"))
        expect(await save(result)).toBe(true)

        expect(mockSave).toHaveBeenCalledTimes(3)
        expect(sent(1)).toEqual(sent(0))
        expect(sent(2).base_revision).toBe("revision-written")
        expect(sent(2).operation).not.toBe(sent(0).operation)
        expect(sent(2).graph.nodes.map((node) => node.id)).toContain("n2")
        expect(useDocumentStatusStore.getState()).toMatchObject({
          sourceRevision: "revision-since",
          graphSynchronized: true,
        })
        expect(useGraphStore.getState().dirty).toBe(false)
      })

      it("saves a source chosen since once the held save lands, though the graph is as it saved", async () => {
        const result = await afterALostSave()
        act(() => {
          useSettingsStore.getState().setSources(["live", "staging"])
          useSettingsStore.getState().setActiveSource("staging")
        })

        mockSave.mockResolvedValueOnce(written("revision-written"))
        mockSave.mockResolvedValueOnce(written("revision-since"))
        expect(await save(result)).toBe(true)

        expect(mockSave).toHaveBeenCalledTimes(3)
        expect(sent(1)).toEqual(sent(0))
        expect([sent(2).active_source, sent(2).base_revision]).toEqual(["staging", "revision-written"])
      })

      it("sets the reload banner when it is refused as stale, and sends nothing newer", async () => {
        const result = await afterALostSave()
        typeSince()

        mockSave.mockRejectedValueOnce(new ApiError("Conflict", 409, "stale_document_revision: disk moved"))
        expect(await save(result)).toBe(false)

        expect(mockSave).toHaveBeenCalledTimes(2)
        expect(useDocumentStatusStore.getState()).toMatchObject({
          sourceRevision: "revision-test",
          graphSynchronized: false,
        })
        expect(useToastStore.getState().toasts.at(-1)?.text).toMatch(/^Save rejected: the pipeline changed on disk/)
      })

      it("stays held when its answer is lost again, and nothing newer is sent before it", async () => {
        const result = await afterALostSave()
        typeSince()

        mockSave.mockRejectedValueOnce(new TypeError("Failed to fetch"))
        expect(await save(result)).toBe(false)
        mockSave.mockResolvedValueOnce(written("revision-written"))
        mockSave.mockResolvedValueOnce(written("revision-since"))
        expect(await save(result)).toBe(true)

        expect(mockSave).toHaveBeenCalledTimes(4)
        expect([sent(1), sent(2)]).toEqual([sent(0), sent(0)])
        expect(sent(3).base_revision).toBe("revision-written")
      })

      it("is let go by an answer: the next Save is a new one", async () => {
        const result = await afterALostSave(new ApiError("HTTP 400", 400, "bad graph"))

        mockSave.mockResolvedValueOnce(written("revision-written"))
        expect(await save(result)).toBe(true)

        expect(mockSave).toHaveBeenCalledTimes(2)
        expect(sent(1).operation).not.toBe(sent(0).operation)
      })

      it("is dropped when the tab takes the document from disk", async () => {
        const result = await afterALostSave()
        act(() => result.current.adoptPipelineDocument(onDisk(), "fingerprint-reloaded"))
        typeSince()

        mockSave.mockResolvedValueOnce(written("revision-written"))
        expect(await save(result)).toBe(true)

        expect(mockSave).toHaveBeenCalledTimes(2)
        expect(sent(1).operation).not.toBe(sent(0).operation)
      })
    })

    it("no longer reports a save that stopped part-way once a save lands", async () => {
      const interrupted = {
        diagnostic_id: "save_interrupted:abc",
        code: "save_interrupted",
        severity: "warning" as const,
        scope: "pipeline" as const,
        message: "A save stopped part-way, so these files may each hold a different save: rating/main.py.",
        element_id: null,
        source_file: "rating/main.py",
        source_span: null,
        remediation: "Saving the pipeline writes them all again.",
        incident_id: null,
      }
      mockLoad.mockResolvedValue({
        document: makePipelineEditorDocument({ source_file: "rating/main.py", source_revision: "revision-test", diagnostics: [interrupted] }),
        documentFingerprint: "fingerprint-loaded",
      })
      const { result } = renderHook(() => usePipelineAPI(makeParams()))
      await waitFor(() => expect(result.current.loading).toBe(false))
      expect(useDocumentStatusStore.getState().diagnostics.map((d) => d.code)).toEqual(["save_interrupted"])

      mockSave.mockResolvedValueOnce(makeSavePipelineResponse({ source_revision: "revision-saved", document_fingerprint: "fingerprint-saved" }))
      await act(async () => { await result.current.handleSave() })

      expect(useDocumentStatusStore.getState().diagnostics).toEqual([])
    })

    describe("of the parts nobody edited (CGEN-11, CGEN-13)", () => {
      const steps = [
        { id: "s", kind: "source", input: "quotes" },
        { id: "w", kind: "with_column", name: "r", expr: { type: "operand", operand: { kind: "literal", type: "number", value: 1 } } },
      ]
      const saved = 'df = quotes\ndf = df.with_columns(pl.lit(1.0).alias("r"))'
      // As the document gives the node: its settings held as a browser holds them (`1.0` is
      // `1`), and the saved form of each part as the server wrote it.
      const loadedEditor = { steps_error: null, steps_discarded: null, saved_code: saved, saved_settings: '{"rate":1.0}' }
      const withConfig = (config: Record<string, unknown>) => (nodes: Node[]) =>
        nodes.map((node) => ({ ...node, data: { ...node.data, config } }))
      const sent = (call: number) => {
        const node = mockSave.mock.calls[call][0].graph.nodes[0].data as {
          config: { code?: unknown }
          editor?: { saved_code?: unknown; saved_settings?: unknown }
        }
        return { code: node.config.code, savedCode: node.editor?.saved_code, savedSettings: node.editor?.saved_settings }
      }

      it("sends each part as saved with its saved form, and reads every edited one from the request", async () => {
        mockLoad.mockResolvedValue(makeLoadedPipeline({ nodes: [], edges: [] }))
        const { result } = renderHook(() => usePipelineAPI(makeParams()))
        await waitFor(() => expect(result.current.loading).toBe(false))
        act(() => useGraphStore.getState().loadGraphSnapshot({
          nodes: [makeNode("t", "polars", { data: { label: "t", nodeType: "polars", config: { steps, code: saved, rate: 1 }, editor: loadedEditor } })],
          edges: [],
          preamble: "",
          submodels: {},
          globalConstants: [],
        }))
        // The saved forms stay with the baseline: the canvas's copy carries none.
        expect(useGraphStore.getState().nodes[0].data.editor).toEqual({ steps_error: null, steps_discarded: null })
        // The live code is a cache: a rendering that lost the decimal, or one Undo restored.
        act(() => useGraphStore.getState().setNodesRaw(withConfig({ steps: structuredClone(steps), code: "df = stale", rate: 1 })))
        mockSave.mockResolvedValueOnce(makeSavePipelineResponse())
        await act(async () => { await result.current.handleSave() })
        expect(sent(0)).toEqual({ code: saved, savedCode: saved, savedSettings: '{"rate":1.0}' })
        expect("edited_steps" in mockSave.mock.calls[0][0]).toBe(false)

        const edited = [...steps, { id: "l", kind: "limit", n: 5 }]
        act(() => useGraphStore.getState().setNodes(withConfig({ steps: edited, code: saved, rate: 1 })))
        mockSave.mockResolvedValueOnce(makeSavePipelineResponse({
          source_revision: "revision-edited",
          saved_parts: [{ submodel: null, node_id: "t", code: "df = written", settings: null }],
        }))
        await act(async () => { await result.current.handleSave() })
        expect(sent(1)).toEqual({ code: saved, savedCode: undefined, savedSettings: '{"rate":1.0}' })

        // Saved again before the browser's own rendering arrived: the code the server wrote.
        mockSave.mockResolvedValueOnce(makeSavePipelineResponse({ source_revision: "revision-again" }))
        await act(async () => { await result.current.handleSave() })
        expect(sent(2)).toEqual({ code: "df = written", savedCode: "df = written", savedSettings: '{"rate":1.0}' })

        act(() => useGraphStore.getState().setNodes(withConfig({ steps: edited, code: saved, rate: 2 })))
        mockSave.mockResolvedValueOnce(makeSavePipelineResponse({
          source_revision: "revision-setting",
          saved_parts: [{ submodel: null, node_id: "t", code: null, settings: '{"rate":2}' }],
        }))
        await act(async () => { await result.current.handleSave() })
        expect(sent(3)).toEqual({ code: "df = written", savedCode: "df = written", savedSettings: undefined })

        mockSave.mockResolvedValueOnce(makeSavePipelineResponse({ source_revision: "revision-settled" }))
        await act(async () => { await result.current.handleSave() })
        expect(sent(4).savedSettings).toBe('{"rate":2}')
      })
    })

    it("second save failure does not corrupt lastSavedSnapshot from first success", async () => {
      // Catches: if the failure path of the second save somehow cleared
      // lastSavedSnapshot, dirty derivation would incorrectly mark the
      // graph as unsaved even though the first save persisted the data.
      mockLoad.mockResolvedValue(makeLoadedPipeline({ nodes: [], edges: [] }))

      let callIdx = 0
      mockSave.mockImplementation(() => {
        callIdx++
        if (callIdx === 1) return Promise.resolve(makeSavePipelineResponse({ file: "ok.py", pipeline_name: "p", source_revision: "revision-test", document_fingerprint: "fingerprint-saved" }))
        return Promise.reject(new Error("conflict"))
      })

      const params = makeParams()

      const { result } = renderHook(() => usePipelineAPI(params))
      await waitFor(() => expect(result.current.loading).toBe(false))
      // The canvas the saves send, after the load sets the empty graph it loaded.
      useGraphStore.setState({ nodes: [makeNode("n1")], edges: [], preamble: "" })

      await act(async () => {
        result.current.handleSave()
        result.current.handleSave()
      })

      // Wait for both promises to settle
      await waitFor(() => {
        const toasts = useToastStore.getState().toasts
        // Should have both a success and an error toast
        expect(toasts.some((t) => t.type === "success")).toBe(true)
        expect(toasts.some((t) => t.type === "error")).toBe(true)
      })

      // After the first success, lastSavedSnapshot is the graph that save sent,
      // not the loaded one. The failing second save's catch block must NOT touch it.
      expect(useGraphStore.getState().lastSavedSnapshot).not.toBeNull()
      expect(useGraphStore.getState().lastSavedSnapshot!.nodes.map((node) => node.id)).toEqual(["n1"])
    })
  })

  // ────────────────────────────────────────────────────────────────
  // 3. Preview caching integration
  // ────────────────────────────────────────────────────────────────

  describe("preview caching", () => {
    it("shows cached preview immediately without API call when cache is fresh", async () => {
      // Catches: if the cache-first logic in fetchPreviewImmediate is broken,
      // every node click would hit the API even when data is fresh, causing
      // unnecessary loading spinners and server load.
      mockLoad.mockResolvedValue(makeLoadedPipeline({ nodes: [], edges: [] }))

      const cachedData = {
        nodeId: "n1",
        nodeLabel: "Node n1",
        status: "ok" as const,
        row_count: 10,
        column_count: 2,
        columns: [{ name: "a", dtype: "f64" }],
        preview: [{ a: 1 }],
        error: null,
        timing_ms: 5,
        memory_bytes: 100,
        timings: [],
        memory: [],
        schema_warnings: [],
      }

      const params = makeParams()
      const { result } = renderHook(() => usePipelineAPI(params))
      await waitFor(() => expect(result.current.loading).toBe(false))

      // Populate after the initial load: a whole-document load is an
      // intentional cache-identity boundary and advances structuralVersion.
      const structuralVersion = useGraphStore.getState().structuralVersion
      useNodeResultsStore.setState({
        previews: {
          n1: {
            data: cachedData,
            structuralVersion,
            source: "live",
            rowLimit: 1000,
            nodeDataEpoch: useNodeDataStore.getState().epoch,
          },
        },
        columnCache: {},
      })

      const node = makeNode("n1")

      vi.useFakeTimers()

      act(() => {
        result.current.fetchPreview(node)
      })

      // fetchPreview shows cached data immediately (before debounce fires)
      expect(result.current.previewData?.nodeId).toBe("n1")
      expect(result.current.previewData?.row_count).toBe(10)

      await advanceTimers(200)

      // API should NOT have been called (cache was fresh for the same structuralVersion)
      expect(mockPreview).not.toHaveBeenCalled()
    })

    it("honours a custom preview debounce before starting the API request", async () => {
      mockLoad.mockResolvedValue(makeLoadedPipeline({ nodes: [], edges: [] }))
      mockPreview.mockResolvedValue(makePreviewResponse({
        status: "ok",
        columns: [],
        preview: [],
        row_count: 0,
        column_count: 0,
      }))

      const { result } = renderHook(() => usePipelineAPI(makeParams()))
      await waitFor(() => expect(result.current.loading).toBe(false))

      vi.useFakeTimers()
      act(() => {
        result.current.fetchPreview(makeNode("n1"), { debounceMs: 800 })
      })

      expect(result.current.previewData?.nodeId).toBe("n1")
      expect(result.current.previewData?.status).toBe("loading")

      act(() => {
        vi.advanceTimersByTime(799)
      })
      expect(mockPreview).not.toHaveBeenCalled()

      await act(async () => {
        // The async advance interleaves the debounce timer with the
        // snapshot-ensure microtask chain that now precedes the request.
        await vi.advanceTimersByTimeAsync(1)
      })
      expect(mockPreview).toHaveBeenCalledTimes(1)
    })

    it("waits 200 ms by default before starting the API request", async () => {
      mockLoad.mockResolvedValue(makeLoadedPipeline({ nodes: [], edges: [] }))
      mockPreview.mockResolvedValue(makePreviewResponse({
        status: "ok",
        columns: [],
        preview: [],
        row_count: 0,
        column_count: 0,
      }))

      const { result } = renderHook(() => usePipelineAPI(makeParams()))
      await waitFor(() => expect(result.current.loading).toBe(false))

      vi.useFakeTimers()
      act(() => {
        result.current.fetchPreview(makeNode("n1"))
      })
      act(() => {
        vi.advanceTimersByTime(199)
      })
      expect(mockPreview).not.toHaveBeenCalled()

      await act(async () => {
        await vi.advanceTimersByTimeAsync(1)
      })
      expect(mockPreview).toHaveBeenCalledTimes(1)
    })

    it("drops a waiting preview when the next selection cannot be previewed", async () => {
      mockLoad.mockResolvedValue(makeLoadedPipeline({ nodes: [], edges: [] }))

      const { result } = renderHook(() => usePipelineAPI(makeParams()))
      await waitFor(() => expect(result.current.loading).toBe(false))

      vi.useFakeTimers()
      act(() => {
        result.current.fetchPreview(makeNode("n1"))
      })
      act(() => {
        result.current.fetchPreview(makeNode("sub", NODE_TYPES.SUBMODEL))
      })
      expect(result.current.previewData).toBeNull()

      await act(async () => {
        await vi.advanceTimersByTimeAsync(1000)
      })
      expect(mockPreview).not.toHaveBeenCalled()
    })

    it("uses graph structuralVersion to decide preview freshness", async () => {
      // Catches: if fetchPreview stops checking structuralVersion, a fresh
      // structural graph change would be missed and the cached preview would
      // be treated as current.
      mockLoad.mockResolvedValue(makeLoadedPipeline({ nodes: [], edges: [] }))

      const cachedData = {
        nodeId: "n1",
        nodeLabel: "Node n1",
        status: "ok" as const,
        row_count: 10,
        column_count: 2,
        columns: [{ name: "a", dtype: "f64" }],
        preview: [{ a: 1 }],
        error: null,
        timing_ms: 5,
        memory_bytes: 100,
        timings: [],
        memory: [],
        schema_warnings: [],
      }

      useNodeResultsStore.setState({
        previews: {
          n1: {
            data: cachedData,
            structuralVersion: 3,
            source: "live",
            rowLimit: 1000,
            nodeDataEpoch: useNodeDataStore.getState().epoch,
          },
        },
        columnCache: {},
      })
      useGraphStore.setState({ structuralVersion: 4 })
      mockPreview.mockResolvedValue(makePreviewResponse({
        status: "ok",
        columns: [{ name: "b", dtype: "i64" }],
        preview: [{ b: 2 }],
        row_count: 20,
        column_count: 1,
      }))

      const params = makeParams()
      const { result } = renderHook(() => usePipelineAPI(params))
      await waitFor(() => expect(result.current.loading).toBe(false))

      const node = makeNode("n1")

      act(() => {
        result.current.fetchPreview(node)
      })

      await waitFor(() => {
        expect(mockPreview).toHaveBeenCalled()
      }, { timeout: 2000 })
    })

    it("fetches from API when cache exists but structuralVersion is stale", async () => {
      // Catches: if the structuralVersion check is removed, the cache would
      // always be considered fresh, showing stale data after the user
      // modifies a node's config.
      mockLoad.mockResolvedValue(makeLoadedPipeline({ nodes: [], edges: [] }))
      mockPreview.mockResolvedValue(makePreviewResponse({
        status: "ok",
        columns: [{ name: "b", dtype: "i64" }],
        preview: [{ b: 2 }],
        row_count: 20,
        column_count: 1,
      }))

      const cachedData = {
        nodeId: "n1",
        nodeLabel: "Node n1",
        status: "ok" as const,
        row_count: 10,
        column_count: 2,
        columns: [],
        preview: [],
        error: null,
        timing_ms: 0,
        memory_bytes: 0,
        timings: [],
        memory: [],
        schema_warnings: [],
      }

      // Cache at version 0, but graph store is at version 5 (stale)
      useNodeResultsStore.setState({
        previews: {
          n1: {
            data: cachedData,
            structuralVersion: 0,
            source: "live",
            rowLimit: 1000,
            nodeDataEpoch: useNodeDataStore.getState().epoch,
          },
        },
        columnCache: {},
      })
      useGraphStore.setState({ structuralVersion: 5 })

      const params = makeParams()
      const { result } = renderHook(() => usePipelineAPI(params))
      await waitFor(() => expect(result.current.loading).toBe(false))

      const node = makeNode("n1")

      act(() => {
        result.current.fetchPreview(node)
      })

      // API should be called since cache is stale (after debounce)
      await waitFor(() => {
        expect(mockPreview).toHaveBeenCalled()
      }, { timeout: 2000 })
    })

    it("refetches when cached preview source differs from active source", async () => {
      mockLoad.mockResolvedValue(makeLoadedPipeline({ nodes: [], edges: [] }))
      mockPreview.mockResolvedValue(makePreviewResponse({
        status: "ok",
        columns: [],
        preview: [],
        row_count: 0,
        column_count: 0,
      }))

      const cachedData = {
        nodeId: "n1",
        nodeLabel: "Node n1",
        status: "ok" as const,
        row_count: 10,
        column_count: 1,
        columns: [],
        preview: [],
        error: null,
        timing_ms: 0,
        memory_bytes: 0,
        timings: [],
        memory: [],
        schema_warnings: [],
      }

      useNodeResultsStore.setState({
        previews: {
          n1: {
            data: cachedData,
            structuralVersion: 0,
            source: "backtest",
            rowLimit: 1000,
            nodeDataEpoch: useNodeDataStore.getState().epoch,
          },
        },
        columnCache: {},
      })

      const { result } = renderHook(() => usePipelineAPI(makeParams()))
      await waitFor(() => expect(result.current.loading).toBe(false))

      act(() => {
        result.current.fetchPreview(makeNode("n1"), { debounceMs: 0 })
      })

      await waitFor(() => {
        expect(mockPreview).toHaveBeenCalled()
      }, { timeout: 2000 })
    })

    it("refetches when cached preview row limit differs from current row limit", async () => {
      mockLoad.mockResolvedValue(makeLoadedPipeline({ nodes: [], edges: [] }))
      mockPreview.mockResolvedValue(makePreviewResponse({
        status: "ok",
        columns: [],
        preview: [],
        row_count: 0,
        column_count: 0,
      }))

      const cachedData = {
        nodeId: "n1",
        nodeLabel: "Node n1",
        status: "ok" as const,
        row_count: 10,
        column_count: 1,
        columns: [],
        preview: [],
        error: null,
        timing_ms: 0,
        memory_bytes: 0,
        timings: [],
        memory: [],
        schema_warnings: [],
      }

      useSettingsStore.setState({ rowLimit: 250 })
      useNodeResultsStore.setState({
        previews: {
          n1: {
            data: cachedData,
            structuralVersion: 0,
            source: "live",
            rowLimit: 1000,
            nodeDataEpoch: useNodeDataStore.getState().epoch,
          },
        },
        columnCache: {},
      })

      const { result } = renderHook(() => usePipelineAPI(makeParams()))
      await waitFor(() => expect(result.current.loading).toBe(false))

      act(() => {
        result.current.fetchPreview(makeNode("n1"), { debounceMs: 0 })
      })

      await waitFor(() => {
        expect(mockPreview).toHaveBeenCalled()
      }, { timeout: 2000 })
    })

    it("terminalizes in-flight preview results after graph execution context changes without applying graph effects", async () => {
      // W0 contract: a structuralVersion bump while the response is in
      // flight must not strand the panel on "loading" — the response still
      // renders as the panel's terminal state. Graph-coupled effects stay
      // version-gated: stale columns are never written into the changed
      // graph (no setNodes/cascade), and no cache entry is created for a
      // node that is absent from the live graph.
      mockLoad.mockResolvedValue(makeLoadedPipeline({ nodes: [], edges: [] }))
      let resolvePreview!: (value: PreviewNodeResponse) => void
      mockPreview.mockReturnValue(new Promise((resolve) => {
        resolvePreview = resolve
      }))

      const params = makeParams()
      const { result } = renderHook(() => usePipelineAPI(params))
      await waitFor(() => expect(result.current.loading).toBe(false))

      act(() => {
        result.current.fetchPreview(makeNode("n1"))
      })
      await waitFor(() => expect(mockPreview).toHaveBeenCalled(), { timeout: 2000 })

      act(() => {
        useGraphStore.getState().setPreambleRaw("import polars as pl")
      })

      await act(async () => {
        resolvePreview(makePreviewResponse({
          status: "ok",
          columns: [{ name: "stale", dtype: "i64" }],
          preview: [{ stale: 1 }],
          row_count: 1,
          column_count: 1,
        }))
        await Promise.resolve()
      })

      expect(result.current.previewData?.status).toBe("ok")
      expect(result.current.previewData?.row_count).toBe(1)
      expect(result.current.previewBusy).toBe(false)
      expect(params.setNodes).not.toHaveBeenCalled()
      expect(useNodeResultsStore.getState().previews.n1).toBeUndefined()
    })
  })

  // ────────────────────────────────────────────────────────────────
  // 4. fetchPreview abort
  // ────────────────────────────────────────────────────────────────

  describe("fetchPreview abort on new request", () => {
    it("cancelPreview clears a pending debounced preview before the API request starts", async () => {
      mockLoad.mockResolvedValue(makeLoadedPipeline({ nodes: [], edges: [] }))
      mockPreview.mockResolvedValue(makePreviewResponse({
        status: "ok",
        columns: [],
        preview: [],
        row_count: 0,
        column_count: 0,
      }))

      const { result } = renderHook(() => usePipelineAPI(makeParams()))
      await waitFor(() => expect(result.current.loading).toBe(false))

      vi.useFakeTimers()
      act(() => {
        result.current.fetchPreview(makeNode("n1"), { debounceMs: 800 })
        result.current.cancelPreview()
        vi.advanceTimersByTime(800)
      })

      expect(mockPreview).not.toHaveBeenCalled()
    })

    it("aborts previous in-flight request when a new fetchPreview is called", async () => {
      // Catches: without abort, the response from a slow first request
      // could overwrite the fresher second request's data, showing the
      // wrong node's preview.
      mockLoad.mockResolvedValue(makeLoadedPipeline({ nodes: [], edges: [] }))

      const abortSignals: AbortSignal[] = []
      mockPreview.mockImplementation((args: { signal?: AbortSignal }) => {
        if (args.signal) abortSignals.push(args.signal)
        return new Promise(() => {}) // never resolves
      })

      const params = makeParams()
      const { result } = renderHook(() => usePipelineAPI(params))
      await waitFor(() => expect(result.current.loading).toBe(false))

      // First preview — triggers debounce
      act(() => {
        result.current.fetchPreview(makeNode("n1"), { debounceMs: 0 })
      })

      // Wait for debounce to fire the first fetchPreviewImmediate
      await waitFor(() => expect(abortSignals.length).toBe(1))

      // Second preview — should abort the first
      act(() => {
        result.current.fetchPreview(makeNode("n2"), { debounceMs: 0 })
      })

      // Wait for second debounce to fire
      expect(abortSignals[0].aborted).toBe(true)
      await waitFor(() => expect(abortSignals.length).toBeGreaterThanOrEqual(2))

      // The first signal should have been aborted
      expect(abortSignals.length).toBeGreaterThanOrEqual(1)
      expect(abortSignals[0].aborted).toBe(true)
    })
  })
})
