import { cleanup, render, screen } from "@testing-library/react"
import type { Edge } from "@xyflow/react"
import { afterEach, describe, expect, it } from "vitest"
import EdgeJoinInsertionFeedback from "../EdgeJoinInsertionFeedback"
import {
  EDGE_JOIN_INSERTION_CANDIDATE_CLASS,
  withEdgeJoinInsertionCandidate,
} from "../../utils/edgeJoinInsertionFeedback"

afterEach(cleanup)

describe("EdgeJoinInsertionFeedback", () => {
  it("exposes a named polite status only while a compatible edge is active", () => {
    const { rerender } = render(<EdgeJoinInsertionFeedback candidateEdgeId="edge-a-b" />)

    const status = screen.getByRole("status", { name: /release to insert an edge join/i })
    expect(status).toHaveAttribute("aria-live", "polite")

    rerender(<EdgeJoinInsertionFeedback candidateEdgeId={null} />)
    expect(screen.queryByRole("status")).not.toBeInTheDocument()
  })
})

describe("withEdgeJoinInsertionCandidate", () => {
  const edges: Edge[] = [
    { id: "edge-a-b", source: "a", target: "b", className: "trace-motion-lite" },
    { id: "edge-b-c", source: "b", target: "c" },
  ]

  it("decorates only the candidate and preserves existing edge classes", () => {
    const decorated = withEdgeJoinInsertionCandidate(edges, "edge-a-b")

    expect(decorated).not.toBe(edges)
    expect(decorated[0]).toMatchObject({
      id: "edge-a-b",
      className: `trace-motion-lite ${EDGE_JOIN_INSERTION_CANDIDATE_CLASS}`,
      ariaLabel: "Release to insert an Edge Join on this connection",
    })
    expect(decorated[1]).toBe(edges[1])
  })

  it("preserves the edge-array identity when there is no active candidate", () => {
    expect(withEdgeJoinInsertionCandidate(edges, null)).toBe(edges)
    expect(withEdgeJoinInsertionCandidate(edges, "missing")).toBe(edges)
  })
})
