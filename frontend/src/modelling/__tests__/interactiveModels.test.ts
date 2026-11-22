/**
 * An interactive build's models (specs/frontend-modelling-optimiser-ui): one model's flat
 * config, and an edit to it sent to each key's home.
 */
import { describe, expect, it } from "vitest"
import { addModelCopy, applyModelUpdate, chosenModel, freeModelName, modelConfig, removeModel } from "../interactiveModels"

const config = {
  interactive_build: true,
  target: "claims",
  models: [
    { id: "m1", name: "t-boost", algorithm: "tboost", feature_columns: ["age"], params: { n_trees: 10 } },
    { id: "m2", name: "wide", algorithm: "tboost", feature_columns: ["age", "region"] },
  ],
}

describe("interactiveModels", () => {
  it("reads a model as the shared settings with its own over them", () => {
    expect(modelConfig(config, config.models[1])).toEqual({
      interactive_build: true,
      target: "claims",
      algorithm: "tboost",
      feature_columns: ["age", "region"],
    })
  })

  it("sends each key of one edit to its home, the model's or the node's, and removes an undefined one", () => {
    const next = applyModelUpdate(config, "m1", { feature_columns: ["region"], params: undefined, target: "cost" })

    expect(next.target).toBe("cost")
    expect(next.feature_columns).toBeUndefined()
    expect(next.models).toEqual([
      { id: "m1", name: "t-boost", algorithm: "tboost", feature_columns: ["region"] },
      config.models[1],
    ])
    expect(() => applyModelUpdate(config, "m1", { name: "other" })).toThrow(/cannot set name/)
  })

  it("falls back to the first model once the chosen one has gone, and finds a free name", () => {
    expect(chosenModel(config, "m9").id).toBe("m1")
    expect(freeModelName(config.models, "t-boost")).toBe("t-boost 2")
    expect(freeModelName(config.models, "short list")).toBe("short list")
  })

  it("never gives a new model the id of one removed, so it cannot take the removed model's run", () => {
    const removed = removeModel(config, "m2")
    const { model } = addModelCopy(removed, config.models[0], "again")

    expect(model.id).not.toBe("m2")
    expect(model.id).not.toBe("m1")
  })
})
