import { afterEach, describe, expect, it, vi } from "vitest"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"

import { GenerateBandsDialog } from "../GenerateBandsDialog"

afterEach(cleanup)

describe("GenerateBandsDialog", () => {
  it("starts from the settings it is given rather than the data's range", () => {
    const onGenerate = vi.fn()
    render(
      <GenerateBandsDialog
        onGenerate={onGenerate}
        onClose={vi.fn()}
        accentColor="#22d3ee"
        dataMin={0}
        dataMax={100}
        initial={{ start: 4000, end: 7000, step: 1200 }}
      />,
    )
    expect(screen.getByLabelText("Start")).toHaveValue(4000)
    expect(screen.getByLabelText("End")).toHaveValue(7000)
    expect(screen.getByLabelText("Step")).toHaveValue(1200)

    fireEvent.click(screen.getByRole("button", { name: "Generate" }))
    expect(onGenerate).toHaveBeenCalledWith([
      { boundary: "5200", label: "4000–5200" },
      { boundary: "6400", label: "5201–6400" },
      { boundary: "7000", label: "6401–7000" },
    ])
  })

  it("starts from the data's range when there are no settings to start from", () => {
    render(
      <GenerateBandsDialog onGenerate={vi.fn()} onClose={vi.fn()} accentColor="#22d3ee" dataMin={0} dataMax={100} />,
    )
    expect(screen.getByLabelText("Start")).toHaveValue(0)
    expect(screen.getByLabelText("End")).toHaveValue(100)
    expect(screen.getByLabelText("Step")).toHaveValue(10)
  })

  describe("on a date column", () => {
    it("asks for Start and End dates and a Step of whole units, starting from the data's dates in months", () => {
      render(
        <GenerateBandsDialog
          temporal
          rightClosed
          onGenerate={vi.fn()}
          onClose={vi.fn()}
          accentColor="#22d3ee"
          dataMin="2024-01-05"
          dataMax="2024-03-20"
        />,
      )
      expect(screen.getByLabelText("Start")).toHaveAttribute("type", "date")
      expect(screen.getByLabelText("Start")).toHaveValue("2024-01-05")
      expect(screen.getByLabelText("End")).toHaveAttribute("type", "date")
      expect(screen.getByLabelText("End")).toHaveValue("2024-03-20")
      expect(screen.getByLabelText("Step")).toHaveValue(1)
      expect(screen.getByLabelText("Step unit")).toHaveValue("months")
      expect(
        Array.from((screen.getByLabelText("Step unit") as HTMLSelectElement).options).map((option) => option.value),
      ).toEqual(["days", "weeks", "months", "years"])
    })

    it("generates bands stepping whole months, each up to the day before the next starts", () => {
      const onGenerate = vi.fn()
      render(<GenerateBandsDialog temporal rightClosed onGenerate={onGenerate} onClose={vi.fn()} accentColor="#22d3ee" />)
      fireEvent.change(screen.getByLabelText("Start"), { target: { value: "2024-01-01" } })
      fireEvent.change(screen.getByLabelText("End"), { target: { value: "2024-03-15" } })
      fireEvent.click(screen.getByRole("button", { name: "Generate" }))
      expect(onGenerate).toHaveBeenCalledWith([
        { boundary: "2024-01-31", label: "2024-01-01–2024-01-31" },
        { boundary: "2024-02-29", label: "2024-02-01–2024-02-29" },
        { boundary: "2024-03-15", label: "2024-03-01–2024-03-15" },
      ])
    })

    it("starts from the settings it is given, and ends each band before its Up to when left-closed", () => {
      const onGenerate = vi.fn()
      render(
        <GenerateBandsDialog
          temporal
          rightClosed={false}
          onGenerate={onGenerate}
          onClose={vi.fn()}
          accentColor="#22d3ee"
          dataMin="2020-06-01"
          dataMax="2025-06-01"
          initial={{ start: "2024-01-01", end: "2024-01-28", step: 2, unit: "weeks" }}
        />,
      )
      expect(screen.getByLabelText("Start")).toHaveValue("2024-01-01")
      expect(screen.getByLabelText("End")).toHaveValue("2024-01-28")
      expect(screen.getByLabelText("Step")).toHaveValue(2)
      expect(screen.getByLabelText("Step unit")).toHaveValue("weeks")
      fireEvent.change(screen.getByLabelText("Step unit"), { target: { value: "days" } })
      fireEvent.change(screen.getByLabelText("Step"), { target: { value: "14" } })
      fireEvent.click(screen.getByRole("button", { name: "Generate" }))
      expect(onGenerate).toHaveBeenCalledWith([
        { boundary: "2024-01-15", label: "2024-01-01–2024-01-14" },
        { boundary: "2024-01-29", label: "2024-01-15–2024-01-28" },
      ])
    })

    it("says why it cannot generate instead of generating", () => {
      const onGenerate = vi.fn()
      render(
        <GenerateBandsDialog
          temporal
          rightClosed
          onGenerate={onGenerate}
          onClose={vi.fn()}
          accentColor="#22d3ee"
          initial={{ start: "2024-01-01", end: "2024-12-31", step: 1, unit: "months" }}
        />,
      )
      fireEvent.change(screen.getByLabelText("Step"), { target: { value: "1.5" } })
      fireEvent.click(screen.getByRole("button", { name: "Generate" }))
      expect(screen.getByText("Step must be a whole number of at least 1")).toBeInTheDocument()
      expect(onGenerate).not.toHaveBeenCalled()
    })
  })
})
