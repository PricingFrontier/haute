import { cleanup, render, screen } from "@testing-library/react"
import { ReactFlowProvider } from "@xyflow/react"
import { afterEach, describe, expect, it } from "vitest"
import FramePortRows from "../../nodes/FramePortRows"

afterEach(cleanup)

function renderRows(
  direction: "source" | "target",
  ports = [
    { id: "frame-a", label: "frame_a" },
    { id: "frame-b", label: "frame_b" },
  ],
) {
  return render(
    <ReactFlowProvider>
      <FramePortRows
        ports={ports}
        direction={direction}
        accent="#56B4E9"
        testIdPrefix="shared"
      />
    </ReactFlowProvider>,
  )
}

describe("FramePortRows", () => {
  it("renders ordered source rows with API-input typography and row-owned handles", () => {
    renderRows("source")

    const rows = screen.getAllByTestId(/^shared-frame-row-/)
    expect(rows.map((row) => row.textContent)).toEqual(["frame_a", "frame_b"])

    const firstLabel = screen.getByTestId("shared-body-label-frame-a")
    expect(firstLabel).toHaveClass(
      "font-semibold",
      "text-[13px]",
      "leading-tight",
    )
    expect(firstLabel).toHaveAttribute("title", "frame_a")

    const firstHandle = rows[0].querySelector('[data-handleid="frame-a"]')
    const secondHandle = rows[1].querySelector('[data-handleid="frame-b"]')
    expect(firstHandle).toHaveClass("react-flow__handle-right")
    expect(secondHandle).toHaveClass("react-flow__handle-right")
    expect(firstHandle).toHaveStyle({ top: "50%" })
    expect(secondHandle).toHaveStyle({ top: "50%" })
  })

  it("renders target handles immediately beside left-aligned frame labels", () => {
    renderRows("target")

    const firstRow = screen.getByTestId("shared-frame-row-frame-a")
    const firstHandle = firstRow.querySelector('[data-handleid="frame-a"]')
    const firstLabel = screen.getByTestId("shared-body-label-frame-a")

    expect(firstHandle).toHaveClass("react-flow__handle-left")
    expect(firstHandle).toHaveStyle({ top: "50%" })
    expect(firstLabel).toHaveClass("text-left")
  })

  it("keeps duplicate display labels independent through stable handle ids", () => {
    renderRows("source", [
      { id: "source-a:quote", label: "quote" },
      { id: "source-b:quote", label: "quote" },
    ])

    expect(screen.getAllByText("quote")).toHaveLength(2)
    expect(
      screen
        .getByTestId("shared-frame-row-source-a:quote")
        .querySelector('[data-handleid="source-a:quote"]'),
    ).toBeTruthy()
    expect(
      screen
        .getByTestId("shared-frame-row-source-b:quote")
        .querySelector('[data-handleid="source-b:quote"]'),
    ).toBeTruthy()
  })
})
