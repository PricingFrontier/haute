import { afterEach, describe, expect, it, vi } from "vitest"
import { cleanup, render, screen } from "@testing-library/react"
import PolarsCodePanel from "../PolarsCodePanel"

vi.mock("../../CodeEditor", () => ({
  CodeEditor: () => <div data-testid="code-editor" />,
}))
vi.mock("../../_shared", () => ({
  InputSourcesBar: () => <div data-testid="input-sources" />,
}))

afterEach(() => {
  cleanup()
})

describe("PolarsCodePanel", () => {
  it("renders the editor and code hints without a trust statement", () => {
    render(
      <PolarsCodePanel config={{ code: "df = rows" }} onUpdate={vi.fn()} inputSources={[]} hint="assign to df" />,
    )
    expect(screen.getByTestId("code-editor")).toBeInTheDocument()
    expect(screen.getByText("assign to df")).toBeInTheDocument()
    expect(screen.getByText("return df")).toBeInTheDocument()
    expect(screen.queryByTestId("polars-trust-note")).not.toBeInTheDocument()
  })
})
