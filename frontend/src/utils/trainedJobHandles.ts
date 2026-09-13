/**
 * Browser-persisted handles to a node's last completed training job.
 *
 * Training results live in memory, so a reload would otherwise forget a model
 * that is still exportable on the server. Each document (pipeline source file)
 * keeps, per modelling node, the job ID, the staleness identity the result was
 * trained under (config hash and source) and a digest of the graph payload the
 * training request submitted. The results store writes a handle when a training job
 * completes — whether or not the node's editor is open — and forgets it when
 * training fails; the modelling editor restores the result through the job
 * status endpoint and reports honestly when the server no longer has it.
 * Storage can be unavailable (private windows, blocked site data), so every
 * access tolerates failure and the editor works without it.
 */
import {
  MODELLING_NODE_TYPE,
  trainingIdentityConfig,
} from "./modellingExportConfig"

const STORAGE_KEY = "haute.modelling.trainedJobs.v2"

export interface TrainedJobHandle {
  jobId: string
  configHash: string
  source: string
  /** `trainingLineage` of the graph payload the training request submitted. */
  lineage: string
}

type HandleStore = Record<string, Record<string, TrainedJobHandle>>

function isHandle(value: unknown): value is TrainedJobHandle {
  if (typeof value !== "object" || value === null) return false
  const record = value as Record<string, unknown>
  return (
    typeof record.jobId === "string" &&
    record.jobId !== "" &&
    typeof record.configHash === "string" &&
    typeof record.source === "string" &&
    typeof record.lineage === "string" &&
    record.lineage !== ""
  )
}

function readStore(): HandleStore {
  try {
    const raw = globalThis.localStorage?.getItem(STORAGE_KEY)
    const parsed: unknown = raw ? JSON.parse(raw) : {}
    return typeof parsed === "object" && parsed !== null ? (parsed as HandleStore) : {}
  } catch {
    return {}
  }
}

function writeStore(store: HandleStore): void {
  try {
    globalThis.localStorage?.setItem(STORAGE_KEY, JSON.stringify(store))
  } catch {
    // Persistence is a convenience: without it a reload simply forgets the result.
  }
}

export function readTrainedJobHandle(sourceFile: string, nodeId: string): TrainedJobHandle | null {
  const handle = readStore()[sourceFile]?.[nodeId]
  return isHandle(handle) ? handle : null
}

export function writeTrainedJobHandle(
  sourceFile: string,
  nodeId: string,
  handle: TrainedJobHandle,
): void {
  const store = readStore()
  const existing = store[sourceFile]?.[nodeId]
  if (
    isHandle(existing) &&
    existing.jobId === handle.jobId &&
    existing.configHash === handle.configHash &&
    existing.source === handle.source &&
    existing.lineage === handle.lineage
  ) {
    return
  }
  writeStore({ ...store, [sourceFile]: { ...(store[sourceFile] ?? {}), [nodeId]: handle } })
}

export function clearTrainedJobHandle(sourceFile: string, nodeId: string): void {
  const store = readStore()
  const document = store[sourceFile]
  if (!document || !(nodeId in document)) return
  const { [nodeId]: _removed, ...rest } = document
  void _removed
  writeStore({ ...store, [sourceFile]: rest })
}

type LineageNode = { id: string; data?: unknown }
type LineageEdge = {
  source: string
  sourceHandle?: string | null
  target: string
  targetHandle?: string | null
}

export type TrainingLineageInput = {
  nodes?: readonly LineageNode[]
  edges?: readonly LineageEdge[]
  preamble?: string
  submodels?: Record<string, unknown>
}

/** Runtime-only config keys the canvas injects; they never describe the pipeline. */
const RUNTIME_CONFIG_KEYS = ["_nodeId", "_columns", "_schemaWarnings", "_availableColumns"]

const text = (value: unknown): string => (typeof value === "string" ? value : "")

const isRecord = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === "object" && !Array.isArray(value)

function canonical(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonical)
  if (value !== null && typeof value === "object") {
    const record = value as Record<string, unknown>
    return Object.fromEntries(
      Object.keys(record)
        .sort()
        .filter((key) => record[key] !== undefined)
        .map((key) => [key, canonical(record[key])]),
    )
  }
  return value
}

function nodeConfig(data: Record<string, unknown>): Record<string, unknown> {
  const raw = data.config
  if (raw === null || typeof raw !== "object" || Array.isArray(raw)) return {}
  const config = { ...(raw as Record<string, unknown>) }
  for (const key of RUNTIME_CONFIG_KEYS) delete config[key]
  return data.nodeType === MODELLING_NODE_TYPE ? trainingIdentityConfig(config) : config
}

/** 53-bit string digest (cyrb53): a compact, stable key for a long canonical text. */
function digest(value: string): string {
  let h1 = 0xdeadbeef
  let h2 = 0x41c6ce57
  for (let index = 0; index < value.length; index += 1) {
    const code = value.charCodeAt(index)
    h1 = Math.imul(h1 ^ code, 2654435761)
    h2 = Math.imul(h2 ^ code, 1597334677)
  }
  h1 = Math.imul(h1 ^ (h1 >>> 16), 2246822507)
  h1 ^= Math.imul(h2 ^ (h2 >>> 13), 3266489909)
  h2 = Math.imul(h2 ^ (h2 >>> 16), 2246822507)
  h2 ^= Math.imul(h1 ^ (h1 >>> 13), 3266489909)
  return `${value.length.toString(36)}-${(4294967296 * (2097151 & h2) + (h1 >>> 0)).toString(36)}`
}

function lineageMaterial(graph: TrainingLineageInput): unknown {
  const nodes = (graph.nodes ?? [])
    .map((node) => {
      const data =
        node.data !== null && typeof node.data === "object"
          ? (node.data as Record<string, unknown>)
          : {}
      return [
        node.id,
        text(data.nodeType),
        text(data.label),
        text(data.description),
        text(data.code),
        text(data.func_name),
        canonical(nodeConfig(data)),
      ] as const
    })
    .sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0))
  const edges = (graph.edges ?? [])
    .map((edge) => `${edge.source}:${edge.sourceHandle ?? ""}->${edge.target}:${edge.targetHandle ?? ""}`)
    .sort()
  const submodels = Object.keys(graph.submodels ?? {})
    .sort()
    .map((name) => [name, submodelMaterial(graph.submodels?.[name])])
  return { nodes, edges, preamble: graph.preamble ?? "", submodels }
}

/** A submodel's interface and file, plus its own graph by the same rules. */
function submodelMaterial(definition: unknown): unknown {
  if (!isRecord(definition)) return canonical(definition)
  const { graph, ...interfaceFields } = definition
  return {
    definition: canonical(interfaceFields),
    graph: isRecord(graph) ? lineageMaterial(graph as TrainingLineageInput) : canonical(graph),
  }
}

/**
 * A durable digest of what a training result depends on in the graph.
 *
 * The in-session staleness check compares a counter that restarts on reload,
 * so a restored result is checked against this instead. It covers every node's
 * type, label, description, code, function name and config (keys sorted,
 * runtime keys and modelling export settings omitted), every edge, the
 * preamble, and every submodel definition — its interface, file and internal
 * graph by the same rules. Positions, selection and preview results never
 * contribute.
 */
export function trainingLineage(graph: TrainingLineageInput): string {
  return digest(JSON.stringify(lineageMaterial(graph)))
}
