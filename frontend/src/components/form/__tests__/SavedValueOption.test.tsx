import { afterEach, describe, expect, it } from "vitest"
import { cleanup, render, screen } from "@testing-library/react"
import SavedValueOption from "../SavedValueOption"

function renderSelect(value: string, options: string[]) {
  render(
    <select aria-label="Column" value={value} onChange={() => {}}>
      <option value="">Select...</option>
      <SavedValueOption value={value} options={options} />
      {options.map((option) => <option key={option} value={option}>{option}</option>)}
    </select>,
  )
  return screen.getByRole("combobox", { name: "Column" }) as HTMLSelectElement
}

describe("SavedValueOption", () => {
  afterEach(cleanup)

  it("shows the saved value while the options are not known", () => {
    const select = renderSelect("premium", [])
    expect(select).toHaveValue("premium")
    expect(select.selectedOptions[0].textContent).toBe("premium")
  })

  it("marks a saved value the known options lack", () => {
    const select = renderSelect("premium", ["volume"])
    expect(select.selectedOptions[0].textContent).toBe("premium (not in input)")
  })

  it("adds nothing when the options hold the value or nothing is saved", () => {
    expect(renderSelect("volume", ["volume"]).options).toHaveLength(2)
    cleanup()
    expect(renderSelect("", ["volume"]).options).toHaveLength(2)
  })
})
