import { describe, expect, it } from "vitest"
import type { Edge, Node } from "@xyflow/react"
import {
  spareProtectedOwners,
  withNativeDeletePolicy,
} from "../submodelDeletionPolicy"

const owner: Node = {
  id: "instance_owner",
  position: { x: 0, y: 0 },
  data: {
    label: "Scoring",
    nodeType: "submodel",
    config: { definitionId: "definition_scoring", alias: "scoring" },
  },
}

const copy: Node = {
  id: "instance_copy",
  position: { x: 0, y: 0 },
  data: {
    label: "Scoring instance",
    nodeType: "submodel",
    config: {
      definitionId: "definition_scoring",
      alias: "scoring_2",
      instanceOf: "instance_owner",
    },
  },
}

const malformed: Node = {
  id: "instance_broken",
  position: { x: 0, y: 0 },
  data: { label: "Broken", nodeType: "submodel", config: {} },
}

const ordinary: Node = {
  id: "plain",
  position: { x: 0, y: 0 },
  data: { label: "Plain", nodeType: "polars", config: {} },
}

describe("withNativeDeletePolicy", () => {
  it("marks owners non-deletable and copies deletable for React Flow", () => {
    const stamped = withNativeDeletePolicy([owner, copy, malformed])
    expect(stamped.find((node) => node.id === owner.id)?.deletable).toBe(false)
    expect(stamped.find((node) => node.id === copy.id)?.deletable).toBe(true)
    expect(stamped.find((node) => node.id === malformed.id)?.deletable).toBe(false)
  })

  it("leaves non-submodel nodes untouched, preserving object identity", () => {
    const stamped = withNativeDeletePolicy([ordinary])
    expect(stamped[0]).toBe(ordinary)
  })

  it("preserves node identity when the stamp already matches", () => {
    const preStamped = { ...owner, deletable: false }
    const stamped = withNativeDeletePolicy([preStamped])
    expect(stamped[0]).toBe(preStamped)
  })
})

describe("spareProtectedOwners", () => {
  it("spares owners and their incident edges while the rest still deletes", () => {
    const edges: Edge[] = [
      { id: "e_owner", source: "plain", target: "instance_owner" },
      { id: "e_copy", source: "plain", target: "instance_copy" },
    ]
    const result = spareProtectedOwners([owner, copy, ordinary], edges)
    expect(result.sparedOwnerIds).toEqual(["instance_owner"])
    expect(result.nodes.map((node) => node.id)).toEqual(["instance_copy", "plain"])
    expect(result.edges.map((edge) => edge.id)).toEqual(["e_copy"])
  })

  it("passes the selection through unchanged when no owner is doomed", () => {
    const edges: Edge[] = [{ id: "e_copy", source: "plain", target: "instance_copy" }]
    const result = spareProtectedOwners([copy, ordinary], edges)
    expect(result.sparedOwnerIds).toEqual([])
    expect(result.nodes.map((node) => node.id)).toEqual(["instance_copy", "plain"])
    expect(result.edges).toEqual(edges)
  })
})
