/**
 * The Modelling view's own state (specs/frontend-modelling-optimiser-ui): the node open,
 * its chosen and unticked models, the settings panel, the pane and the factor charted.
 */
import { afterEach, describe, expect, it } from "vitest"
import useModellingViewStore, { openModelInModellingView } from "../useModellingViewStore"
import useWorkbenchStore from "../useWorkbenchStore"

const reset = () => {
  useModellingViewStore.setState({ modelId: null, chosenModel: null, hiddenModels: [], panel: "features", pane: "factor", factor: null, trainAction: null })
  useWorkbenchStore.setState({ activeView: "pipeline" })
}

describe("useModellingViewStore", () => {
  afterEach(reset)

  it("opens a model at its settings and the Factor pane, every model ticked and no factor chosen", () => {
    useModellingViewStore.setState({ panel: "split", pane: "curve", factor: "age", hiddenModels: ["m2"] })
    useModellingViewStore.getState().openModel("frequency")

    expect(useModellingViewStore.getState()).toMatchObject({ modelId: "frequency", panel: "features", pane: "factor", factor: null, hiddenModels: [] })
  })

  it("keeps the panel, the pane, the unticked models and the factor when the open model is opened again", () => {
    const store = useModellingViewStore.getState()
    store.openModel("frequency")
    store.showPanel("setup")
    store.setModelShown("m2", false)
    store.selectFactor("age")
    store.showPane("curve")
    store.openModel("frequency")

    expect(useModellingViewStore.getState()).toMatchObject({ modelId: "frequency", panel: "setup", pane: "curve", factor: "age", hiddenModels: ["m2"] })
  })

  it("shows the chosen model's Features from a shared panel, keeps a model panel or a closed one, and ticks a model back on", () => {
    const store = useModellingViewStore.getState()
    store.showPanel("split")
    store.showModelPanel()
    expect(useModellingViewStore.getState().panel).toBe("features")
    store.showPanel("train")
    store.showModelPanel()
    expect(useModellingViewStore.getState().panel).toBe("train")
    // A closed panel stays closed when a model is chosen.
    store.closePanel()
    store.showModelPanel()
    expect(useModellingViewStore.getState().panel).toBeNull()

    store.setModelShown("m1", false)
    store.setModelShown("m1", false)
    expect(useModellingViewStore.getState().hiddenModels).toEqual(["m1"])
    store.setModelShown("m1", true)
    expect(useModellingViewStore.getState().hiddenModels).toEqual([])
  })

  it("chooses a model of the node, and forgets the choice when another node opens", () => {
    const store = useModellingViewStore.getState()
    store.openModel("frequency")
    store.chooseModel("m2")
    store.openModel("frequency")
    expect(useModellingViewStore.getState().chosenModel).toBe("m2")

    store.openModel("severity")
    expect(useModellingViewStore.getState().chosenModel).toBeNull()
  })

  it("shows the factor chart when a factor is chosen", () => {
    const store = useModellingViewStore.getState()
    store.openModel("frequency")
    store.showPane("curve")
    store.selectFactor("age")

    expect(useModellingViewStore.getState()).toMatchObject({ pane: "factor", factor: "age" })
  })

  it("goes back to the list, forgetting the factor", () => {
    const store = useModellingViewStore.getState()
    store.openModel("frequency")
    store.selectFactor("age")
    store.closeModel()

    expect(useModellingViewStore.getState()).toMatchObject({ modelId: null, factor: null })
  })

  it("opens a model in the view and shows the view", () => {
    openModelInModellingView("severity")

    expect(useModellingViewStore.getState().modelId).toBe("severity")
    expect(useWorkbenchStore.getState().activeView).toBe("modelling")
  })
})
