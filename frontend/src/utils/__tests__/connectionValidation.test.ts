import { describe, expect, it } from "vitest"
import type { Connection, Edge } from "@xyflow/react"
import {
  isPipelineConnectionValid,
  validatePipelineConnection,
} from "../connectionValidation"
import type { SimpleNode } from "../../panels/editors/_shared"
import { NODE_TYPES, type NodeTypeValue } from "../nodeTypes"

function connection(source: string, target: string): Connection {
  return {
    source,
    target,
    sourceHandle: null,
    targetHandle: null,
  }
}

function node(id: string, label: string, nodeType: NodeTypeValue = NODE_TYPES.POLARS): SimpleNode {
  return {
    id,
    type: nodeType,
    data: { label, description: "", nodeType, config: {} },
  }
}

function apiInput(id: string, labels: readonly string[] = ["Quote_id"]): SimpleNode {
  return {
    ...node(id, id, NODE_TYPES.API_INPUT),
    data: {
      label: id,
      description: "",
      nodeType: NODE_TYPES.API_INPUT,
      config: {
        tables: labels.map((label) => ({
          path: `$[:].${label}[:]`,
          label,
          emit: true,
          columns: [{ name: "id", selected: true }],
        })),
      },
    },
  }
}

const incoming = (
  id: string,
  source: string,
  target: string,
  sourceHandle: string | null = null,
): Edge => ({ id, source, target, sourceHandle, targetHandle: null })

