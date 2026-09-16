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

export type StepInputRename = { ok: true; steps: unknown[]; changed: boolean } | { ok: false; error: string }

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
    const duplicate = before.map((name) => rename(name) as string).find((name, index, all) => all.indexOf(name) !== index)
    return { ok: false, error: `input "${duplicate}" would be referenced by more than one input` }
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
