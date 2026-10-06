import { afterEach, describe, expect, it } from "vitest"
import { cleanup, render, screen } from "@testing-library/react"
import ExploreTableHead from "../ExploreTableHead"

afterEach(cleanup)

function renderHead(props: Parameters<typeof ExploreTableHead>[0]) {
  return render(
    <table>
      <ExploreTableHead {...props} />
    </table>,
  )
}

describe("ExploreTableHead", () => {
  it("renders one column header per label, rich labels included", () => {
    renderHead({ labels: ["Field", <span key="d">Distinct</span>, "Null %"] })
    const headers = screen.getAllByRole("columnheader")
    expect(headers.map((header) => header.textContent)).toEqual(["Field", "Distinct", "Null %"])
    headers.forEach((header) => {
      expect(header).toHaveAttribute("scope", "col")
      expect(header).toHaveClass("uppercase", "text-left", "px-2", "py-1.5")
      expect(header.style.position).toBe("")
    })
  })

  it("renders an empty label as an unlabelled, hidden spacer column", () => {
    const { container } = renderHead({ labels: ["", "Field"] })
    const [spacer, field] = Array.from(container.querySelectorAll("th"))
    expect(spacer).toHaveAttribute("aria-hidden", "true")
    expect(spacer).not.toHaveAttribute("scope")
    expect(field).toHaveAttribute("scope", "col")
    expect(screen.getAllByRole("columnheader")).toHaveLength(1)
  })

  it("tightens the padding when dense and pins the row when sticky", () => {
    const { unmount } = renderHead({ labels: ["Level"], dense: true })
    expect(screen.getByRole("columnheader")).toHaveClass("py-1")
    expect(screen.getByRole("columnheader")).not.toHaveClass("py-1.5")
    unmount()

    renderHead({ labels: ["Name"], sticky: true })
    const header = screen.getByRole("columnheader")
    expect(header.style.position).toBe("sticky")
    expect(header.style.top).toBe("0px")
    expect(header.style.background).toBe("var(--bg-elevated)")
  })
})
