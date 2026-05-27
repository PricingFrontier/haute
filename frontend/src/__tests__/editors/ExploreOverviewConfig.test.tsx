import { afterEach, describe, expect, it, vi } from "vitest"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import ExploreOverviewConfig from "../../panels/editors/ExploreOverviewConfig"

afterEach(cleanup)

const SNAPSHOT_TOGGLE = /dataset snapshot/i
const SCHEMA_TOGGLE = /^schema$/i
const NUMERIC_TOGGLE = /^numeric summary$/i
const CATEGORICAL_TOGGLE = /^categorical summary$/i
const QUALITY_TOGGLE = /^data quality$/i

describe("ExploreOverviewConfig", () => {
  it("renders the overview card toggles", () => {
    render(<ExploreOverviewConfig config={{}} onUpdate={vi.fn()} />)

    expect(screen.getByRole("checkbox", { name: SNAPSHOT_TOGGLE })).toHaveAttribute("aria-checked", "false")
    expect(screen.getByRole("checkbox", { name: SCHEMA_TOGGLE })).toHaveAttribute("aria-checked", "false")
    expect(screen.getByRole("checkbox", { name: NUMERIC_TOGGLE })).toHaveAttribute("aria-checked", "false")
    expect(screen.getByRole("checkbox", { name: CATEGORICAL_TOGGLE })).toHaveAttribute("aria-checked", "false")
    expect(screen.getByRole("checkbox", { name: QUALITY_TOGGLE })).toHaveAttribute("aria-checked", "false")
  })

  it("writes new overview keys when enabling cards", () => {
    const onUpdate = vi.fn()
    render(<ExploreOverviewConfig config={{}} onUpdate={onUpdate} />)

    fireEvent.click(screen.getByRole("checkbox", { name: SNAPSHOT_TOGGLE }))

    expect(onUpdate).toHaveBeenCalledTimes(1)
    expect(onUpdate).toHaveBeenCalledWith("overview", { dataset_snapshot: true })
  })

  it("preserves unknown overview keys while toggling new cards", () => {
    const onUpdate = vi.fn()
    render(<ExploreOverviewConfig config={{ overview: { future_card: true } }} onUpdate={onUpdate} />)

    fireEvent.click(screen.getByRole("checkbox", { name: QUALITY_TOGGLE }))

    expect(onUpdate).toHaveBeenCalledTimes(1)
    expect(onUpdate).toHaveBeenCalledWith("overview", {
      future_card: true,
      data_quality: true,
    })
  })

  it("drops disabled card keys from overview", () => {
    const onUpdate = vi.fn()
    render(
      <ExploreOverviewConfig
        config={{
          overview: {
            dataset_snapshot: true,
            schema: true,
            numeric_summary: true,
            categorical_summary: true,
            data_quality: true,
          },
        }}
        onUpdate={onUpdate}
      />,
    )

    fireEvent.click(screen.getByRole("checkbox", { name: SCHEMA_TOGGLE }))

    expect(onUpdate).toHaveBeenCalledTimes(1)
    expect(onUpdate).toHaveBeenCalledWith("overview", {
      dataset_snapshot: true,
      numeric_summary: true,
      categorical_summary: true,
      data_quality: true,
    })
  })

  it("drops numeric summary when disabling that card", () => {
    const onUpdate = vi.fn()
    render(<ExploreOverviewConfig config={{ overview: { numeric_summary: true } }} onUpdate={onUpdate} />)

    fireEvent.click(screen.getByRole("checkbox", { name: NUMERIC_TOGGLE }))

    expect(onUpdate).toHaveBeenCalledTimes(1)
    expect(onUpdate).toHaveBeenCalledWith("overview", {})
  })

  it("writes categorical summary when enabling that card", () => {
    const onUpdate = vi.fn()
    render(<ExploreOverviewConfig config={{}} onUpdate={onUpdate} />)

    fireEvent.click(screen.getByRole("checkbox", { name: CATEGORICAL_TOGGLE }))

    expect(onUpdate).toHaveBeenCalledTimes(1)
    expect(onUpdate).toHaveBeenCalledWith("overview", { categorical_summary: true })
  })

  it("reads schema as the Schema card only", () => {
    const onUpdate = vi.fn()
    render(<ExploreOverviewConfig config={{ overview: { schema: true } }} onUpdate={onUpdate} />)

    expect(screen.getByRole("checkbox", { name: SCHEMA_TOGGLE })).toHaveAttribute("aria-checked", "true")
    expect(screen.getByRole("checkbox", { name: QUALITY_TOGGLE })).toHaveAttribute("aria-checked", "false")

    fireEvent.click(screen.getByRole("checkbox", { name: SCHEMA_TOGGLE }))

    expect(onUpdate).toHaveBeenCalledTimes(1)
    expect(onUpdate).toHaveBeenCalledWith("overview", {})
  })

  it("exposes descriptions to assistive tech", () => {
    render(<ExploreOverviewConfig config={{}} onUpdate={vi.fn()} />)

    expect(screen.getByRole("checkbox", { name: SNAPSHOT_TOGGLE })).toHaveAccessibleDescription(
      /Rows, source, upstream node/i,
    )
    expect(screen.getByRole("checkbox", { name: SCHEMA_TOGGLE })).toHaveAccessibleDescription(
      /Field-level types/i,
    )
    expect(screen.getByRole("checkbox", { name: NUMERIC_TOGGLE })).toHaveAccessibleDescription(
      /Numeric fields only/i,
    )
    expect(screen.getByRole("checkbox", { name: CATEGORICAL_TOGGLE })).toHaveAccessibleDescription(
      /Non-numeric fields/i,
    )
    expect(screen.getByRole("checkbox", { name: QUALITY_TOGGLE })).toHaveAccessibleDescription(
      /Missing, constant, negative/i,
    )
  })

  it("exposes a data-testid container for outer panel wiring", () => {
    render(<ExploreOverviewConfig config={{}} onUpdate={vi.fn()} />)
    expect(screen.getByTestId("explore-overview-config")).toBeTruthy()
  })
})
