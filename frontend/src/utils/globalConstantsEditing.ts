/**
 * The Constants pane's editing helpers — type changes, splitting and
 * joining a constant by source, new names — and the reads analysis that
 * names each constant's readers. Kept apart from `globalConstants.ts`,
 * which the initial bundle loads, so they load with the pane and the step
 * editor.
 */

import { isPlainObject } from "../types/guards"
import {
  holdsSourceValue,
  valueProblem,
  type GlobalConstantDraft,
  type GlobalConstantType,
} from "./globalConstants"

/**
 * *text*, a value of type *from*, as a value of type *to*: kept when it
 * converts exactly (an integer to a float, a whole float to an integer, any
 * value to text), and emptied otherwise.
 */
export function convertValue(text: string, from: GlobalConstantType, to: GlobalConstantType): string {
  if (text === "" || from === to || to === "text") return text
  if (to === "integer" && from === "float" && valueProblem("float", text) === null) {
    const number = Number(text)
    return Number.isInteger(number) ? String(number) : ""
  }
  return valueProblem(to, text) === null ? text : ""
}

/** *draft* with its type changed, keeping each value that converts exactly; a missing value stays missing. */
export function withType(draft: GlobalConstantDraft, type: GlobalConstantType): GlobalConstantDraft {
  return {
    ...draft,
    type,
    value: convertValue(draft.value, draft.type, type),
    bySource: Object.fromEntries(
      Object.entries(draft.bySource)
        .filter(([source]) => holdsSourceValue(draft, source))
        .map(([source, text]) => [source, convertValue(text, draft.type, type)]),
    ),
  }
}

/** *draft* split by source, every source starting from the uniform value. */
export function splitBySource(draft: GlobalConstantDraft, sources: readonly string[]): GlobalConstantDraft {
  return {
    ...draft,
    split: true,
    bySource: Object.fromEntries(sources.map((source) => [source, draft.value])),
  }
}

/** The values joining *draft* would discard: each non-live value that differs from live's. */
export function valuesDiscardedByJoining(draft: GlobalConstantDraft): Record<string, string> {
  const live = holdsSourceValue(draft, "live") ? draft.bySource.live : undefined
  return Object.fromEntries(
    Object.entries(draft.bySource).filter(
      ([source, text]) => source !== "live" && holdsSourceValue(draft, source) && text !== live,
    ),
  )
}

/** *draft* made uniform, keeping its live value. */
export function joinSources(draft: GlobalConstantDraft): GlobalConstantDraft {
  return { ...draft, split: false, value: draft.bySource.live ?? "", bySource: {} }
}

/** A free generated name: `constant_1`, `constant_2`, … */
export function freeConstantName(drafts: readonly GlobalConstantDraft[]): string {
  const taken = new Set(drafts.map((draft) => draft.name))
  let index = 1
  while (taken.has(`constant_${index}`)) index += 1
  return `constant_${index}`
}

/** A new, empty uniform float constant with a free name. */
export function newConstantDraft(drafts: readonly GlobalConstantDraft[]): GlobalConstantDraft {
  return { name: freeConstantName(drafts), type: "float", split: false, value: "", bySource: {} }
}

/** The `by_source` keys of split drafts that are not pipeline sources. */
export function unknownSourceKeys(
  drafts: readonly GlobalConstantDraft[],
  sources: readonly string[],
): string[] {
  const keys = new Set<string>()
  for (const draft of drafts) {
    if (!draft.split) continue
    for (const source of Object.keys(draft.bySource)) {
      if (!sources.includes(source)) keys.add(source)
    }
  }
  return [...keys].sort()
}

// ─── Reads ────────────────────────────────────────────────────────────────

/** Every constant: code that passes the name on may read any of them. */
export const EVERY_CONSTANT = "every" as const
export type ConstantReads = ReadonlySet<string> | typeof EVERY_CONSTANT

const ATTRIBUTE_READ = /\bglobal_constants\s*\.\s*([A-Za-z_][A-Za-z0-9_]*)/g
const ANY_USE = /\bglobal_constants\b/g

/**
 * The constants *code* names as `global_constants.<name>`, or every constant
 * when it uses the name any other way — the rule of the backend's reads
 * analysis. Text is scanned, not parsed, so a read inside a string or a
 * comment also counts.
 */
export function codeConstantReads(code: string): ConstantReads {
  if (!code.includes("global_constants")) return new Set()
  const reads = new Set<string>()
  let attributeUses = 0
  for (const match of code.matchAll(ATTRIBUTE_READ)) {
    reads.add(match[1])
    attributeUses += 1
  }
  const uses = [...code.matchAll(ANY_USE)].length
  return uses > attributeUses ? EVERY_CONSTANT : reads
}


function stepConstantReads(value: unknown, reads: Set<string>): void {
  if (Array.isArray(value)) {
    for (const item of value) stepConstantReads(item, reads)
    return
  }
  if (!isPlainObject(value)) return
  if (value.kind === "constant" && typeof value.name === "string") reads.add(value.name)
  for (const child of Object.values(value)) stepConstantReads(child, reads)
}

/** The constants a node's configuration reads: its steps' Constant operands and its code. */
export function nodeConstantReads(config: Record<string, unknown> | undefined): ConstantReads {
  if (!config) return new Set()
  const reads = new Set<string>()
  stepConstantReads(config.steps, reads)
  const codes: string[] = []
  if (typeof config.code === "string") codes.push(config.code)
  if (Array.isArray(config.steps)) {
    for (const step of config.steps) {
      if (isPlainObject(step) && step.kind === "free_code" && typeof step.code === "string") {
        codes.push(step.code)
      }
    }
  }
  for (const code of codes) {
    const codeReads = codeConstantReads(code)
    if (codeReads === EVERY_CONSTANT) return EVERY_CONSTANT
    for (const name of codeReads) reads.add(name)
  }
  return reads
}

interface ReaderNode {
  id: string
  data: Record<string, unknown>
}

/** The labels of the nodes that read *name*, in node order. */
export function constantReaders(nodes: readonly ReaderNode[], name: string): string[] {
  const readers: string[] = []
  for (const node of nodes) {
    const config = isPlainObject(node.data.config) ? node.data.config : undefined
    const reads = nodeConstantReads(config)
    if (reads === EVERY_CONSTANT || reads.has(name)) {
      readers.push(typeof node.data.label === "string" && node.data.label ? node.data.label : node.id)
    }
  }
  return readers
}
