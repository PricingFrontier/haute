import { describe, expect, it } from "vitest"

import type { PipelineNameViolation } from "../../types/pipelineDocument"
import { frameViolationMessages, portViolationMessages } from "../inputBindingViolations"

function violation(
  message: string,
  binding: PipelineNameViolation["binding"],
): PipelineNameViolation {
  return { kind: "support_input", name: "x", message, parties: [], binding }
}

const FRAME = (nodeId: string, handle: string) =>
  ({ kind: "frame", node_id: nodeId, handle, submodel: null }) as const
const PORT = (submodel: string, port: string) => ({ kind: "submodel_input", submodel, port }) as const

describe("input binding violations", () => {
  const violations = [
    violation("first consumer", FRAME("quotes", "rate_lookup")),
    violation("second consumer", FRAME("quotes", "rate_lookup")),
    violation("another request input", FRAME("other", "drivers")),
    violation("a port", PORT("rates", "band")),
    violation("an alias", null),
  ]

  it("finds each frame label's violations on one request input, every consumer's together", () => {
    expect([...frameViolationMessages(violations, "quotes")]).toEqual([
      ["rate_lookup", "first consumer second consumer"],
    ])
  })

  it("finds each public input port's violations in one definition", () => {
    expect([...portViolationMessages(violations, "rates")]).toEqual([["band", "a port"]])
    expect(portViolationMessages(violations, "other").size).toBe(0)
  })
})
