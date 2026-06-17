import { describe, it, expect, afterEach } from "vitest"
import { render, screen, cleanup } from "@testing-library/react"
import Tooltip from "../Tooltip"

describe("Tooltip", () => {
  afterEach(cleanup)

  it("renders the child and the label (role=tooltip)", () => {
    render(
      <Tooltip label="Commit hash explanation">
        <button>hash</button>
      </Tooltip>,
    )
    expect(screen.getByText("hash")).toBeInTheDocument()
    expect(screen.getByRole("tooltip")).toHaveTextContent("Commit hash explanation")
  })

  it("supports the bottom side without throwing", () => {
    render(
      <Tooltip label="below" side="bottom">
        <span>anchor</span>
      </Tooltip>,
    )
    expect(screen.getByRole("tooltip")).toHaveTextContent("below")
  })
})
