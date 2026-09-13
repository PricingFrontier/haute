import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import {
  clearTrainedJobHandle,
  readTrainedJobHandle,
  trainingLineage,
  writeTrainedJobHandle,
} from "../trainedJobHandles"

const HANDLE = { jobId: "job_1", configHash: "hash", source: "live", lineage: "lineage" }

describe("trained job handles", () => {
  beforeEach(() => localStorage.clear())
  afterEach(() => vi.restoreAllMocks())

  it("keeps one handle per node within each document", () => {
    writeTrainedJobHandle("a.py", "node_1", HANDLE)
    writeTrainedJobHandle("a.py", "node_2", { ...HANDLE, jobId: "job_2" })
    writeTrainedJobHandle("b.py", "node_1", { ...HANDLE, jobId: "job_3" })

    expect(readTrainedJobHandle("a.py", "node_1")).toEqual(HANDLE)
    expect(readTrainedJobHandle("a.py", "node_2")?.jobId).toBe("job_2")
    expect(readTrainedJobHandle("b.py", "node_1")?.jobId).toBe("job_3")
    expect(readTrainedJobHandle("c.py", "node_1")).toBeNull()
  })

  it("forgets one node without touching the others", () => {
    writeTrainedJobHandle("a.py", "node_1", HANDLE)
    writeTrainedJobHandle("a.py", "node_2", HANDLE)
    clearTrainedJobHandle("a.py", "node_1")
    expect(readTrainedJobHandle("a.py", "node_1")).toBeNull()
    expect(readTrainedJobHandle("a.py", "node_2")).toEqual(HANDLE)
  })

  it("ignores malformed stored data, including a handle without a lineage", () => {
    const key = "haute.modelling.trainedJobs.v2"
    localStorage.setItem(key, JSON.stringify({ "a.py": { node_1: { jobId: 3 } } }))
    expect(readTrainedJobHandle("a.py", "node_1")).toBeNull()
    const { lineage: _lineage, ...withoutLineage } = HANDLE
    void _lineage
    localStorage.setItem(key, JSON.stringify({ "a.py": { node_1: withoutLineage } }))
    expect(readTrainedJobHandle("a.py", "node_1")).toBeNull()
    localStorage.setItem(key, "{not json")
    expect(readTrainedJobHandle("a.py", "node_1")).toBeNull()
  })

  it("works without storage", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked")
    })
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked")
    })
    expect(() => writeTrainedJobHandle("a.py", "node_1", HANDLE)).not.toThrow()
    expect(readTrainedJobHandle("a.py", "node_1")).toBeNull()
    expect(() => clearTrainedJobHandle("a.py", "node_1")).not.toThrow()
  })
})

describe("trainingLineage", () => {
  const node = (id: string, data: Record<string, unknown>) => ({
    id,
    position: { x: 0, y: 0 },
    data: { label: id, nodeType: "polars", ...data },
  })
  const sourceConfig = { path: "a.parquet", columns: ["x"] }
  const graph = {
    nodes: [
      node("source", { code: "df", config: sourceConfig }),
      node("model", {
        nodeType: "modelling",
        config: { target: "y", params: { depth: 6, iterations: 100 } },
      }),
    ],
    edges: [{ source: "source", target: "model", sourceHandle: null }],
    preamble: "import polars as pl",
  }

  it("ignores key order, positions, runtime config keys and modelling export settings", () => {
    const reordered = {
      ...graph,
      nodes: [
        {
          ...graph.nodes[1],
          data: {
            ...graph.nodes[1].data,
            config: {
              params: { iterations: 100, depth: 6 },
              _nodeId: "model",
              target: "y",
              mlflow_experiment: "pricing",
              mlflow_destination: "databricks",
              model_export_path: "models/frequency.cbm",
            },
          },
        },
        { ...graph.nodes[0], position: { x: 300, y: 40 }, selected: true },
      ],
    }
    expect(trainingLineage(reordered)).toBe(trainingLineage(graph))
  })

  const withSubmodel = (code: string, position = { x: 0, y: 0 }) => ({
    ...graph,
    submodels: {
      features: {
        definitionId: "features",
        file: "submodels/features.py",
        inputPorts: [{ name: "policies", targets: [{ nodeId: "clean", handleId: null }] }],
        outputPorts: [{ name: "clean", source: { nodeId: "clean", handleId: null } }],
        graph: {
          nodes: [{ id: "clean", position, data: { label: "clean", nodeType: "polars", code } }],
          edges: [],
        },
      },
    },
  })

  it("changes when a submodel's internal transform changes, but not when it only moves", () => {
    const original = trainingLineage(withSubmodel("df"))
    expect(trainingLineage(withSubmodel("df", { x: 500, y: 90 }))).toBe(original)
    expect(trainingLineage(withSubmodel("df.drop_nulls()"))).not.toBe(original)
    expect(original).not.toBe(trainingLineage(graph))
  })

  it.each([
    ["an upstream transform's code", (g: typeof graph) => ({ ...g, nodes: [node("source", { code: "df.head(5)", config: sourceConfig }), g.nodes[1]] })],
    ["a training setting", (g: typeof graph) => ({ ...g, nodes: [g.nodes[0], { ...g.nodes[1], data: { ...g.nodes[1].data, config: { target: "claims", params: { depth: 6, iterations: 100 } } } }] })],
    ["the wiring", (g: typeof graph) => ({ ...g, edges: [] })],
    ["the preamble", (g: typeof graph) => ({ ...g, preamble: "import numpy as np" })],
  ])("changes when %s changes", (_what, change) => {
    expect(trainingLineage(change(graph))).not.toBe(trainingLineage(graph))
  })
})
