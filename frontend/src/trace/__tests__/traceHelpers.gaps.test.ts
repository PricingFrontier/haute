import { describe, it, expect } from "vitest"
import {
  buildWaterfallSteps,
  resolveWaterfallProp,
  buildChainEntries,
  buildInputSourceEntries,
} from "../traceHelpers"
import type {
  WaterfallEntryProp,
  ExpressionChainEntry,
  InputSourceEntry,
} from "../traceHelpers"

describe("buildWaterfallSteps", () => {
  it("returns null when fewer than 3 factors", () => {
    expect(buildWaterfallSteps({ a: 1, b: 2 }, "a * b")).toBeNull()
  })

  it("returns null when a referenced factor is missing", () => {
    expect(buildWaterfallSteps({ a: 1, b: 2 }, "a * b * c")).toBeNull()
  })

  it("returns null when a referenced factor is non-numeric", () => {
    expect(buildWaterfallSteps({ a: 1, b: 2, c: "x" }, "a * b * c")).toBeNull()
  })

  it("builds steps with running products and direction per factor", () => {
    const steps = buildWaterfallSteps({ a: 100, b: 1.5, c: 0.5 }, "a * b * c")
    expect(steps).not.toBeNull()
    expect(steps!).toHaveLength(3)

    expect(steps![0]).toEqual({
      name: "a",
      factor: 100,
      runningValue: 100,
      prevValue: 0,
      direction: "neutral",
    })
    // b = 1.5 > 1 -> positive, running 100 * 1.5 = 150
    expect(steps![1]).toMatchObject({
      name: "b",
      factor: 1.5,
      runningValue: 150,
      prevValue: 100,
      direction: "positive",
    })
    // c = 0.5 < 1 -> negative, running 150 * 0.5 = 75
    expect(steps![2]).toMatchObject({
      name: "c",
      factor: 0.5,
      runningValue: 75,
      prevValue: 150,
      direction: "negative",
    })
  })

  it("marks a factor of exactly 1 as neutral", () => {
    const steps = buildWaterfallSteps({ a: 2, b: 1, c: 3 }, "a * b * c")
    expect(steps![1]).toMatchObject({ name: "b", factor: 1, direction: "neutral" })
  })

  it("trims whitespace around factor names", () => {
    const steps = buildWaterfallSteps({ a: 2, b: 3, c: 4 }, "a*b*c")
    expect(steps!.map((s) => s.name)).toEqual(["a", "b", "c"])
  })
})

describe("resolveWaterfallProp", () => {
  it("returns nulls when prop is null/undefined", () => {
    expect(resolveWaterfallProp(null)).toEqual({ steps: null, error: null })
    expect(resolveWaterfallProp(undefined)).toEqual({ steps: null, error: null })
  })

  it("returns the error object when an error prop is given", () => {
    const err = { error: "boom", error_type: "ValueError" }
    expect(resolveWaterfallProp(err)).toEqual({ steps: null, error: err })
  })

  it("returns nulls for a non-array object without an error key", () => {
    const obj = { something: "else" } as unknown as WaterfallEntryProp[]
    expect(resolveWaterfallProp(obj)).toEqual({ steps: null, error: null })
  })

  it("returns nulls when fewer than 3 entries", () => {
    const entries: WaterfallEntryProp[] = [
      { label: "a", operation: "x", value: 1, delta: 0, cumulative: 1 },
      { label: "b", operation: "x", value: 2, delta: 1, cumulative: 2 },
    ]
    expect(resolveWaterfallProp(entries)).toEqual({ steps: null, error: null })
  })

  it("maps entries into steps with prevValue from prior cumulative and signed direction", () => {
    const entries: WaterfallEntryProp[] = [
      { label: "base", operation: "init", value: 100, delta: 0, cumulative: 100 },
      { label: "up", operation: "*", value: 1.5, delta: 50, cumulative: 150 },
      { label: "down", operation: "*", value: 0.5, delta: -75, cumulative: 75 },
    ]
    const { steps, error } = resolveWaterfallProp(entries)
    expect(error).toBeNull()
    expect(steps).toHaveLength(3)
    expect(steps![0]).toMatchObject({
      name: "base",
      runningValue: 100,
      prevValue: 0,
      direction: "neutral",
    })
    expect(steps![1]).toMatchObject({
      name: "up",
      runningValue: 150,
      prevValue: 100,
      direction: "positive",
    })
    expect(steps![2]).toMatchObject({
      name: "down",
      runningValue: 75,
      prevValue: 150,
      direction: "negative",
    })
  })
})

describe("buildChainEntries", () => {
  it("returns empty array when chain is absent or trivial", () => {
    expect(buildChainEntries(null, "t", {})).toEqual([])
    expect(buildChainEntries(undefined, "t", {})).toEqual([])
    const single: ExpressionChainEntry[] = [
      { expression_text: "a * b", target_column: "t" },
    ]
    expect(buildChainEntries(single, "t", {})).toEqual([])
  })

  it("skips the target column and normalises remaining rows", () => {
    const chain: ExpressionChainEntry[] = [
      {
        expression_text: "a * b",
        target_column: "mid",
        substituted_text: "2 * 3",
        result_value: 6,
      },
      { expression_text: "mid / 2", target_column: "final" },
    ]
    const out = buildChainEntries(chain, "final", { mid: 6 })
    expect(out).toHaveLength(1)
    expect(out[0]).toEqual({
      column: "mid",
      formulaText: "a × b",
      substitutedText: "2 × 3",
      value: 6,
      source: null,
    })
  })

  it("falls back to inputValues when result_value is missing and nulls absent text", () => {
    const chain: ExpressionChainEntry[] = [
      { expression_text: "", target_column: "x" },
      { expression_text: "x + 1", target_column: "y" },
    ]
    const out = buildChainEntries(chain, "ignored", { x: 42 })
    const xRow = out.find((r) => r.column === "x")!
    expect(xRow.formulaText).toBeNull()
    expect(xRow.substitutedText).toBeNull()
    expect(xRow.value).toBe(42)
  })
})

describe("buildInputSourceEntries", () => {
  it("returns empty array when inputSources is absent", () => {
    expect(buildInputSourceEntries(null, {}, new Set())).toEqual([])
    expect(buildInputSourceEntries(undefined, {}, new Set())).toEqual([])
  })

  it("skips columns already present and normalises the rest", () => {
    const sources: Record<string, InputSourceEntry> = {
      dup: { node_name: "n1" },
      fresh: {
        node_name: "n2",
        expression_text: "a / b",
        substituted_text: "4 / 2",
        result_value: 2,
        input_sources: { a: { node_name: "n3" } },
      },
    }
    const out = buildInputSourceEntries(sources, {}, new Set(["dup"]))
    expect(out).toHaveLength(1)
    expect(out[0]).toEqual({
      column: "fresh",
      formulaText: "a ÷ b",
      substitutedText: "4 ÷ 2",
      value: 2,
      source: "n2",
      subSources: { a: { node_name: "n3" } },
    })
  })

  it("falls back to inputValues and nulls missing fields", () => {
    const sources: Record<string, InputSourceEntry> = {
      col: { node_name: "n" },
    }
    const out = buildInputSourceEntries(sources, { col: 7 }, new Set())
    expect(out[0]).toMatchObject({
      column: "col",
      formulaText: null,
      substitutedText: null,
      value: 7,
      source: "n",
      subSources: null,
    })
  })
})
