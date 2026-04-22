import { describe, it, expect } from "vitest"
import { toggleButtonStyle } from "../styles"
import { MODEL_COLORS } from "../../../theme/colors"

describe("toggleButtonStyle", () => {
  it("selected returns purple background, border, and color", () => {
    const style = toggleButtonStyle(true)
    expect(style.background).toBe(MODEL_COLORS.accentSoft)
    expect(style.color).toBe(MODEL_COLORS.accent)
    expect(style.border).toBe(`1px solid ${MODEL_COLORS.accentBorder}`)
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

  it("selected background uses the model soft-accent token", () => {
    expect(toggleButtonStyle(true).background).toBe(MODEL_COLORS.accentSoft)
  })

  it("unselected background contains var(--chrome-hover)", () => {
    expect(toggleButtonStyle(false).background).toContain("var(--chrome-hover)")
  })
})
