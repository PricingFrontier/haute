/**
 * Render tests for TransformEditor.
 *
 * Tests: label, hint text for empty/present input sources, input sources bar.
 */
import { describe, it, expect, vi, afterEach } from "vitest"
import { render, screen, cleanup } from "@testing-library/react"
import TransformEditor from "../../panels/editors/TransformEditor"

vi.mock("../../panels/editors/_shared", async () => {
  const actual = await vi.importActual("../../panels/editors/_shared")
  return {
    ...(actual as Record<string, unknown>),
  }
})

vi.mock("../../panels/editors/CodeEditor", () => ({
  CodeEditor: ({ defaultValue, onChange, placeholder }: { defaultValue: string; onChange?: (v: string) => void; placeholder?: string }) => (
    <textarea
      data-testid="code-editor"
      defaultValue={defaultValue}
      onChange={(e) => onChange?.(e.target.value)}
      placeholder={placeholder}
    />
  ),
}))

afterEach(cleanup)

describe("TransformEditor", () => {
  it("renders Polars Code label", () => {
    render(
      <TransformEditor config={{}} onUpdate={vi.fn()} inputSources={[]} />,
    )
    expect(screen.getByText("Polars Code")).toBeTruthy()
  })

  it('shows "assign to df" hint when no input sources', () => {
    render(
      <TransformEditor config={{}} onUpdate={vi.fn()} inputSources={[]} />,
    )
    expect(screen.getByText("assign to df")).toBeTruthy()
  })

  it('shows "use input names" hint when input sources present', () => {
    const inputs = [
      { sourceNodeId: "test-source", varName: "claims", sourceLabel: "Claims Data", edgeId: "e1" },
    ]
    render(
      <TransformEditor config={{}} onUpdate={vi.fn()} inputSources={inputs} />,
    )
    expect(screen.getByText("use input names")).toBeTruthy()
  })

  it("renders input sources bar showing connected variable names", () => {
    const inputs = [
      { sourceNodeId: "test-source", varName: "claims", sourceLabel: "Claims Data", edgeId: "e1" },
      { sourceNodeId: "test-source", varName: "policies", sourceLabel: "Policy Data", edgeId: "e2" },
    ]
    render(
      <TransformEditor config={{}} onUpdate={vi.fn()} inputSources={inputs} />,
    )
    expect(screen.getByText("claims")).toBeTruthy()
    expect(screen.getByText("policies")).toBeTruthy()
    // Multiple inputs should show "Inputs" (plural)
    expect(screen.getByText("Inputs")).toBeTruthy()
  })

  it("passes config.code as default value to code editor", () => {
    render(
      <TransformEditor config={{ code: "df = claims.filter(pl.col('amount') > 0)" }} onUpdate={vi.fn()} inputSources={[]} />,
    )
    const editor = screen.getByTestId("code-editor") as HTMLTextAreaElement
    expect(editor.defaultValue).toBe("df = claims.filter(pl.col('amount') > 0)")
  })

  it("shows return df hint below code editor", () => {
    render(
      <TransformEditor config={{}} onUpdate={vi.fn()} inputSources={[]} />,
    )
    expect(screen.getByText("return df")).toBeTruthy()
  })

  it("passes empty string as default value when config.code is absent", () => {
    render(
      <TransformEditor config={{}} onUpdate={vi.fn()} inputSources={[]} />,
    )
    const editor = screen.getByTestId("code-editor") as HTMLTextAreaElement
    expect(editor.defaultValue).toBe("")
  })

  it("does not synthesize input alias scaffold when inputs are connected", () => {
    const inputs = [
      { sourceNodeId: "test-source", varName: "claims", sourceLabel: "Claims Data", edgeId: "e1" },
    ]
    render(
      <TransformEditor config={{}} onUpdate={vi.fn()} inputSources={inputs} />,
    )
    const editor = screen.getByTestId("code-editor") as HTMLTextAreaElement
    expect(editor.defaultValue).toBe("")
    expect(editor.defaultValue).not.toContain("df = claims")
  })

  it("shows single input source name with 'Input' singular label", () => {
    const inputs = [
      { sourceNodeId: "test-source", varName: "quotes", sourceLabel: "Quotes Data", edgeId: "e1" },
    ]
    render(
      <TransformEditor config={{}} onUpdate={vi.fn()} inputSources={inputs} />,
    )
    expect(screen.getByText("quotes")).toBeTruthy()
    expect(screen.getByText("Input")).toBeTruthy()
  })
})
