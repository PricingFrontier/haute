import { describe, it, expect, vi, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup } from "@testing-library/react"
import { GenerateBandsDialog } from "../GenerateBandsDialog"

const ACCENT = "#f97316"

describe("GenerateBandsDialog", () => {
  afterEach(cleanup)

  it("renders all input fields (no label format field)", () => {
    render(
      <GenerateBandsDialog
        onGenerate={vi.fn()}
        onClose={vi.fn()}
        accentColor={ACCENT}
      />,
    )
    expect(screen.getByLabelText("Start")).toBeInTheDocument()
    expect(screen.getByLabelText("End")).toBeInTheDocument()
    expect(screen.getByLabelText("Step")).toBeInTheDocument()
    // Label format field should NOT exist
    expect(screen.queryByLabelText("Label format")).not.toBeInTheDocument()
  })

  it("Escape cancels the dialog (parity with the Cancel button)", () => {
    const onClose = vi.fn()
    render(
      <GenerateBandsDialog onGenerate={vi.fn()} onClose={onClose} accentColor={ACCENT} />,
    )
    fireEvent.keyDown(document, { key: "Escape" })
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it("pre-fills from dataMin/dataMax when provided", () => {
    render(
      <GenerateBandsDialog
        onGenerate={vi.fn()}
        onClose={vi.fn()}
        accentColor={ACCENT}
        dataMin={10}
        dataMax={100}
      />,
    )
    expect(screen.getByLabelText("Start")).toHaveValue(10)
    expect(screen.getByLabelText("End")).toHaveValue(100)
  })

  it("auto-suggests step for ~10 bands when data range is known", () => {
    render(
      <GenerateBandsDialog
        onGenerate={vi.fn()}
        onClose={vi.fn()}
        accentColor={ACCENT}
        dataMin={0}
        dataMax={100}
      />,
    )
    // Math.ceil((100-0)/10) = 10
    expect(screen.getByLabelText("Step")).toHaveValue(10)
  })

  it("generate button creates correct breakpoints with auto labels", () => {
    const onGenerate = vi.fn()
    render(
      <GenerateBandsDialog
        onGenerate={onGenerate}
        onClose={vi.fn()}
        accentColor={ACCENT}
      />,
    )
    fireEvent.change(screen.getByLabelText("Start"), { target: { value: "0" } })
    fireEvent.change(screen.getByLabelText("End"), { target: { value: "30" } })
    fireEvent.change(screen.getByLabelText("Step"), { target: { value: "10" } })
    fireEvent.click(screen.getByText("Generate"))

    expect(onGenerate).toHaveBeenCalledTimes(1)
    const breakpoints = onGenerate.mock.calls[0][0]
    // Integer boundaries: first band starts at start, subsequent use prev+1
    expect(breakpoints).toEqual([
      { boundary: "10", label: "0–10" },
      { boundary: "20", label: "11–20" },
      { boundary: "30", label: "21–30" },
    ])
  })

  it("validates step > 0", () => {
    const onGenerate = vi.fn()
    render(
      <GenerateBandsDialog
        onGenerate={onGenerate}
        onClose={vi.fn()}
        accentColor={ACCENT}
      />,
    )
    fireEvent.change(screen.getByLabelText("Start"), { target: { value: "0" } })
    fireEvent.change(screen.getByLabelText("End"), { target: { value: "10" } })
    fireEvent.change(screen.getByLabelText("Step"), { target: { value: "0" } })
    fireEvent.click(screen.getByText("Generate"))

    expect(onGenerate).not.toHaveBeenCalled()
    expect(screen.getByText("Step must be greater than 0")).toBeInTheDocument()
  })

  it("validates end > start", () => {
    const onGenerate = vi.fn()
    render(
      <GenerateBandsDialog
        onGenerate={onGenerate}
        onClose={vi.fn()}
        accentColor={ACCENT}
      />,
    )
    fireEvent.change(screen.getByLabelText("Start"), { target: { value: "20" } })
    fireEvent.change(screen.getByLabelText("End"), { target: { value: "10" } })
    fireEvent.change(screen.getByLabelText("Step"), { target: { value: "5" } })
    fireEvent.click(screen.getByText("Generate"))

    expect(onGenerate).not.toHaveBeenCalled()
    expect(screen.getByText("End must be greater than start")).toBeInTheDocument()
  })

  it("cancel button calls onClose", () => {
    const onClose = vi.fn()
    render(
      <GenerateBandsDialog
        onGenerate={vi.fn()}
        onClose={onClose}
        accentColor={ACCENT}
      />,
    )
    fireEvent.click(screen.getByText("Cancel"))
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it("edge case: step larger than range produces single band", () => {
    const onGenerate = vi.fn()
    render(
      <GenerateBandsDialog
        onGenerate={onGenerate}
        onClose={vi.fn()}
        accentColor={ACCENT}
      />,
    )
    fireEvent.change(screen.getByLabelText("Start"), { target: { value: "0" } })
    fireEvent.change(screen.getByLabelText("End"), { target: { value: "5" } })
    fireEvent.change(screen.getByLabelText("Step"), { target: { value: "100" } })
    fireEvent.click(screen.getByText("Generate"))

    expect(onGenerate).toHaveBeenCalledTimes(1)
    const breakpoints = onGenerate.mock.calls[0][0]
    // Step > range: end is included as the only boundary
    expect(breakpoints).toEqual([
      { boundary: "5", label: "0–5" },
    ])
  })

  it("dialog renders with box shadow", () => {
    const { container } = render(
      <GenerateBandsDialog
        onGenerate={vi.fn()}
        onClose={vi.fn()}
        accentColor={ACCENT}
      />,
    )
    const dialog = container.querySelector("[role='dialog']") as HTMLElement
    expect(dialog.style.boxShadow).toBeTruthy()
  })
})
