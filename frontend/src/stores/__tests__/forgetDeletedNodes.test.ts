/** A deleted node's results go with it, its models' runs among them (specs/frontend-modelling-optimiser-ui). */
import { afterEach, describe, expect, it } from "vitest"
import { makeTrainResult } from "../../test-utils/factories"
import { forgetDeletedNodes } from "../forgetDeletedNodes"
import useNodeResultsStore from "../useNodeResultsStore"

const run = { result: makeTrainResult(), jobId: "job_1", configHash: "hash", source: "live", structuralVersion: 0 }

describe("forgetDeletedNodes", () => {
  afterEach(() => useNodeResultsStore.setState({ trainResults: {}, trainJobs: {} }))

  it("forgets the runs of a deleted node's models, and keeps another node's", () => {
    useNodeResultsStore.setState({
      trainResults: {
        "frequency#model:m1": run,
        "frequency#model:m2": run,
        "frequency_2#model:m1": run,
      },
    })

    forgetDeletedNodes(["frequency"])

    expect(Object.keys(useNodeResultsStore.getState().trainResults)).toEqual(["frequency_2#model:m1"])
  })
})
