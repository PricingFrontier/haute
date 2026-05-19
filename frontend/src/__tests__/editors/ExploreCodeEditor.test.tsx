import { afterEach, describe, expect, it, vi } from "vitest"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import ExploreCodeEditor from "../../panels/editors/ExploreCodeEditor"

vi.mock("../../panels/editors/CodeEditor", () => ({
  CodeEditor: ({
    defaultValue,
    onChange,
    availableColumns,
  }: {
    defaultValue: string
    onChange?: (value: string) => void
    availableColumns?: string[]
  }) => (
    <textarea
      data-testid="code-editor"
      data-available-columns={JSON.stringify(availableColumns ?? [])}
      defaultValue={defaultValue}
      onChange={(event) => onChange?.(event.target.value)}
    />
  ),
}))

afterEach(cleanup)

describe("ExploreCodeEditor", () => {
  it("renders the Polars editor chrome for analysis data preparation", () => {
    render(<ExploreCodeEditor config={{}} onUpdate={vi.fn()} inputSources={[]} />)

    expect(screen.getByText("Polars Code")).toBeTruthy()
    expect(screen.getByText("assign to df")).toBeTruthy()
    expect(screen.getByText("return df")).toBeTruthy()
  })

  it("passes config code and upstream columns to the code editor", () => {
    render(
      <ExploreCodeEditor
        config={{ code: "df = df.filter(pl.col('premium') > 0)" }}
        onUpdate={vi.fn()}
        inputSources={[]}
        upstreamColumns={[
          { name: "premium", dtype: "Int64" },
          { name: "region", dtype: "String" },
        ]}
      />,
    )

    const editor = screen.getByTestId("code-editor") as HTMLTextAreaElement
    expect(editor.defaultValue).toBe("df = df.filter(pl.col('premium') > 0)")
    expect(editor.dataset.availableColumns).toBe(JSON.stringify(["premium", "region"]))
  })

  it("updates the code config key when the editor changes", () => {
    const onUpdate = vi.fn()
    render(<ExploreCodeEditor config={{}} onUpdate={onUpdate} inputSources={[]} />)

    fireEvent.change(screen.getByTestId("code-editor"), { target: { value: "df = df.head(10)" } })

    expect(onUpdate).toHaveBeenCalledWith("code", "df = df.head(10)")
  })
})
