import { describe, expect, it } from "vitest"
import {
  MISSING_VALUE,
  constantDrafts,
  constantIssues,
  constantsPayload,
  constantsWithSourceValue,
  saveRefusal,
  withoutSource,
  type GlobalConstantDraft,
} from "../globalConstants"
import {
  EVERY_CONSTANT,
  codeConstantReads,
  constantReaders,
  convertValue,
  freeConstantName,
  joinSources,
  newConstantDraft,
  nodeConstantReads,
  splitBySource,
  unknownSourceKeys,
  valuesDiscardedByJoining,
  withType,
} from "../globalConstantsEditing"

const uniform = (name: string, value: string, type: GlobalConstantDraft["type"] = "float"): GlobalConstantDraft =>
  ({ name, type, split: false, value, bySource: {} })
const split = (name: string, bySource: Record<string, string>, type: GlobalConstantDraft["type"] = "float"): GlobalConstantDraft =>
  ({ name, type, split: true, value: "", bySource })

describe("global constant drafts", () => {
  it("round-trip the stored shape, leaving a split constant's missing sources out", () => {
    const drafts = constantDrafts([
      { name: "rate", type: "float", value: 1.5 },
      { name: "label", type: "text", by_source: { live: "a", nb_batch: "b" } },
      { name: "on", type: "boolean", value: false },
    ])

    expect(drafts).toEqual([
      uniform("rate", "1.5"),
      split("label", { live: "a", nb_batch: "b" }, "text"),
      uniform("on", "false", "boolean"),
    ])
    expect(constantsPayload([...drafts, split("factor", { live: "2", nb_batch: "" }, "integer")])).toEqual([
      { name: "rate", type: "float", value: 1.5 },
      { name: "label", type: "text", by_source: { live: "a", nb_batch: "b" } },
      { name: "on", type: "boolean", value: false },
      { name: "factor", type: "integer", by_source: { live: 2 } },
    ])
  })

  it("keep an empty split text value, which is a value, apart from a missing one", () => {
    const drafts = constantDrafts([{ name: "suffix", type: "text", by_source: { live: "", nb_batch: "_batch" } }])

    expect(constantsPayload(drafts)).toEqual([
      { name: "suffix", type: "text", by_source: { live: "", nb_batch: "_batch" } },
    ])
    expect(constantIssues(drafts, ["live", "nb_batch", "other"])[0].bySource).toEqual({ other: MISSING_VALUE })
    expect(constantsWithSourceValue(drafts, "live")).toEqual(["suffix"])
    expect(constantsPayload([split("rate", { live: "", nb_batch: "2" })])).toEqual([
      { name: "rate", type: "float", by_source: { nb_batch: 2 } },
    ])
  })

  it("refuse an integer a JavaScript number cannot hold exactly", () => {
    expect(constantIssues([uniform("big", "9007199254740993", "integer")], [])[0].value).toBe(
      "Enter a whole number between -9007199254740991 and 9007199254740991.",
    )
    expect(constantIssues([uniform("max", "9007199254740991", "integer")], [])[0].value).toBeUndefined()
  })

  it("leave invalid drafts out of the payload", () => {
    expect(constantsPayload([uniform("rate", "abc"), uniform("1bad", "1"), uniform("ok", "2")])).toEqual([
      { name: "ok", type: "float", value: 2 },
    ])
  })

  it("mark each invalid name, duplicate and value with its reason, and a missing split value", () => {
    const issues = constantIssues(
      [
        uniform("class", "1"),
        uniform("rate", "1"),
        uniform("rate", "x"),
        uniform("when", "2026-02-30", "date"),
        split("factor", { live: "1.5" }, "integer"),
      ],
      ["live", "nb_batch"],
    )

    expect(issues[0].name).toBe("class is a Python keyword.")
    expect(issues[1].name).toBe("rate is defined more than once.")
    expect(issues[2].value).toBe("Enter a number.")
    expect(issues[3].value).toBe("Enter a real date as YYYY-MM-DD.")
    expect(issues[4].bySource).toEqual({ live: "Enter a whole number.", nb_batch: MISSING_VALUE })
  })

  it("refuse a save for an invalid constant but not for a missing value", () => {
    expect(saveRefusal([split("rate", { live: "1" })], ["live", "nb_batch"])).toBeNull()
    expect(saveRefusal([uniform("rate", "")], ["live"])).toBe(
      "global constant rate is invalid - Enter a value.",
    )
  })

  it("keep each value that converts exactly when the type changes", () => {
    expect(convertValue("3", "integer", "float")).toBe("3")
    expect(convertValue("3.0", "float", "integer")).toBe("3")
    expect(convertValue("3.5", "float", "integer")).toBe("")
    expect(convertValue("true", "boolean", "text")).toBe("true")
    expect(convertValue("abc", "text", "integer")).toBe("")
    expect(withType(split("x", { live: "2.0", nb_batch: "2.5" }), "integer").bySource).toEqual({
      live: "2",
      nb_batch: "",
    })
    // A missing number stays missing as text, rather than becoming empty text.
    expect(withType(split("x", { live: "2", nb_batch: "" }), "text").bySource).toEqual({ live: "2" })
  })

  it("split from the uniform value and join on the live value, naming what joining discards", () => {
    const splitDraft = splitBySource(uniform("rate", "1.5"), ["live", "nb_batch"])
    expect(splitDraft.bySource).toEqual({ live: "1.5", nb_batch: "1.5" })

    const edited = { ...splitDraft, bySource: { live: "1.5", nb_batch: "2.5" } }
    expect(valuesDiscardedByJoining(splitDraft)).toEqual({})
    expect(valuesDiscardedByJoining(edited)).toEqual({ nb_batch: "2.5" })
    expect(joinSources(edited)).toEqual(uniform("rate", "1.5"))
  })

  it("name a new constant with the first free generated name", () => {
    expect(freeConstantName([uniform("constant_1", "1"), uniform("constant_3", "1")])).toBe("constant_2")
    expect(newConstantDraft([])).toEqual(uniform("constant_1", ""))
  })

  it("find, remove and report source values", () => {
    const drafts = [split("rate", { live: "1", nb_batch: "2", old: "3" }), uniform("flat", "1")]
    expect(constantsWithSourceValue(drafts, "nb_batch")).toEqual(["rate"])
    expect(withoutSource(drafts, "nb_batch")[0].bySource).toEqual({ live: "1", old: "3" })
    expect(unknownSourceKeys(drafts, ["live", "nb_batch"])).toEqual(["old"])
  })
})

