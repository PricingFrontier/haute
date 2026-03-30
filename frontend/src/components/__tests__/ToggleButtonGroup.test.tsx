import { describe, it, expect, vi, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup } from "@testing-library/react"
import ToggleButtonGroup from "../ToggleButtonGroup"

const OPTIONS = [
  { key: "a" as const, label: "Alpha" },
  { key: "b" as const, label: "Beta" },
  { key: "c" as const, label: "Gamma" },
]

const ACCENT = "#3b82f6"
const ACCENT_RGB = "rgb(59, 130, 246)"

function renderToggle(overrides: Partial<Parameters<typeof ToggleButtonGroup>[0]> = {}) {
  const props = {
    value: "a" as string,
    onChange: vi.fn(),
    options: OPTIONS,
    accentColor: ACCENT,
    ...overrides,
  }
  return { ...render(<ToggleButtonGroup {...props} />), props }
}

describe("ToggleButtonGroup", () => {
  afterEach(cleanup)

  it("renders all option labels", () => {
    renderToggle()
    expect(screen.getByText("Alpha")).toBeInTheDocument()
    expect(screen.getByText("Beta")).toBeInTheDocument()
    expect(screen.getByText("Gamma")).toBeInTheDocument()
  })

  it("calls onChange with clicked option key", () => {
    const { props } = renderToggle()
    fireEvent.click(screen.getByText("Beta"))
    expect(props.onChange).toHaveBeenCalledWith("b")
  })

  it("active button has accent-colored border", () => {
    renderToggle({ value: "a" })
    const activeBtn = screen.getByText("Alpha").closest("button")!
    expect(activeBtn.style.border).toContain(ACCENT_RGB)
  })

  it("inactive button has default border", () => {
    renderToggle({ value: "a" })
    const inactiveBtn = screen.getByText("Beta").closest("button")!
    expect(inactiveBtn.style.border).toContain("var(--border)")
  })

  it("active button text color matches accent", () => {
    renderToggle({ value: "b" })
    const activeBtn = screen.getByText("Beta").closest("button")!
    expect(activeBtn.style.color).toBe(ACCENT_RGB)
  })

  it("inactive button text color is secondary", () => {
    renderToggle({ value: "b" })
    const inactiveBtn = screen.getByText("Alpha").closest("button")!
    expect(inactiveBtn.style.color).toBe("var(--text-secondary)")
  })

  it("renders icons when provided", () => {
    const options = [
      { key: "x" as const, label: "With Icon", icon: <span data-testid="test-icon">I</span> },
    ]
    render(
      <ToggleButtonGroup
        value="x"
        onChange={vi.fn()}
        options={options}
        accentColor={ACCENT}
      />,
    )
    expect(screen.getByTestId("test-icon")).toBeInTheDocument()
  })

  it("calls onChange even when clicking the already-active option", () => {
    const { props } = renderToggle({ value: "a" })
    fireEvent.click(screen.getByText("Alpha"))
    expect(props.onChange).toHaveBeenCalledWith("a")
  })

  // --- Accessibility tests ---

  it("container has role='radiogroup'", () => {
    renderToggle()
    expect(screen.getByRole("radiogroup")).toBeInTheDocument()
  })

  it("each button has role='radio'", () => {
    renderToggle()
    const radios = screen.getAllByRole("radio")
    expect(radios).toHaveLength(3)
  })

  it("active option has aria-checked='true', others have 'false'", () => {
    renderToggle({ value: "b" })
    const radios = screen.getAllByRole("radio")
    // Order: Alpha, Beta, Gamma
    expect(radios[0]).toHaveAttribute("aria-checked", "false")
    expect(radios[1]).toHaveAttribute("aria-checked", "true")
    expect(radios[2]).toHaveAttribute("aria-checked", "false")
  })

  it("only active button has tabIndex 0, others have -1", () => {
    renderToggle({ value: "b" })
    const radios = screen.getAllByRole("radio")
    expect(radios[0]).toHaveAttribute("tabindex", "-1")
    expect(radios[1]).toHaveAttribute("tabindex", "0")
    expect(radios[2]).toHaveAttribute("tabindex", "-1")
  })

  it("aria-labelledby is applied when provided", () => {
    renderToggle({ ariaLabelledBy: "my-label-id" })
    expect(screen.getByRole("radiogroup")).toHaveAttribute("aria-labelledby", "my-label-id")
  })

  it("aria-labelledby is not present when not provided", () => {
    renderToggle()
    expect(screen.getByRole("radiogroup")).not.toHaveAttribute("aria-labelledby")
  })

  it("ArrowRight moves to next option", () => {
    const { props } = renderToggle({ value: "a" })
    const radios = screen.getAllByRole("radio")
    fireEvent.keyDown(radios[0], { key: "ArrowRight" })
    expect(props.onChange).toHaveBeenCalledWith("b")
  })

  it("ArrowDown moves to next option", () => {
    const { props } = renderToggle({ value: "a" })
    const radios = screen.getAllByRole("radio")
    fireEvent.keyDown(radios[0], { key: "ArrowDown" })
    expect(props.onChange).toHaveBeenCalledWith("b")
  })

  it("ArrowLeft moves to previous option", () => {
    const { props } = renderToggle({ value: "b" })
    const radios = screen.getAllByRole("radio")
    fireEvent.keyDown(radios[1], { key: "ArrowLeft" })
    expect(props.onChange).toHaveBeenCalledWith("a")
  })

  it("ArrowUp moves to previous option", () => {
    const { props } = renderToggle({ value: "b" })
    const radios = screen.getAllByRole("radio")
    fireEvent.keyDown(radios[1], { key: "ArrowUp" })
    expect(props.onChange).toHaveBeenCalledWith("a")
  })

  it("ArrowRight wraps from last to first", () => {
    const { props } = renderToggle({ value: "c" })
    const radios = screen.getAllByRole("radio")
    fireEvent.keyDown(radios[2], { key: "ArrowRight" })
    expect(props.onChange).toHaveBeenCalledWith("a")
  })

  it("ArrowLeft wraps from first to last", () => {
    const { props } = renderToggle({ value: "a" })
    const radios = screen.getAllByRole("radio")
    fireEvent.keyDown(radios[0], { key: "ArrowLeft" })
    expect(props.onChange).toHaveBeenCalledWith("c")
  })

  it("Home key moves to first option", () => {
    const { props } = renderToggle({ value: "c" })
    const radios = screen.getAllByRole("radio")
    fireEvent.keyDown(radios[2], { key: "Home" })
    expect(props.onChange).toHaveBeenCalledWith("a")
  })

  it("End key moves to last option", () => {
    const { props } = renderToggle({ value: "a" })
    const radios = screen.getAllByRole("radio")
    fireEvent.keyDown(radios[0], { key: "End" })
    expect(props.onChange).toHaveBeenCalledWith("c")
  })

  it("arrow keys call preventDefault to avoid page scrolling", () => {
    renderToggle({ value: "a" })
    const radios = screen.getAllByRole("radio")
    const event = new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true, cancelable: true })
    const preventSpy = vi.spyOn(event, "preventDefault")
    radios[0].dispatchEvent(event)
    expect(preventSpy).toHaveBeenCalled()
  })

  it("aria-label is applied when provided", () => {
    renderToggle({ ariaLabel: "Choose mode" })
    expect(screen.getByRole("radiogroup")).toHaveAttribute("aria-label", "Choose mode")
  })

  it("works with a single option", () => {
    const onChange = vi.fn()
    render(
      <ToggleButtonGroup
        value="only"
        onChange={onChange}
        options={[{ key: "only", label: "Only" }]}
        accentColor={ACCENT}
      />,
    )
    const radios = screen.getAllByRole("radio")
    expect(radios).toHaveLength(1)
    expect(radios[0]).toHaveAttribute("aria-checked", "true")
    expect(radios[0]).toHaveAttribute("tabindex", "0")
    // Arrow wraps to itself
    fireEvent.keyDown(radios[0], { key: "ArrowRight" })
    expect(onChange).toHaveBeenCalledWith("only")
  })

  it("works with two options", () => {
    const onChange = vi.fn()
    render(
      <ToggleButtonGroup
        value="x"
        onChange={onChange}
        options={[
          { key: "x", label: "X" },
          { key: "y", label: "Y" },
        ]}
        accentColor={ACCENT}
      />,
    )
    const radios = screen.getAllByRole("radio")
    expect(radios).toHaveLength(2)
    fireEvent.keyDown(radios[0], { key: "ArrowRight" })
    expect(onChange).toHaveBeenCalledWith("y")
    onChange.mockClear()
    fireEvent.keyDown(radios[0], { key: "ArrowLeft" })
    expect(onChange).toHaveBeenCalledWith("y")
  })

  it("unrelated keys do not trigger onChange", () => {
    const { props } = renderToggle({ value: "a" })
    const radios = screen.getAllByRole("radio")
    fireEvent.keyDown(radios[0], { key: "Enter" })
    fireEvent.keyDown(radios[0], { key: "Tab" })
    fireEvent.keyDown(radios[0], { key: "a" })
    expect(props.onChange).not.toHaveBeenCalled()
  })

  it("click still works as before", () => {
    const { props } = renderToggle({ value: "a" })
    fireEvent.click(screen.getByText("Gamma"))
    expect(props.onChange).toHaveBeenCalledWith("c")
  })
})
