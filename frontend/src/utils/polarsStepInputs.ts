/**
 * Input references inside a stepped transform's `config.steps`
 * (`source.input`, `join.input`, `concat.inputs`). Topology rewrites that
 * rename an incoming edge (node renames, Edge Join insertion) rewrite these
 * references in place instead of recording an `inputMapping`, which a stepped
 * original transform never carries.
 */

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}

/**
 * Where `df` comes from when a step list starts: `input` means the first step
 * chooses an input (a Transform); `frame` means the surface hands the steps a
 * frame already bound to `df` (a Data Input's opened snapshot).
 */
export type StepStart = "input" | "frame"

/** How one node type authors steps; the browser mirror of `haute._polars_steps.STEPPED_NODE_TYPES`. */
export type SteppedSurface = {
  start: StepStart
  /** What join/concat steps may reference: the incoming edge names, or nothing. */
  inputs: "edges" | "none"
}

// Held equal to the backend table by tests/test_polars_steps_catalogue.py.
export const STEPPED_NODE_TYPES: Readonly<Record<string, SteppedSurface>> = {
  polars: { start: "input", inputs: "edges" },
  dataInput: { start: "frame", inputs: "none" },
  externalFile: { start: "frame", inputs: "edges" },
  ratingStep: { start: "frame", inputs: "none" },
  modelScore: { start: "frame", inputs: "none" },
  scenarioExpander: { start: "frame", inputs: "none" },
  explore: { start: "frame", inputs: "none" },
}

/** The stepped surface of a node type, or undefined for a type that does not author steps. */
export function steppedSurfaceFor(nodeType: string): SteppedSurface | undefined {
  return Object.hasOwn(STEPPED_NODE_TYPES, nodeType) ? STEPPED_NODE_TYPES[nodeType] : undefined
}

/**
 * Whether a stepped node type's steps may name its incoming edges. Only such
 * a surface needs its step references rewritten when an input is renamed.
 */
export function steppedSurfaceAllowsInputReferences(nodeType: string): boolean {
  return steppedSurfaceFor(nodeType)?.inputs === "edges"
}

/**
 * The input names a stepped node's steps may reference: the edge names for an
 * `edges` surface, none for a surface whose code sees only `df`. Every render
 * request takes its names from here, never from the input chips on display.
 */
export function stepInputNames(nodeType: string, edgeNames: readonly string[]): string[] {
  const surface = steppedSurfaceFor(nodeType)
  if (surface === undefined) throw new Error(`Node type ${JSON.stringify(nodeType)} does not author steps.`)
  return surface.inputs === "edges" ? [...edgeNames] : []
}

/** Whether `config` is authored as steps on a node type that supports them. */
export function isSteppedConfig(nodeType: string, config: unknown): boolean {
  return steppedSurfaceFor(nodeType) !== undefined && isRecord(config) && Array.isArray(config.steps)
}

/** Authored transform settings, excluding the caches materialised from steps. */
export function authoredPolarsConfig(config: Record<string, unknown>): Record<string, unknown> {
  if (!Array.isArray(config.steps)) return config
  return Object.fromEntries(Object.entries(config).filter(([key]) => key !== "code" && key !== "_steps_error"))
}

/** Whether `config` belongs to an ordinary (non-instance) stepped transform. */
export function isSteppedTransformConfig(config: unknown): config is Record<string, unknown> & { steps: unknown[] } {
  return isRecord(config) && !("instanceOf" in config) && Array.isArray(config.steps)
}

/** Distinct input names the steps reference, in first-seen order. */
export function referencedStepInputs(steps: unknown[]): string[] {
  const seen: string[] = []
  const add = (name: unknown) => {
    if (typeof name === "string" && !seen.includes(name)) seen.push(name)
  }
  for (const raw of steps) {
    if (!isRecord(raw)) continue
    if (raw.kind === "source" || raw.kind === "join") add(raw.input)
    else if (raw.kind === "concat" && Array.isArray(raw.inputs)) raw.inputs.forEach(add)
  }
  return seen
}

/** A failed rename names the input two references would share. */
export type StepInputRename = { ok: true; steps: unknown[]; changed: boolean } | { ok: false; duplicate: string }

/**
 * Map every input reference through `renames`. Fails when two distinct
 * referenced inputs would end up sharing one name.
 */
export function renameStepInputs(steps: unknown[], renames: ReadonlyMap<string, string>): StepInputRename {
  const rename = (name: unknown): unknown =>
    typeof name === "string" ? (renames.get(name) ?? name) : name
  const before = referencedStepInputs(steps)
  const after = new Set(before.map((name) => rename(name) as string))
  if (after.size !== before.length) {
    const duplicate = before.map((name) => rename(name) as string).find((name, index, all) => all.indexOf(name) !== index) ?? ""
    return { ok: false, duplicate }
  }
  let changed = false
  const next = steps.map((raw) => {
    if (!isRecord(raw)) return raw
    const step: Record<string, unknown> = { ...raw }
    if ((step.kind === "source" || step.kind === "join") && typeof step.input === "string") {
      const renamed = rename(step.input)
      if (renamed !== step.input) changed = true
      step.input = renamed
    } else if (step.kind === "concat" && Array.isArray(step.inputs)) {
      const renamed = step.inputs.map(rename)
      if (renamed.some((name, index) => name !== (step.inputs as unknown[])[index])) changed = true
      step.inputs = renamed
    }
    return step
  })
  return { ok: true, steps: next, changed }
}
