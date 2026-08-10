/**
 * Render tests for TransformEditor.
 *
 * Tests: label, hints, input sources, and editable starter-code semantics.
 */
import { describe, it, expect, vi, afterEach } from "vitest"
import { render, screen, cleanup, fireEvent } from "@testing-library/react"
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

  it('shows "use input names, assign to df" hint when input sources present', () => {
    const inputs = [
      { sourceNodeId: "test-source", name: "claims", sourceLabel: "Claims Data", edgeId: "e1" },
    ]
    render(
      <TransformEditor config={{}} onUpdate={vi.fn()} inputSources={inputs} />,
    )
    expect(screen.getByText("use input names, assign to df")).toBeTruthy()
  })

  it("renders input sources bar showing connected variable names", () => {
    const inputs = [
      { sourceNodeId: "test-source", name: "claims", sourceLabel: "Claims Data", edgeId: "e1" },
      { sourceNodeId: "test-source", name: "policies", sourceLabel: "Policy Data", edgeId: "e2" },
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
    const inputs = [
      { sourceNodeId: "test-source", name: "claims", sourceLabel: "Claims Data", edgeId: "e1" },
    ]
    render(
      <TransformEditor
        config={{ code: "df = claims.filter(pl.col('amount') > 0)" }}
        onUpdate={vi.fn()}
        inputSources={inputs}
      />,
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
    expect(editor.placeholder).toBe("")
  })

  it("suggests a commented pass-through from the first connected input", () => {
    const onUpdate = vi.fn()
    const inputs = [
      { sourceNodeId: "test-source", name: "claims", sourceLabel: "Claims Data", edgeId: "e1" },
      { sourceNodeId: "other-source", name: "policies", sourceLabel: "Policy Data", edgeId: "e2" },
    ]
    render(
      <TransformEditor config={{}} onUpdate={onUpdate} inputSources={inputs} />,
    )
    const editor = screen.getByTestId("code-editor") as HTMLTextAreaElement
    expect(editor.defaultValue).toBe("# df = claims")
    expect(editor.placeholder).toBe("")
    expect(onUpdate).not.toHaveBeenCalled()
  })

  it("commits the runnable pass-through when the user removes the comment prefix", () => {
    const onUpdate = vi.fn()
    const inputs = [
      { sourceNodeId: "test-source", name: "claims", sourceLabel: "Claims Data", edgeId: "e1" },
    ]
    render(
      <TransformEditor config={{}} onUpdate={onUpdate} inputSources={inputs} />,
    )
    const editor = screen.getByTestId("code-editor") as HTMLTextAreaElement

    fireEvent.change(editor, { target: { value: "df = claims" } })

    expect(onUpdate).toHaveBeenCalledOnce()
    expect(onUpdate).toHaveBeenCalledWith("code", "df = claims")
  })

  it("does not reinsert the starter after an explicit empty code commit", () => {
    const inputs = [
      { sourceNodeId: "test-source", name: "claims", sourceLabel: "Claims Data", edgeId: "e1" },
    ]
    render(
      <TransformEditor config={{ code: "" }} onUpdate={vi.fn()} inputSources={inputs} />,
    )
    const editor = screen.getByTestId("code-editor") as HTMLTextAreaElement
    expect(editor.defaultValue).toBe("")
    expect(editor.placeholder).toBe("")
  })

  it("does not suggest code while any connected input is unresolved", () => {
    const inputs = [
      {
        sourceNodeId: "api-source",
        name: "<unresolved>",
        sourceLabel: "Quote Input",
        edgeId: "e1",
        frameUnresolved: true,
      },
      { sourceNodeId: "other-source", name: "policies", sourceLabel: "Policy Data", edgeId: "e2" },
    ]
    render(
      <TransformEditor config={{}} onUpdate={vi.fn()} inputSources={inputs} />,
    )
    const editor = screen.getByTestId("code-editor") as HTMLTextAreaElement
    expect(editor.defaultValue).toBe("")
  })

  it("does not suggest code when an input uses the reserved df name", () => {
    const inputs = [
      { sourceNodeId: "df-source", name: "df", sourceLabel: "df", edgeId: "e1" },
    ]
    render(
      <TransformEditor config={{}} onUpdate={vi.fn()} inputSources={inputs} />,
    )
    const editor = screen.getByTestId("code-editor") as HTMLTextAreaElement
    expect(editor.defaultValue).toBe("")
  })

  it("shows single input source name with 'Input' singular label", () => {
    const inputs = [
      { sourceNodeId: "test-source", name: "quotes", sourceLabel: "Quotes Data", edgeId: "e1" },
    ]
    render(
      <TransformEditor config={{}} onUpdate={vi.fn()} inputSources={inputs} />,
    )
    expect(screen.getByText("quotes")).toBeTruthy()
    expect(screen.getByText("Input")).toBeTruthy()
  })
})