describe("constant reads", () => {
  it("names each attribute read, and every constant for any other use", () => {
    expect(codeConstantReads("df = df.with_columns(r=pl.lit(global_constants.rate * global_constants.factor))"))
      .toEqual(new Set(["rate", "factor"]))
    expect(codeConstantReads("x = getattr(global_constants, name)")).toBe(EVERY_CONSTANT)
    expect(codeConstantReads("df = df")).toEqual(new Set())
  })

  it("reads a node's code, its free-code steps and its Constant operands", () => {
    expect(nodeConstantReads({
      code: "df = df",
      steps: [
        { kind: "free_code", code: "df = df.with_columns(pl.lit(global_constants.a))" },
        { kind: "filter", predicate: { type: "operand", operand: { kind: "constant", name: "b" } } },
      ],
    })).toEqual(new Set(["a", "b"]))
  })

  it("lists the nodes that read a constant by label", () => {
    const nodes = [
      { id: "n1", data: { label: "Pricing", config: { code: "x = global_constants.rate" } } },
      { id: "n2", data: { label: "Other", config: { code: "x = 1" } } },
      { id: "n3", data: { label: "", config: { code: "f(global_constants)" } } },
    ]
    expect(constantReaders(nodes, "rate")).toEqual(["Pricing", "n3"])
  })
})
