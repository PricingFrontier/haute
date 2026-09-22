import { cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it } from "vitest"
import { ModellingPreview } from "../ModellingPreview"
import { makeTrainResult } from "../../test-utils/factories"
import useUIStore from "../../stores/useUIStore"

const result = makeTrainResult({
  feature_importance: [
    { feature: "age", importance: 10 },
    { feature: "region,code", importance: 5 },
  ],
  ave_per_feature: [
    {
      feature: "age",
      type: "numeric",
      bins: [{ label: "20–30", exposure: 20, avg_actual: 1, avg_predicted: 1.1 }],
    },
    {
      feature: "region,code",
      type: "categorical",
      bins: [{ label: "north", exposure: 10, avg_actual: 2, avg_predicted: 1.8 }],
    },
  ],
  pdp_data: [{ feature: "age", type: "numeric", grid: [{ value: 25, avg_prediction: 1.1 }] }],
})
const data = { result, jobId: "job", nodeLabel: "Frequency", configHash: "hash" }
beforeEach(() => useUIStore.setState({ modellingPreviewHeight: 420 }))
afterEach(cleanup)

describe("Validation workspace", () => {
  it("preserves active pane and feature search through Focus view and Escape", () => {
    render(<ModellingPreview data={data} nodeId="model" />)
    fireEvent.click(screen.getByRole("tab", { name: "AvE" }))
    fireEvent.change(screen.getByRole("textbox", { name: "Search features" }), {
      target: { value: "region" },
    })
    fireEvent.click(screen.getByRole("button", { name: "region,code" }))
    const focus = screen.getByRole("button", { name: "Focus view" })
    focus.focus()
    fireEvent.click(focus)
    const dialog = screen.getByRole("dialog", { name: "Model validation" })
    expect(within(dialog).getByRole("tab", { name: "AvE" })).toHaveAttribute(
      "aria-selected",
      "true",
    )
    expect(within(dialog).getByRole("textbox", { name: "Search features" })).toHaveValue("region")
    expect(within(dialog).getByRole("button", { name: "region,code" })).toHaveAttribute(
      "aria-pressed",
      "true",
    )
    fireEvent.keyDown(document, { key: "Escape" })
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Focus view" })).toHaveFocus()
    expect(screen.getByRole("textbox", { name: "Search features" })).toHaveValue("region")
  })

  it("keeps an unavailable feature selected between AvE and PDP, including names with commas", () => {
    render(<ModellingPreview data={data} nodeId="model" />)
    fireEvent.click(screen.getByRole("tab", { name: "AvE" }))
    fireEvent.click(screen.getByRole("button", { name: "region,code" }))
    fireEvent.click(screen.getByRole("tab", { name: "PDP" }))
    expect(screen.getByText("No PDP data for region,code")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "region,code" })).toHaveAttribute(
      "aria-pressed",
      "true",
    )
    fireEvent.click(screen.getByRole("tab", { name: "AvE" }))
    expect(screen.getByRole("heading", { name: "region,code" })).toBeInTheDocument()
  })

  it("keeps diagnostic provenance visible outside Summary", () => {
    render(
      <ModellingPreview
        data={{
          ...data,
          result: { ...result, diagnostics_set: "development", final_test_rows: 0 },
        }}
        nodeId="model"
      />,
    )
    fireEvent.click(screen.getByRole("tab", { name: "PDP" }))
    expect(screen.getByText(/Diagnostics: Development/)).toHaveTextContent("8,000 rows")
    expect(screen.getByText(/not held-out performance/i)).toBeInTheDocument()
  })

  it("resets feature selection and search when a new training result replaces the current one", () => {
    const { rerender } = render(<ModellingPreview data={data} nodeId="model" />)
    fireEvent.click(screen.getByRole("tab", { name: "AvE" }))
    fireEvent.change(screen.getByRole("textbox", { name: "Search features" }), {
      target: { value: "region" },
    })
    fireEvent.click(screen.getByRole("button", { name: "region,code" }))
    rerender(<ModellingPreview data={{ ...data, result: { ...result } }} nodeId="model" />)
    expect(screen.getByRole("tab", { name: "Summary" })).toHaveAttribute("aria-selected", "true")
    fireEvent.click(screen.getByRole("tab", { name: "AvE" }))
    expect(screen.getByRole("textbox", { name: "Search features" })).toHaveValue("")
    expect(screen.getByRole("button", { name: "age" })).toHaveAttribute("aria-pressed", "true")
  })

  it("remembers the docked height across preview remounts", () => {
    const { unmount } = render(<ModellingPreview data={data} nodeId="model" />)
    const frame = screen.getByTestId("modelling-preview-frame")
    expect(frame.style.height).toBe("420px")
    fireEvent.mouseDown(frame.firstElementChild!, { clientY: 600 })
    fireEvent.mouseMove(document, { clientY: 550 })
    fireEvent.mouseUp(document, { clientY: 550 })
    expect(frame.style.height).toBe("470px")
    unmount()
    render(<ModellingPreview data={data} nodeId="model" />)
    expect(screen.getByTestId("modelling-preview-frame").style.height).toBe("470px")
  })
})
