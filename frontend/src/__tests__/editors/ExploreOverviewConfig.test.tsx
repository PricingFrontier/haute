/**
 * Render tests for ExploreOverviewConfig.
 *
 * The Overview right-panel pane on an Explore node toggles which cards render
 * in the bottom preview pane. Today there is only the dataset-header card —
 * the toggle writes ``config.overview.dataset_header`` (snake_case to match
 * the backend round-trip in _codegen_builders.py / _config_builder.py).
 *
 * The component must:
 *  - read ``overview.dataset_header`` defaulting to false,
 *  - on enable: write ``overview = { ...prev, dataset_header: true }``,
 *  - on disable: drop the key entirely so the generated .py stays bare,
 *  - preserve any other future overview keys round-trip.
 */
import { describe, it, expect, vi, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup } from "@testing-library/react"
import ExploreOverviewConfig from "../../panels/editors/ExploreOverviewConfig"

afterEach(cleanup)

const TOGGLE_NAME = /dataset header/i

describe("ExploreOverviewConfig", () => {
  it("renders unchecked checkbox row when config has no overview block", () => {
    render(<ExploreOverviewConfig config={{}} onUpdate={vi.fn()} />)
    const toggle = screen.getByRole("checkbox", { name: TOGGLE_NAME })
    expect(toggle.getAttribute("aria-checked")).toBe("false")
  })

  it("renders checked checkbox row when config.overview.dataset_header is true", () => {
    render(
      <ExploreOverviewConfig
        config={{ overview: { dataset_header: true } }}
        onUpdate={vi.fn()}
      />,
    )
    const toggle = screen.getByRole("checkbox", { name: TOGGLE_NAME })
    expect(toggle.getAttribute("aria-checked")).toBe("true")
  })

  it("clicking unchecked checkbox row calls onUpdate with dataset_header: true", () => {
    const onUpdate = vi.fn()
    render(<ExploreOverviewConfig config={{}} onUpdate={onUpdate} />)
    fireEvent.click(screen.getByRole("checkbox", { name: TOGGLE_NAME }))
    expect(onUpdate).toHaveBeenCalledTimes(1)
    expect(onUpdate).toHaveBeenCalledWith("overview", { dataset_header: true })
  })

  it("clicking checked checkbox row drops dataset_header key from overview", () => {
    const onUpdate = vi.fn()
    render(
      <ExploreOverviewConfig
        config={{ overview: { dataset_header: true } }}
        onUpdate={onUpdate}
      />,
    )
    fireEvent.click(screen.getByRole("checkbox", { name: TOGGLE_NAME }))
    expect(onUpdate).toHaveBeenCalledTimes(1)
    expect(onUpdate).toHaveBeenCalledWith("overview", {})
  })

  it("preserves other overview keys when enabling dataset_header", () => {
    const onUpdate = vi.fn()
    render(
      <ExploreOverviewConfig
        config={{ overview: { dataset_header: false, future_card: true } }}
        onUpdate={onUpdate}
      />,
    )
    fireEvent.click(screen.getByRole("checkbox", { name: TOGGLE_NAME }))
    expect(onUpdate).toHaveBeenCalledTimes(1)
    expect(onUpdate).toHaveBeenCalledWith("overview", {
      future_card: true,
      dataset_header: true,
    })
  })

  it("preserves other overview keys when disabling dataset_header", () => {
    const onUpdate = vi.fn()
    render(
      <ExploreOverviewConfig
        config={{ overview: { dataset_header: true, future_card: true } }}
        onUpdate={onUpdate}
      />,
    )
    fireEvent.click(screen.getByRole("checkbox", { name: TOGGLE_NAME }))
    expect(onUpdate).toHaveBeenCalledTimes(1)
    expect(onUpdate).toHaveBeenCalledWith("overview", { future_card: true })
  })

  it("exposes a data-testid container for outer panel wiring", () => {
    render(<ExploreOverviewConfig config={{}} onUpdate={vi.fn()} />)
    expect(screen.getByTestId("explore-overview-config")).toBeTruthy()
  })

  it("does not render a separate selection marker for the dataset header row", () => {
    render(<ExploreOverviewConfig config={{}} onUpdate={vi.fn()} />)

    expect(screen.queryByTestId("explore-overview-indicator-dataset_header")).not.toBeInTheDocument()
  })

  it("exposes the dataset header description to assistive tech", () => {
    render(<ExploreOverviewConfig config={{}} onUpdate={vi.fn()} />)
    const toggle = screen.getByRole("checkbox", { name: TOGGLE_NAME })
    expect(toggle).toHaveAccessibleDescription(/Shows row count, columns, source/i)
  })

  describe("Schema table toggle", () => {
    const SCHEMA_TOGGLE = /schema table/i

    it("renders both Dataset header and Schema table toggles", () => {
      render(<ExploreOverviewConfig config={{}} onUpdate={vi.fn()} />)
      expect(screen.getByRole("checkbox", { name: TOGGLE_NAME })).toBeInTheDocument()
      expect(screen.getByRole("checkbox", { name: SCHEMA_TOGGLE })).toBeInTheDocument()
    })

    it("does not render a separate selection marker for the schema row", () => {
      render(
        <ExploreOverviewConfig
          config={{ overview: { schema: true } }}
          onUpdate={vi.fn()}
        />,
      )

      expect(screen.queryByTestId("explore-overview-indicator-schema")).not.toBeInTheDocument()
    })

    it("renders unchecked schema toggle when config has no overview block", () => {
      render(<ExploreOverviewConfig config={{}} onUpdate={vi.fn()} />)
      const toggle = screen.getByRole("checkbox", { name: SCHEMA_TOGGLE })
      expect(toggle.getAttribute("aria-checked")).toBe("false")
    })

    it("renders checked schema toggle when config.overview.schema is true", () => {
      render(
        <ExploreOverviewConfig
          config={{ overview: { schema: true } }}
          onUpdate={vi.fn()}
        />,
      )
      const toggle = screen.getByRole("checkbox", { name: SCHEMA_TOGGLE })
      expect(toggle.getAttribute("aria-checked")).toBe("true")
    })

    it("clicking unchecked schema toggle calls onUpdate with schema: true", () => {
      const onUpdate = vi.fn()
      render(<ExploreOverviewConfig config={{}} onUpdate={onUpdate} />)
      fireEvent.click(screen.getByRole("checkbox", { name: SCHEMA_TOGGLE }))
      expect(onUpdate).toHaveBeenCalledTimes(1)
      expect(onUpdate).toHaveBeenCalledWith("overview", { schema: true })
    })

    it("clicking Schema table toggle adds schema: true preserving dataset_header", () => {
      const onUpdate = vi.fn()
      render(
        <ExploreOverviewConfig
          config={{ overview: { dataset_header: true } }}
          onUpdate={onUpdate}
        />,
      )
      fireEvent.click(screen.getByRole("checkbox", { name: SCHEMA_TOGGLE }))
      expect(onUpdate).toHaveBeenCalledTimes(1)
      expect(onUpdate).toHaveBeenCalledWith("overview", {
        dataset_header: true,
        schema: true,
      })
    })

    it("disabling Schema table drops the key", () => {
      const onUpdate = vi.fn()
      render(
        <ExploreOverviewConfig
          config={{ overview: { dataset_header: true, schema: true } }}
          onUpdate={onUpdate}
        />,
      )
      fireEvent.click(screen.getByRole("checkbox", { name: SCHEMA_TOGGLE }))
      expect(onUpdate).toHaveBeenCalledTimes(1)
      expect(onUpdate).toHaveBeenCalledWith("overview", { dataset_header: true })
    })

  })
})
