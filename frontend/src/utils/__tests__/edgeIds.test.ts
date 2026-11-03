import { describe, expect, it } from "vitest"

import { loadUiContractFixture } from "../../testSupport/uiContractFixtures"
import { edgeId, withEdgeIdentity, type EdgeIdentity } from "../edgeIds"

describe("edge ids", () => {
  it("are minted as the server mints them, from the whole connection identity", () => {
    const { cases } = loadUiContractFixture<{ cases: (Required<EdgeIdentity> & { id: string })[] }>("edge_ids")
    for (const { id, ...identity } of cases) expect(edgeId(identity)).toBe(id)
    expect(new Set(cases.map((item) => item.id)).size).toBe(cases.length)
  })

  it("treat an absent handle as null and refuse an empty field", () => {
    expect(edgeId({ source: "source", target: "target" })).toBe("e-source-target")
    expect(() => edgeId({ source: "a", target: "b", sourceHandle: "" })).toThrow("empty")
    expect(() => edgeId({ source: "", target: "b" })).toThrow("empty")
  })

  it("follow an edge moved to another handle, keeping everything else", () => {
    const edge = { id: "e-a-join--base", source: "a", target: "join", targetHandle: "base", data: { kept: true } }
    expect(withEdgeIdentity(edge, { targetHandle: "join" })).toEqual({
      id: "e-a-join--join", source: "a", target: "join", targetHandle: "join", data: { kept: true },
    })
    expect(edge.id).toBe("e-a-join--base")
  })
})
