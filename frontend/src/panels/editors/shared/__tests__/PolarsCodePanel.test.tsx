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

describe("PolarsCodePanel trust statement", () => {
  it("states that node code runs as trusted project code (ENG-T04)", () => {
    render(
      <PolarsCodePanel config={{ code: "df = rows" }} onUpdate={vi.fn()} inputSources={[]} hint="assign to df" />,
    )
    expect(screen.getByTestId("polars-trust-note")).toHaveTextContent(
      "Runs as trusted project code with the privileges of the process running haute.",
    )
  })
})
