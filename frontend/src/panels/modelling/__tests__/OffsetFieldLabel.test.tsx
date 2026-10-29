import { afterEach, describe, expect, it } from "vitest"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"

import { OffsetFieldLabel } from "../OffsetFieldLabel"

afterEach(cleanup)

function hoverHelp(): string {
  render(<OffsetFieldLabel />)
  fireEvent.mouseEnter(screen.getByTestId("offset-help"))
  return screen.getByRole("tooltip").textContent ?? ""
}

// BUG-28: the help named only "a CatBoost Poisson or Tweedie loss" as an exposure, and
// nothing about the families that refuse an offset with Logloss.
describe("OffsetFieldLabel", () => {
  it("names every loss that takes the offset as an exposure multiplier", () => {
    expect(hoverHelp()).toContain(
      "Under a log link (a log-link GLM, or a Poisson, Gamma or Tweedie loss) it is a positive multiplier",
    )
  })

  it("names the families that take no offset with the Logloss loss", () => {
    expect(hoverHelp()).toContain("XGBoost, LightGBM, t-boost and EBM take no offset with the Logloss loss")
  })

  it("says a missing exposure scores as a full policy term and an added offset is required", () => {
    const help = hoverHelp()
    expect(help).toContain("a missing or empty exposure is an exposure of one")
    expect(help).toContain("Under any other link it is added to the prediction")
    expect(help).toContain("must be present when scoring")
  })
})
