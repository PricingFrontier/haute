import { describe, expect, it } from "vitest"
import { stageConfiguration } from "../stageSettings"

describe("stage settings", () => {
  it("moves supported legacy settings to their stages while preserving the GBM", () => {
    const config = { params: { n_trees: 20, prune: false, prune_main_effects: true,
      prune_path_steps: 16, band_tolerance: null, graduate: false, graduation_alpha: 2 } }
    const result = stageConfiguration(config)
    expect(result.params).toEqual({ n_trees: 20 })
    expect(result.stages.path).toEqual({ steps: 16, main_effects: true, min_rows: 500 })
    expect(result.stages.banding.tolerance).toBe(.75)
    expect(result.stages.smoothing.alpha).toBe(2)
    expect(result.stages.applied_recipe).toEqual({ schema_version: 1, operations: [] })
    expect(config.params.prune).toBe(false)
  })
  it("keeps an explicit applied recipe and refuses unsupported legacy choices", () => {
    const recipe = { schema_version: 1, operations: [{ stage: "pruned", parent: "full",
      settings: { fraction: .995, tolerance: .001, max_tables: 3 }, path: { steps: 32, main_effects: false, min_rows: 500 } }] }
    expect(stageConfiguration({ stages: { applied_recipe: recipe } }).stages.applied_recipe).toEqual(recipe)
    expect(() => stageConfiguration({ params: { prune_selector: "fold_vote" } })).toThrow(/prune_selector/)
    expect(() => stageConfiguration({ params: { prune_box_budget: 10 } })).toThrow(/prune_box_budget/)
  })
})

it.each([false, true].flatMap((pruned) => [false, true].map((banded) => ({ pruned, banded }))))(
  "routes enabled phases around skipped predecessors: %j", ({ pruned, banded }) => {
    const { stages } = stageConfiguration({ stages: { enabled: { pruned, banded, smoothed: true } } })
    expect(stages.banding.parent).toBe(pruned ? "pruned" : "full")
    expect(stages.smoothing.parent).toBe(banded ? "banded" : pruned ? "pruned" : "full")
  },
)

it("migrates the previous parent chain without changing its applied recipe", () => {
  const recipe = { schema_version: 1, operations: [{ stage: "banded", parent: "full", settings: { tolerance: .3, deviance_cap: .001 } }] }
  const { stages } = stageConfiguration({ stages: { banding: { parent: "full", tolerance: .3 }, applied_recipe: recipe } })
  expect(stages.enabled).toEqual({ pruned: false, banded: true, smoothed: true })
  expect(stages.banding.parent).toBe("full")
  expect(stages.banding.tolerance).toBe(.3)
  expect(stages.applied_recipe).toEqual(recipe)
})