describe("connection validation", () => {
  it("exempts an output-to-output edgeJoin gesture from input-name uniqueness", () => {
    const nodes = [apiInput("api", ["quotes"]), node("transform", "Transform")]
    const edges = [incoming("existing", "api", "transform", "quotes")]

    expect(
      validatePipelineConnection(
        {
          source: "api",
          target: "transform",
          sourceHandle: "quotes",
          targetHandle: "transform-output",
          sourceHandleType: "source",
          targetHandleType: "source",
        },
        nodes,
        edges,
      ),
    ).toEqual({ ok: true })
  })

  it("rejects self loops globally", () => {
    const polars = node("polars1", "Polars 1")
    expect(isPipelineConnectionValid(connection(polars.id, polars.id), [polars], [])).toBe(false)
  })

  it("rejects incomplete connections globally", () => {
    const polars = node("polars1", "Polars 1")
    const output = node("output1", "Output", NODE_TYPES.OUTPUT)
    expect(
      isPipelineConnectionValid(
        { ...connection("polars1", "output1"), target: null },
        [polars, output],
        [],
      ),
    ).toBe(false)
  })

  it.each([
    [
      "source-to-target",
      {
        source: "api",
        target: "target",
        sourceHandle: "Quote_id",
        targetHandle: null,
      },
    ],
    [
      "target-to-source under ConnectionMode.Loose",
      {
        source: "target",
        target: "api",
        sourceHandle: null,
        targetHandle: "Quote_id",
      },
    ],
  ] as const)(
    "rejects a duplicate derived input name for a %s gesture",
    (_direction, candidate) => {
      const nodes = [
        apiInput("api"),
        node("ordinary", "Quote id"),
        node("target", "Target"),
      ]
      const edges = [incoming("existing", "ordinary", "target")]

      expect(isPipelineConnectionValid(candidate, nodes, edges, {})).toBe(false)
    },
  )

  it("allows a distinct derived input name on the same target", () => {
    const nodes = [
      apiInput("api"),
      node("ordinary", "Driver claims"),
      node("target", "Target"),
    ]
    const edges = [incoming("existing", "ordinary", "target")]

    expect(
      isPipelineConnectionValid(
        {
          source: "api",
          target: "target",
          sourceHandle: "Quote_id",
          targetHandle: null,
        },
        nodes,
        edges,
      ),
    ).toBe(true)
  })

  it.each([
    [
      "source-to-target",
      { source: "api", target: "target", sourceHandle: null, targetHandle: null },
    ],
    [
      "target-to-source under ConnectionMode.Loose",
      { source: "target", target: "api", sourceHandle: null, targetHandle: null },
    ],
  ] as const)(
    "rejects a null-handle apiInput connection for a %s gesture",
    (_direction, candidate) => {
      expect(
        isPipelineConnectionValid(
          candidate,
          [apiInput("api"), node("target", "Target")],
          [],
        ),
      ).toBe(false)
    },
  )

  it("rejects ordinary sources whose sanitised names collide", () => {
    const nodes = [
      node("first", "Driver claims"),
      node("second", "Driver-claims"),
      node("target", "Target"),
    ]
    const edges = [incoming("existing", "first", "target")]

    expect(
      isPipelineConnectionValid(connection("second", "target"), nodes, edges),
    ).toBe(false)
  })

  it.each([
    [
      "source-to-target",
      {
        source: "api",
        target: "submodel__pricing",
        sourceHandle: "drivers",
        targetHandle: "in__child_b",
      },
    ],
    [
      "target-to-source under ConnectionMode.Loose",
      {
        source: "submodel__pricing",
        target: "api",
        sourceHandle: "in__child_b",
        targetHandle: "drivers",
      },
    ],
  ] as const)("allows two apiInput frames to fan out to different submodel children for a %s gesture", (_direction, candidate) => {
    const api = apiInput("api", ["quotes", "drivers"])
    const ordinary = node("ordinary", "drivers")
    const boundary = node("submodel__pricing", "Pricing", NODE_TYPES.SUBMODEL)
    const childA = node("child_a", "Child A")
    const childB = node("child_b", "Child B")
    const submodels = {
      pricing: { graph: { nodes: [childA, childB], edges: [] } },
    }
    const edges = [
      {
        ...incoming("quotes_to_a", "api", boundary.id, "quotes"),
        targetHandle: "in__child_a",
      },
      {
        ...incoming("ordinary_drivers_to_a", ordinary.id, boundary.id),
        targetHandle: "in__child_a",
      },
    ]

    expect(
      validatePipelineConnection(
        candidate,
        [api, ordinary, boundary],
        edges,
        submodels,
      ),
    ).toEqual({ ok: true })
  })

  it.each([
    [
      "source-to-target",
      {
        source: "api",
        target: "submodel__pricing",
        sourceHandle: "quotes",
        targetHandle: "in__child_a",
      },
    ],
    [
      "target-to-source under ConnectionMode.Loose",
      {
        source: "submodel__pricing",
        target: "api",
        sourceHandle: "in__child_a",
        targetHandle: "quotes",
      },
    ],
  ] as const)("rejects a same-child derived-name collision for a %s gesture", (_direction, candidate) => {
    const api = apiInput("api", ["quotes"])
    const ordinary = node("ordinary", "quotes")
    const boundary = node("submodel__pricing", "Pricing", NODE_TYPES.SUBMODEL)
    const childA = node("child_a", "Child A")
    const childB = node("child_b", "Child B")
    const submodels = {
      pricing: { graph: { nodes: [childA, childB], edges: [] } },
    }
    const edges = [
      {
        ...incoming("ordinary_to_a", ordinary.id, boundary.id),
        targetHandle: "in__child_a",
      },
    ]

    expect(
      validatePipelineConnection(
        candidate,
        [api, ordinary, boundary],
        edges,
        submodels,
      ),
    ).toEqual({
      ok: false,
      reason: { kind: "duplicate-input-name", inputName: "quotes" },
    })
  })

  it.each([
    [
      "source-to-target",
      {
        source: "api",
        target: "submodel__pricing",
        sourceHandle: "quotes",
        targetHandle: "in__child_a",
      },
    ],
    [
      "target-to-source under ConnectionMode.Loose",
      {
        source: "submodel__pricing",
        target: "api",
        sourceHandle: "in__child_a",
        targetHandle: "quotes",
      },
    ],
  ] as const)(
    "rejects a boundary connection colliding with an internal child input for a %s gesture",
    (_direction, candidate) => {
      const api = apiInput("api", ["quotes"])
      const boundary = node("submodel__pricing", "Pricing", NODE_TYPES.SUBMODEL)
      const childA = node("child_a", "Child A")
      const childB = node("child_b", "Child B")
      const internalSource = node("internal_quotes", "quotes")
      const submodels = {
        pricing: {
          graph: {
            nodes: [childA, childB, internalSource],
            edges: [incoming("internal_quotes_to_a", internalSource.id, childA.id)],
            submodels: {},
          },
        },
      }

      expect(
        validatePipelineConnection(candidate, [api, boundary], [], submodels),
      ).toEqual({
        ok: false,
        reason: { kind: "duplicate-input-name", inputName: "quotes" },
      })
    },
  )
})
