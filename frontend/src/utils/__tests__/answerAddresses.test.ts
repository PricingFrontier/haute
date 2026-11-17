import { describe, expect, it } from "vitest"

import { answerTargetKey, keysOnCanvas, placeAddress } from "../answerAddresses"
import { addressKey, childAddress, frameKey, nodeFrame, rootAddress } from "../nodeAddress"
import { occurrencePort, rootAddresses } from "../../test-utils/factories"

const child = (occurrence: string, nodeId: string) => ({ occurrence, node_id: nodeId, label: nodeId })

describe("placeAddress", () => {
  it("places a pipeline node on the main canvas and nowhere inside a submodel", () => {
    expect(placeAddress({ occurrence: null, node_id: "source" }, null)).toEqual({ id: "source", viaCard: false })
    expect(placeAddress({ occurrence: null, node_id: "source" }, "pricing")).toBeNull()
  })

  it("places a child on its occurrence's card at the root, and on itself inside its occurrence", () => {
    expect(placeAddress(child("pricing", "rate"), null)).toEqual({ id: "pricing", viaCard: true })
    expect(placeAddress(child("pricing", "rate"), "pricing")).toEqual({ id: "rate", viaCard: false })
  })

  it("places another occurrence's child nowhere inside a submodel", () => {
    expect(placeAddress(child("pricing_2", "rate"), "pricing")).toBeNull()
  })
})

describe("placeAddress with boundary cards", () => {
  const boundaries = [
    { id: "input-card", direction: "input" as const, externalNodeIds: ["source", "pricing_2"] },
    { id: "output-card", direction: "output" as const, externalNodeIds: ["report", "source"] },
  ]

  it("places a pipeline node feeding the open occurrence on its Input card, ahead of the Output card", () => {
    expect(placeAddress({ occurrence: null, node_id: "source" }, "pricing", boundaries)).toEqual({ id: "input-card", viaCard: true })
  })

  it("places a pipeline node reading from the open occurrence on its Output card", () => {
    expect(placeAddress({ occurrence: null, node_id: "report" }, "pricing", boundaries)).toEqual({ id: "output-card", viaCard: true })
  })

  it("places another occurrence's child by the occurrence its card reaches", () => {
    expect(placeAddress(child("pricing_2", "rate"), "pricing", boundaries)).toEqual({ id: "input-card", viaCard: true })
  })

  it("places a node no card reaches nowhere, and the open occurrence's own children on themselves", () => {
    expect(placeAddress({ occurrence: null, node_id: "elsewhere" }, "pricing", boundaries)).toBeNull()
    expect(placeAddress(child("pricing", "source"), "pricing", boundaries)).toEqual({ id: "source", viaCard: false })
  })

  it("ignores the cards at the root", () => {
    expect(placeAddress(child("pricing", "rate"), null, boundaries)).toEqual({ id: "pricing", viaCard: true })
  })
})

describe("keysOnCanvas", () => {
  it("gives each canvas node the key that describes it, never a card its children's", () => {
    const addresses = {
      ...rootAddresses("source", "pricing"),
      "submodel_runtime/pricing/rate": child("pricing", "rate"),
    }

    expect(keysOnCanvas(addresses, null)).toEqual(new Map([["source", "source"], ["pricing", "pricing"]]))
    expect(keysOnCanvas(addresses, "pricing")).toEqual(new Map([["rate", "submodel_runtime/pricing/rate"]]))
  })
})

describe("answerTargetKey", () => {
  const answer = {
    node_addresses: {
      ...rootAddresses("source"),
      "submodel_runtime/pricing/rate": child("pricing", "rate"),
      "submodel_runtime/pricing_2/rate": child("pricing_2", "rate"),
    },
    occurrence_ports: [
      occurrencePort("pricing", "input", "policy", { key: "source", handle: "batch" }),
      occurrencePort("pricing", "input", "extra", null),
      occurrencePort("pricing", "output", "premium", { key: "submodel_runtime/pricing/rate", handle: "scored" }),
    ],
  }

  it("finds a node target's key by its address, telling two occurrences' children apart", () => {
    expect(answerTargetKey(answer, nodeFrame(rootAddress("source")))).toBe("source")
    expect(answerTargetKey(answer, nodeFrame(childAddress("pricing_2", "rate")))).toBe("submodel_runtime/pricing_2/rate")
  })

  it("finds a card's key through the endpoint its port resolves to", () => {
    expect(answerTargetKey(answer, { kind: "occurrenceInput", occurrence: "pricing", port: "policy" })).toBe("source")
    expect(answerTargetKey(answer, { kind: "occurrenceOutput", occurrence: "pricing", port: "premium" }))
      .toBe("submodel_runtime/pricing/rate")
  })

  it("is null for a target the answer names nothing for", () => {
    expect(answerTargetKey(answer, { kind: "occurrenceInput", occurrence: "pricing", port: "extra" })).toBeNull()
    expect(answerTargetKey({ node_addresses: {}, occurrence_ports: [] }, nodeFrame(rootAddress("source")))).toBeNull()
  })
})

describe("frameKey", () => {
  it("keys a frame target apart from every node's key and from other targets", () => {
    const output = frameKey({ kind: "occurrenceOutput", occurrence: "pricing", port: "premium" })

    expect(output).not.toBe(addressKey(rootAddress("pricing")))
    expect(output).not.toBe(addressKey(childAddress("pricing", "premium")))
    expect(output).not.toBe(frameKey({ kind: "occurrenceOutput", occurrence: "pricing_2", port: "premium" }))
    expect(output).not.toBe(frameKey({ kind: "occurrenceInput", occurrence: "pricing", port: "premium" }))
  })
})
