import { describe, expect, it } from "vitest"
import { optimiserResultSavePath } from "../optimiserHelpers"

describe("optimiserResultSavePath", () => {
  it("gives case-variant labels on distinct nodes distinct paths", () => {
    // "Foo" and "FOO" are legal coexisting node labels; a label-only path
    // sent both to the same file and the backend save route has no
    // overwrite guard.
    const a = optimiserResultSavePath("Foo", "optimiser_1")
    const b = optimiserResultSavePath("FOO", "optimiser_2")
    expect(a).toBe("output/optimiser_Foo_optimiser_1.json")
    expect(b).toBe("output/optimiser_FOO_optimiser_2.json")
    expect(a).not.toBe(b)
  })

  it("is stable for the same node, so re-saves overwrite their own file", () => {
    expect(optimiserResultSavePath("Foo", "optimiser_1")).toBe(
      optimiserResultSavePath("Foo", "optimiser_1"),
    )
  })

  it("routes the label through the shared portableKey", () => {
    // portableKey: space → underscore, ASCII punctuation stripped,
    // casing preserved (NOT the old ad-hoc lowercase rule).
    expect(optimiserResultSavePath("My Node!", "opt_1")).toBe(
      "output/optimiser_My_Node_opt_1.json",
    )
  })

  it("sanitizes the node id defensively too", () => {
    // Real ids are filename-safe (<type>_<n> or Python func names); anything
    // odd is still flattened by portableKey rather than reaching the path.
    expect(optimiserResultSavePath("Foo", "weird id!")).toBe(
      "output/optimiser_Foo_weird_id.json",
    )
  })
})
