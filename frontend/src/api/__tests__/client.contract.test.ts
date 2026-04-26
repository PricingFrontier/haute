import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { loadUiContractFixture } from "../../testSupport/uiContractFixtures"
import {
  applyOptimiser,
  checkMlflow,
  createGitBranch,
  createSubmodel,
  createUtilityFile,
  deleteUtilityFile,
  dissolveSubmodel,
  buildJsonCache,
  estimateOptimiserSolve,
  estimateTrainingRam,
  fetchDatabricksData,
  fetchSchema,
  getGitHistory,
  getGitStatus,
  getOptimiserStatus,
  getTrainStatus,
  gitArchiveBranch,
  gitDeleteBranch,
  gitPull,
  gitRevert,
  gitSave,
  gitSubmit,
  listUtilityFiles,
  loadSubmodel,
  logOptimiserToMlflow,
  logToMlflow,
  previewNode,
  readUtilityFile,
  runFrontier,
  savePipeline,
  saveOptimiser,
  selectFrontierPoint,
  solveOptimiser,
  switchGitBranch,
  traceCell,
  trainModel,
  updateUtilityFile,
} from "../client"

let mockFetch: ReturnType<typeof vi.fn>

function jsonResponse(body: unknown, status = 200) {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    statusText: status === 200 ? "OK" : "Error",
    json: () => Promise.resolve(body),
  })
}

const dummyGraph = {
  nodes: [{ id: "n1", type: "custom", position: { x: 0, y: 0 }, data: {} }],
  edges: [],
}

