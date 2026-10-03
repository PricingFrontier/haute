import { describe, expect, it } from "vitest"
import { EditorState } from "@codemirror/state"
import { CompletionContext } from "@codemirror/autocomplete"
import { constantCompletionSource, constantCompletions } from "../constantCompletion"
import type { GlobalConstantDraft } from "../../../utils/globalConstants"

const drafts: GlobalConstantDraft[] = [
  { name: "rate", type: "float", split: true, value: "", bySource: { live: "1.5" } },
  { name: "region", type: "text", split: false, value: "north", bySource: {} },
  { name: "1bad", type: "float", split: false, value: "1", bySource: {} },
]

function complete(doc: string) {
  const state = EditorState.create({ doc })
  const source = constantCompletionSource(constantCompletions(drafts, "nb_batch"))
  return source(new CompletionContext(state, doc.length, false))
}

describe("global constant completion", () => {
  it("values each valid constant for the active source, marking a missing value", () => {
    expect(constantCompletions(drafts, "live")).toEqual([
      { name: "rate", type: "float", value: "1.5" },
      { name: "region", type: "text", value: "north" },
    ])
    expect(constantCompletions(drafts, "nb_batch")[0]).toEqual({ name: "rate", type: "float", value: "missing" })
  })

  it("completes names after global_constants. with each one's type and value", () => {
    const result = complete("df = df.with_columns(pl.lit(global_constants.r")
    expect(result?.options.map((option) => [option.label, option.detail])).toEqual([
      ["rate", "float = missing"],
      ["region", "text = north"],
    ])
    expect(complete("df = df.with_columns(pl.lit(global_constants.re")?.options.map((option) => option.label)).toEqual(["region"])
    expect(complete("df = rate")).toBeNull()
  })
})
