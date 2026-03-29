import { describe, it, expect, vi, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup } from "@testing-library/react"
import ConfigCheckbox from "../../../components/form/ConfigCheckbox"

afterEach(cleanup)

describe("ConfigCheckbox", () => {
  it("renders with label text", () => {
    render(<ConfigCheckbox checked={false} onChange={vi.fn()} label="Enable feature" />)
    expect(screen.getByText("Enable feature")).toBeTruthy()
  })

  it("checkbox reflects checked=true", () => {
    render(<ConfigCheckbox checked={true} onChange={vi.fn()} label="Toggle" />)
    const cb = screen.getByRole("checkbox") as HTMLInputElement
    expect(cb.checked).toBe(true)
  })

  it("checkbox reflects checked=false", () => {
    render(<ConfigCheckbox checked={false} onChange={vi.fn()} label="Toggle" />)
    const cb = screen.getByRole("checkbox") as HTMLInputElement
    expect(cb.checked).toBe(false)
  })

  it("calls onChange with true when clicking unchecked checkbox", () => {
    const onChange = vi.fn()
    render(<ConfigCheckbox checked={false} onChange={onChange} label="Toggle" />)
    fireEvent.click(screen.getByRole("checkbox"))
    expect(onChange).toHaveBeenCalledWith(true)
  })

  it("calls onChange with false when clicking checked checkbox", () => {
    const onChange = vi.fn()
    render(<ConfigCheckbox checked={true} onChange={onChange} label="Toggle" />)
    fireEvent.click(screen.getByRole("checkbox"))
    expect(onChange).toHaveBeenCalledWith(false)
  })

  it("disabled checkbox is disabled", () => {
    render(<ConfigCheckbox checked={false} onChange={vi.fn()} label="Toggle" disabled />)
    const cb = screen.getByRole("checkbox") as HTMLInputElement
    expect(cb.disabled).toBe(true)
  })

  it("custom id is applied to checkbox", () => {
    render(<ConfigCheckbox checked={false} onChange={vi.fn()} label="Toggle" id="my-cb" />)
    const cb = screen.getByRole("checkbox") as HTMLInputElement
    expect(cb.id).toBe("my-cb")
    expect(screen.getByLabelText("Toggle")).toBe(cb)
  })

  it("disabled checkbox has disabled attribute preventing interaction", () => {
    const onChange = vi.fn()
    render(<ConfigCheckbox checked={false} onChange={onChange} label="Toggle" disabled />)
    const cb = screen.getByRole("checkbox") as HTMLInputElement
    expect(cb.disabled).toBe(true)
    expect(cb.className).toContain("disabled:cursor-not-allowed")
  })

  it("label styling changes with disabled state", () => {
    const { rerender } = render(<ConfigCheckbox checked={false} onChange={vi.fn()} label="Toggle" />)
    const label = screen.getByText("Toggle")
    expect(label.style.color).toBe("var(--text-secondary)")
    expect(label.style.cursor).toBe("pointer")

    rerender(<ConfigCheckbox checked={false} onChange={vi.fn()} label="Toggle" disabled />)
    expect(label.style.color).toBe("var(--text-muted)")
    expect(label.style.cursor).toBe("not-allowed")
  })
})
