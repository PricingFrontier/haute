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
  CodeEditor: ({ defaultValue, availableColumns }: { defaultValue: string; availableColumns?: string[] }) => (
    <textarea
      data-testid="code-editor"
      data-available-columns={JSON.stringify(availableColumns ?? [])}
      defaultValue={defaultValue}
    />
  ),
}))

const INPUT_COLUMNS = [
  { name: "policy_id", dtype: "String" },
  { name: "exposure", dtype: "Float64" },
]
// The node's own columns repeat its input columns (one with a type its code changed).
const NODE_COLUMNS = [
  { name: "policy_id", dtype: "String" },
  { name: "exposure", dtype: "Int64" },
  { name: "prediction", dtype: "Float64" },
]

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

  it("completes the input columns, then the node's own columns, in the code box", () => {
    render(
      <SteppedCodePane
        config={{ code: "df = df" }}
        onUpdate={vi.fn()}
        inputSources={[]}
        inputNames={[]}
        start="frame"
        codeHint="Post-processing Code (optional)"
        upstreamColumns={INPUT_COLUMNS}
        nodeColumns={NODE_COLUMNS}
      />,
    )
    const editor = screen.getByTestId("code-editor") as HTMLTextAreaElement
    expect(JSON.parse(editor.dataset.availableColumns ?? "")).toEqual(["policy_id", "exposure", "prediction"])
  })

  it("completes the node's own columns when it has no inputs", () => {
    render(
      <SteppedCodePane
        config={{ code: "" }}
        onUpdate={vi.fn()}
        inputSources={[]}
        inputNames={[]}
        start="frame"
        codeHint="df = the opened input snapshot"
        upstreamColumns={[]}
        nodeColumns={NODE_COLUMNS}
      />,
    )
    const editor = screen.getByTestId("code-editor") as HTMLTextAreaElement
    expect(JSON.parse(editor.dataset.availableColumns ?? "")).toEqual(["policy_id", "exposure", "prediction"])
  })

  it("starts a frame-mode step list from the same columns, each with its input type", () => {
    render(
      <SteppedCodePane
        config={{ steps: [] }}
        onUpdate={vi.fn()}
        inputSources={[]}
        inputNames={[]}
        start="frame"
        codeHint="Post-processing Code (optional)"
        upstreamColumns={INPUT_COLUMNS}
        nodeColumns={NODE_COLUMNS}
      />,
    )
    expect(stepsEditorProps.at(-1)?.frameColumns).toEqual([
      { name: "policy_id", dtype: "String" },
      { name: "exposure", dtype: "Float64" },
      { name: "prediction", dtype: "Float64" },
    ])
  })

  it("keeps the code columns' identity while an edit leaves both column lists alone", () => {
    const pane = (steps: unknown[]) => (
      <SteppedCodePane
        config={{ steps }}
        onUpdate={vi.fn()}
        inputSources={[]}
        inputNames={[]}
        start="frame"
        codeHint="Post-processing Code (optional)"
        upstreamColumns={INPUT_COLUMNS}
        nodeColumns={NODE_COLUMNS}
      />
    )
    const { rerender } = render(pane([]))
    const first = stepsEditorProps.at(-1)?.frameColumns
    expect(first).toHaveLength(3)

    rerender(pane([{ id: "s1", kind: "limit", n: 5 }]))

    expect(stepsEditorProps.at(-1)?.frameColumns).toBe(first)
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
