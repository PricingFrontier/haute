import { describe, it, expect, vi, afterEach } from "vitest"
import { render, screen, cleanup } from "@testing-library/react"

const { stepsEditorProps } = vi.hoisted(() => ({ stepsEditorProps: [] as Record<string, unknown>[] }))

vi.mock("../../polarsSteps/PolarsStepsEditor", () => ({
  default: (props: Record<string, unknown>) => {
    stepsEditorProps.push(props)
    return <div data-testid="polars-steps-editor" />
  },
}))

vi.mock("../../CodeEditor", () => ({
  CodeEditor: ({ defaultValue }: { defaultValue: string }) => (
    <textarea data-testid="code-editor" defaultValue={defaultValue} />
  ),
}))

import SteppedCodePane from "../SteppedCodePane"

afterEach(() => {
  cleanup()
  stepsEditorProps.length = 0
})

describe("SteppedCodePane", () => {
  it.each([
    ["input", ["quotes"]],
    ["frame", []],
  ] as const)("renders the step builder for a steps list in %s mode, passing the surface through", (start, inputNames) => {
    const onReplace = vi.fn()
    render(
      <SteppedCodePane
        config={{ steps: [] }}
        onUpdate={vi.fn()}
        onReplaceConfig={onReplace}
        inputSources={[{ sourceNodeId: "q", name: "quotes", sourceLabel: "Quotes", edgeId: "e1" }]}
        inputNames={[...inputNames]}
        start={start}
        codeHint="assign to df"
        runError="boom"
      />,
    )
    expect(screen.getByTestId("polars-steps-editor")).toBeInTheDocument()
    expect(screen.queryByText("Polars Code")).not.toBeInTheDocument()
    expect(stepsEditorProps.at(-1)).toMatchObject({
      start,
      inputNames: [...inputNames],
      runError: "boom",
      onReplaceConfig: onReplace,
    })
  })

  it("renders the code box with its hint when there is no steps list", () => {
    render(
      <SteppedCodePane
        config={{ code: "df = df.head(2)" }}
        onUpdate={vi.fn()}
        inputSources={[]}
        inputNames={[]}
        start="frame"
        codeHint="df = the opened input snapshot"
      />,
    )
    expect(screen.getByText("Polars Code")).toBeInTheDocument()
    expect(screen.getByText("df = the opened input snapshot")).toBeInTheDocument()
    expect((screen.getByTestId("code-editor") as HTMLTextAreaElement).defaultValue).toBe("df = df.head(2)")
    expect(screen.queryByTestId("polars-steps-discarded")).not.toBeInTheDocument()
    expect(stepsEditorProps).toHaveLength(0)
  })

  it("shows the discard notice above the code box after steps were discarded on load", () => {
    render(
      <SteppedCodePane
        config={{ code: "df = df.head(3)", _steps_discarded: "Steps were discarded because the function body no longer matches the rendered steps." }}
        onUpdate={vi.fn()}
        inputSources={[]}
        inputNames={[]}
        start="frame"
        codeHint="assign to df"
      />,
    )
    expect(screen.getByTestId("polars-steps-discarded")).toHaveTextContent("Steps were discarded")
    expect(screen.getByText("Polars Code")).toBeInTheDocument()
  })
})
