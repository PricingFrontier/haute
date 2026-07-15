import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup } from "@testing-library/react"
import ImportsPanel from "../ImportsPanel"

// Mock the CodeEditor (heavy CodeMirror dependency)
vi.mock("../editors/CodeEditor", () => ({
  CodeEditor: ({ defaultValue, onChange, placeholder }: { defaultValue?: string; onChange?: (v: string) => void; placeholder?: string }) => (
    <textarea
      data-testid="code-editor"
      defaultValue={defaultValue}
      onChange={(e) => onChange?.(e.target.value)}
      placeholder={placeholder}
    />
  ),
}))

describe("ImportsPanel", () => {
  const defaultProps = {
    preamble: "from utility.features import *",
    onPreambleChange: vi.fn(),
    onClose: vi.fn(),
  }

  beforeEach(() => {
    defaultProps.onClose.mockClear()
    defaultProps.onPreambleChange.mockClear()
  })

  afterEach(cleanup)

  it("renders header", () => {
    render(<ImportsPanel {...defaultProps} />)
    expect(screen.getByText("Pipeline Imports")).toBeInTheDocument()
  })

  it("renders description text", () => {
    render(<ImportsPanel {...defaultProps} />)
    expect(screen.getByText(/Import statements for utility modules/)).toBeInTheDocument()
  })

  it("renders close button", () => {
    render(<ImportsPanel {...defaultProps} />)
    expect(screen.getByTitle("Close")).toBeInTheDocument()
  })

  it("close button calls onClose", () => {
    render(<ImportsPanel {...defaultProps} />)
    fireEvent.click(screen.getByTitle("Close"))
    expect(defaultProps.onClose).toHaveBeenCalledOnce()
  })

  it("renders preamble in editor", () => {
    render(<ImportsPanel {...defaultProps} />)
    expect(screen.getByTestId("code-editor")).toHaveValue("from utility.features import *")
  })

  it("calls onPreambleChange when edited", () => {
    render(<ImportsPanel {...defaultProps} />)
    fireEvent.change(screen.getByTestId("code-editor"), { target: { value: "import numpy as np" } })
    expect(defaultProps.onPreambleChange).toHaveBeenCalledWith("import numpy as np")
  })

  it("shows always-included imports note", () => {
    render(<ImportsPanel {...defaultProps} />)
    expect(screen.getByText(/import polars as pl/)).toBeInTheDocument()
    expect(screen.getByText(/import haute/)).toBeInTheDocument()
  })
})
