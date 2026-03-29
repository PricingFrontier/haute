import { describe, it, expect, afterEach } from "vitest"
import { render, screen, cleanup } from "@testing-library/react"
import EditorLabel from "../../../components/form/EditorLabel"

afterEach(cleanup)

const BASE_CLASSES = ["text-[11px]", "font-bold", "uppercase", "tracking-[0.08em]"]

describe("EditorLabel", () => {
  it("renders children text", () => {
    render(<EditorLabel>Hello</EditorLabel>)
    expect(screen.getByText("Hello")).toBeTruthy()
  })

  it("renders as <label> by default", () => {
    render(<EditorLabel>Label</EditorLabel>)
    expect(screen.getByText("Label").tagName).toBe("LABEL")
  })

  it('renders as <span> when as="span"', () => {
    render(<EditorLabel as="span">Span</EditorLabel>)
    expect(screen.getByText("Span").tagName).toBe("SPAN")
  })

  it('renders as <div> when as="div"', () => {
    render(<EditorLabel as="div">Div</EditorLabel>)
    expect(screen.getByText("Div").tagName).toBe("DIV")
  })

  it("default color is var(--text-muted)", () => {
    render(<EditorLabel>Styled</EditorLabel>)
    expect(screen.getByText("Styled").style.color).toBe("var(--text-muted)")
  })

  it("custom color overrides default", () => {
    render(<EditorLabel color="red">Custom</EditorLabel>)
    expect(screen.getByText("Custom").style.color).toBe("red")
  })

  it("className prop appended to base classes", () => {
    render(<EditorLabel className="extra-class">Classy</EditorLabel>)
    const el = screen.getByText("Classy")
    for (const cls of BASE_CLASSES) {
      expect(el.className).toContain(cls)
    }
    expect(el.className).toContain("extra-class")
  })

  it('htmlFor only applied when tag is "label"', () => {
    render(<EditorLabel htmlFor="input-id">For Label</EditorLabel>)
    expect(screen.getByText("For Label").getAttribute("for")).toBe("input-id")
  })

  it('htmlFor NOT applied when tag is "span"', () => {
    render(
      <EditorLabel as="span" htmlFor="input-id">
        Span
      </EditorLabel>,
    )
    expect(screen.getByText("Span").getAttribute("for")).toBeNull()
  })

  it('htmlFor NOT applied when tag is "div"', () => {
    render(
      <EditorLabel as="div" htmlFor="input-id">
        Div
      </EditorLabel>,
    )
    expect(screen.getByText("Div").getAttribute("for")).toBeNull()
  })

  it("base classes always present", () => {
    render(<EditorLabel>Base</EditorLabel>)
    const el = screen.getByText("Base")
    for (const cls of BASE_CLASSES) {
      expect(el.className).toContain(cls)
    }
  })

  it("empty children renders empty element", () => {
    const { container } = render(<EditorLabel>{""}</EditorLabel>)
    const label = container.querySelector("label")!
    expect(label).toBeTruthy()
    expect(label.textContent).toBe("")
  })
})