beforeEach(() => {
  mockFetch = vi.fn()
  globalThis.fetch = mockFetch as unknown as typeof fetch
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe("client runtime contracts", () => {
  it("savePipeline rejects malformed 200 payloads", async () => {
    mockFetch.mockReturnValue(jsonResponse({ file: 123, pipeline_name: "pricing" }))

    await expect(
      savePipeline({
        name: "pricing",
        description: "",
        graph: dummyGraph,
        preamble: "",
        source_file: "pricing.py",
      }),
    ).rejects.toThrow(/parseSavePipelineResponse/i)
  })

  it("previewNode rejects malformed preview payloads", async () => {
    mockFetch.mockReturnValue(jsonResponse({ status: "ok", node_id: 42 }))

    await expect(previewNode(dummyGraph, "n1", 10)).rejects.toThrow(/parsePreviewNodeResponse/i)
  })

  it("traceCell rejects malformed trace payloads", async () => {
    mockFetch.mockReturnValue(jsonResponse({ status: "ok", trace: { row_index: 0 } }))

    await expect(
      traceCell({ graph: dummyGraph, row_index: 0, target_node_id: "n1" }),
    ).rejects.toThrow(/parseTraceResponse/i)
  })

  it("fetchSchema rejects malformed schema payloads", async () => {
    mockFetch.mockReturnValue(jsonResponse({ path: "x.parquet", columns: "bad" }))

    await expect(fetchSchema("x.parquet")).rejects.toThrow(/parseSchemaResponse/i)
  })

  it("trainModel accepts the tightened train contract", async () => {
    mockFetch.mockReturnValue(jsonResponse(loadUiContractFixture("train_started_response")))

    const result = await trainModel({ graph: dummyGraph, node_id: "model1" })

    expect(result.status).toBe("started")
    expect(result.job_id).toBe("job-1")
  })

  it("getTrainStatus rejects malformed nested train results", async () => {
    const fixture = loadUiContractFixture<Record<string, unknown>>("train_status_response")
    const result = fixture.result as Record<string, unknown>

    mockFetch.mockReturnValue(
      jsonResponse({
        ...fixture,
        result: {
          ...result,
          glm_coefficients: [{ feature: "x", coefficient: "bad" }],
        },
      }),
    )

    await expect(getTrainStatus("job-1")).rejects.toThrow(/parseTrainResponse/i)
  })

  it("getOptimiserStatus rejects malformed optimiser result payloads", async () => {
    const fixture = loadUiContractFixture<Record<string, unknown>>("optimiser_status_response")
    const result = fixture.result as Record<string, unknown>

    mockFetch.mockReturnValue(
      jsonResponse({
        ...fixture,
        result: {
          ...result,
          lambdas: { loss: "bad" },
        },
      }),
    )

    await expect(getOptimiserStatus("job-1")).rejects.toThrow(/parseOptimiserStatusResponse/i)
  })

  it("getGitStatus rejects malformed git payloads", async () => {
    const fixture = loadUiContractFixture<Record<string, unknown>>("git_status_response")
    mockFetch.mockReturnValue(jsonResponse({ ...fixture, is_read_only: undefined }))

    await expect(getGitStatus()).rejects.toThrow(/parseGitStatusResponse/i)
  })

  it("buildJsonCache rejects incomplete cache-build payloads", async () => {
    const fixture = loadUiContractFixture<Record<string, unknown>>("json_cache_build_response")
    mockFetch.mockReturnValue(jsonResponse({ ...fixture, data_path: undefined }))

    await expect(buildJsonCache({ path: "/data/input.json" })).rejects.toThrow(/parseJsonCacheBuildResponse/i)
  })

  it("fetchDatabricksData rejects malformed fetch payloads", async () => {
    const fixture = loadUiContractFixture<Record<string, unknown>>("fetch_table_response")
    mockFetch.mockReturnValue(jsonResponse({ ...fixture, column_count: undefined }))

    await expect(fetchDatabricksData({ table: "cat.sch.tbl" })).rejects.toThrow(/parseFetchTableResponse/i)
  })
})

describe("next-wave client runtime contracts", () => {
  const malformedCases: Array<{
    name: string
    response: Record<string, unknown>
    call: () => Promise<unknown>
    error: RegExp
  }> = [
    {
      name: "createSubmodel",
      response: { ...loadUiContractFixture<Record<string, unknown>>("submodel_create_response"), graph: { edges: [] } },
      call: () => createSubmodel({
        name: "pricing",
        node_ids: ["n1"],
        graph: dummyGraph,
        preamble: "",
        source_file: "main.py",
        pipeline_name: "main",
      }),
      error: /parseSubmodelCreateResponse/i,
    },
    {
      name: "loadSubmodel",
      response: { ...loadUiContractFixture<Record<string, unknown>>("submodel_graph_response"), submodel_name: 42 },
      call: () => loadSubmodel("pricing"),
      error: /parseSubmodelGraphResponse/i,
    },
    {
      name: "dissolveSubmodel",
      response: { ...loadUiContractFixture<Record<string, unknown>>("dissolve_submodel_response"), graph: { nodes: "bad", edges: [] } },
      call: () => dissolveSubmodel({
        submodel_name: "pricing",
        graph: dummyGraph,
        preamble: "",
        source_file: "main.py",
        pipeline_name: "main",
      }),
      error: /parseDissolveSubmodelResponse/i,
    },
    {
      name: "checkMlflow",
      response: { ...loadUiContractFixture<Record<string, unknown>>("mlflow_check_response"), mlflow_installed: "yes" },
      call: () => checkMlflow(),
      error: /parseMlflowCheckResponse/i,
    },
    {
      name: "estimateTrainingRam",
      response: { ...loadUiContractFixture<Record<string, unknown>>("train_estimate_response"), estimated_mb: "bad" },
      call: () => estimateTrainingRam({ graph: dummyGraph, node_id: "model1" }),
      error: /parseTrainEstimateResponse/i,
    },
    {
      name: "logToMlflow",
      response: { ...loadUiContractFixture<Record<string, unknown>>("mlflow_log_response"), run_id: 42 },
      call: () => logToMlflow({ job_id: "job-1" }),
      error: /parseMlflowLogResponse/i,
    },
    {
      name: "solveOptimiser",
      response: { ...loadUiContractFixture<Record<string, unknown>>("solve_optimiser_response"), job_id: 42 },
      call: () => solveOptimiser({ graph: dummyGraph, node_id: "opt1" }),
      error: /parseSolveOptimiserResponse/i,
    },
    {
      name: "estimateOptimiserSolve",
      response: { ...loadUiContractFixture<Record<string, unknown>>("optimiser_estimate_response"), total_rows: "bad" },
      call: () => estimateOptimiserSolve({ graph: dummyGraph, node_id: "opt1" }),
      error: /parseOptimiserEstimateResponse/i,
    },
    {
      name: "applyOptimiser",
      response: { ...loadUiContractFixture<Record<string, unknown>>("optimiser_apply_response"), constraints: { loss: "bad" } },
      call: () => applyOptimiser({ job_id: "opt-job-1" }),
      error: /parseApplyOptimiserResponse/i,
    },
    {
      name: "saveOptimiser",
      response: { ...loadUiContractFixture<Record<string, unknown>>("optimiser_save_response"), message: 42 },
      call: () => saveOptimiser({ job_id: "opt-job-1", output_path: "output.py" }),
      error: /parseSaveOptimiserResponse/i,
    },
    {
      name: "logOptimiserToMlflow",
      response: { ...loadUiContractFixture<Record<string, unknown>>("mlflow_log_response"), tracking_uri: 42 },
      call: () => logOptimiserToMlflow({ job_id: "opt-job-1" }),
      error: /parseMlflowLogResponse/i,
    },
    {
      name: "runFrontier",
      response: { ...loadUiContractFixture<Record<string, unknown>>("optimiser_frontier_response"), constraint_names: "bad" },
      call: () => runFrontier({ job_id: "opt-job-1", threshold_ranges: { loss: [0.8, 1.0] } }),
      error: /constraint_names/i,
    },
    {
      name: "selectFrontierPoint",
      response: { ...loadUiContractFixture<Record<string, unknown>>("optimiser_frontier_select_response"), lambdas: { loss: "bad" } },
      call: () => selectFrontierPoint({ job_id: "opt-job-1", point_index: 0 }),
      error: /parseFrontierSelectResponse/i,
    },
    {
      name: "listUtilityFiles",
      response: { ...loadUiContractFixture<Record<string, unknown>>("utility_list_response"), files: "bad" },
      call: () => listUtilityFiles(),
      error: /parseUtilityListResponse/i,
    },
    {
      name: "readUtilityFile",
      response: { ...loadUiContractFixture<Record<string, unknown>>("utility_read_response"), content: 42 },
      call: () => readUtilityFile("helpers"),
      error: /parseUtilityReadResponse/i,
    },
    {
      name: "createUtilityFile",
      response: { ...loadUiContractFixture<Record<string, unknown>>("utility_write_response"), import_line: 42 },
      call: () => createUtilityFile({ name: "helpers", content: "" }),
      error: /parseUtilityWriteResponse/i,
    },
    {
      name: "updateUtilityFile",
      response: { ...loadUiContractFixture<Record<string, unknown>>("utility_write_response"), error_line: "bad" },
      call: () => updateUtilityFile("helpers", "print('x')"),
      error: /parseUtilityWriteResponse/i,
    },
    {
      name: "deleteUtilityFile",
      response: { ...loadUiContractFixture<Record<string, unknown>>("utility_delete_response"), module: 42 },
      call: () => deleteUtilityFile("helpers"),
      error: /parseUtilityDeleteResponse/i,
    },
    {
      name: "createGitBranch",
      response: { ...loadUiContractFixture<Record<string, unknown>>("git_create_branch_response"), branch: 42 },
      call: () => createGitBranch("new feature branch"),
      error: /parseGitCreateBranchResponse/i,
    },
    {
      name: "switchGitBranch",
      response: { ...loadUiContractFixture<Record<string, unknown>>("git_switch_branch_response"), branch: 42 },
      call: () => switchGitBranch("feat/pricing-improvements"),
      error: /parseGitSwitchBranchResponse/i,
    },
    {
      name: "gitSave",
      response: { ...loadUiContractFixture<Record<string, unknown>>("git_save_response"), timestamp: 42 },
      call: () => gitSave(),
      error: /parseGitSaveResponse/i,
    },
    {
      name: "gitSubmit",
      response: { ...loadUiContractFixture<Record<string, unknown>>("git_submit_response"), compare_url: 42 },
      call: () => gitSubmit(),
      error: /parseGitSubmitResponse/i,
    },
    {
      name: "getGitHistory",
      response: {
        ...loadUiContractFixture<Record<string, unknown>>("git_history_response"),
        entries: [
          {
            ...(loadUiContractFixture<Record<string, unknown>>("git_history_response").entries as Array<Record<string, unknown>>)[0],
            files_changed: "bad",
          },
        ],
      },
      call: () => getGitHistory(),
      error: /parseGitHistoryResponse/i,
    },
    {
      name: "gitRevert",
      response: { ...loadUiContractFixture<Record<string, unknown>>("git_revert_response"), backup_tag: 42 },
      call: () => gitRevert("abc123def456"),
      error: /parseGitRevertResponse/i,
    },
    {
      name: "gitPull",
      response: { ...loadUiContractFixture<Record<string, unknown>>("git_pull_response"), success: "yes" },
      call: () => gitPull(),
      error: /parseGitPullResponse/i,
    },
    {
      name: "gitArchiveBranch",
      response: { ...loadUiContractFixture<Record<string, unknown>>("git_archive_response"), archived_as: 42 },
      call: () => gitArchiveBranch("feat/pricing-improvements"),
      error: /parseGitArchiveResponse/i,
    },
    {
      name: "gitDeleteBranch",
      response: { ...loadUiContractFixture<Record<string, unknown>>("git_delete_branch_response"), branch: 42 },
      call: () => gitDeleteBranch("feat/pricing-improvements"),
      error: /parseGitDeleteBranchResponse/i,
    },
  ]

  for (const testCase of malformedCases) {
    it(`${testCase.name} rejects malformed 200 payloads`, async () => {
      mockFetch.mockReturnValue(jsonResponse(testCase.response))

      await expect(testCase.call()).rejects.toThrow(testCase.error)
    })
  }
})
