import { describe, expect, it } from "vitest"
import type { Node } from "@xyflow/react"
import {
  encodeRuntimeIdPart,
  qualifiedRuntimeNodeId,
  resolveDrilledOccurrenceIdentity,
  runtimeNodeIdForVisibleNode,
} from "../submodelRuntimeTarget"

function boundary(id: string, instanceId = "instance", definitionId = "definition"): Node {
  return { id, position: { x: 0, y: 0 }, data: { label: id, nodeType: "submodelPort", instanceId, definitionId } }
}

describe("submodel runtime targets", () => {
  it("encodes runtime segments like urllib.parse.quote(..., safe='')", () => {
    expect(encodeRuntimeIdPart("a/b ?!()'*~")).toBe("a%2Fb%20%3F%21%28%29%27%2A~")
    expect(qualifiedRuntimeNodeId("instance/a", "child ?")).toBe("submodel_runtime/instance%2Fa/child%20%3F")
  })

  it("keeps root IDs local and qualifies drilled IDs", () => {
    expect(resolveDrilledOccurrenceIdentity([])).toBeNull()
    expect(runtimeNodeIdForVisibleNode([], "child", null)).toBe("child")
    expect(runtimeNodeIdForVisibleNode(
      [boundary("in"), boundary("out")],
      "child",
      { instanceId: "instance", definitionId: "definition" },
    )).toBe("submodel_runtime/instance/child")
  })

  it("qualifies a drilled child whose definition has no boundary ports", () => {
    expect(runtimeNodeIdForVisibleNode([], "child", {
      instanceId: "source_instance",
      definitionId: "source_definition",
    })).toBe("submodel_runtime/source_instance/child")
  })

  it("fails loudly for malformed or disagreeing drilled boundaries", () => {
    expect(() => resolveDrilledOccurrenceIdentity([boundary("bad", " ")])).toThrow("malformed canonical identity")
    expect(() => resolveDrilledOccurrenceIdentity([boundary("in"), boundary("out", "other")])).toThrow("disagree")
  })
})
