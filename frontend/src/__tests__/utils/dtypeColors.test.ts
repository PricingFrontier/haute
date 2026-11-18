import { describe, it, expect } from "vitest"
import { getDtypeColor, getKindColor } from "../../utils/dtypeColors"

describe("getDtypeColor", () => {
  it.each([
    ["Int8", "text-blue-400"],
    ["UInt64", "text-blue-400"],
    ["Float16", "text-emerald-400"],
    ["Float64", "text-emerald-400"],
    ["Decimal(precision=10, scale=2)", "text-emerald-400"],
    ["String", "text-amber-400"],
    ["Utf8", "text-amber-400"],
    ["Boolean", "text-purple-400"],
    ["Date", "text-rose-400"],
    ["Datetime(time_unit='us', time_zone=None)", "text-rose-400"],
  ])("colours %s by its kind", (dtype, color) => {
    expect(getDtypeColor(dtype)).toBe(color)
  })

  it.each(["Categorical", "Duration(time_unit='us')", "List(Int64)", "Complex128", ""])(
    "gives %s the muted colour",
    (dtype) => {
      expect(getDtypeColor(dtype)).toBe("text-slate-400")
    },
  )

  it("colours a kind as it colours its types", () => {
    expect(getKindColor("integer")).toBe(getDtypeColor("Int64"))
    expect(getKindColor(null)).toBe("text-slate-400")
  })
})
