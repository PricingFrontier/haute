import { describe, it, expect } from "vitest"
import { toggleButtonStyle } from "../styles"

describe("toggleButtonStyle", () => {
  it("selected returns purple background, border, and color", () => {
    const style = toggleButtonStyle(true)
    expect(style.background).toBe("rgba(168,85,247,.15)")
    expect(style.color).toBe("#a855f7")
    expect(style.border).toBe("1px solid rgba(168,85,247,.3)")
  })

  it("unselected returns chrome-hover background, transparent border, and muted color", () => {
    const style = toggleButtonStyle(false)
    expect(style.background).toBe("var(--chrome-hover)")
    expect(style.color).toBe("var(--text-muted)")
    expect(style.border).toBe("1px solid transparent")
  })

  it("return type is usable as React.CSSProperties", () => {
    const style: React.CSSProperties = toggleButtonStyle(true)
    expect(style).toHaveProperty("background")
    expect(style).toHaveProperty("color")
    expect(style).toHaveProperty("border")
  })

  it("selected background contains rgba", () => {
    expect(toggleButtonStyle(true).background).toContain("rgba")
  })

  it("unselected background contains var(--chrome-hover)", () => {
    expect(toggleButtonStyle(false).background).toContain("var(--chrome-hover)")
  })
})
