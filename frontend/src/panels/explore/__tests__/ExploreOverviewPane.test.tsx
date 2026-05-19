import { afterEach, describe, expect, it } from "vitest"
import { cleanup, render, screen } from "@testing-library/react"
import type { ExploreCacheReport } from "../../../api/types"
import type { SimpleNode } from "../../editors"
import ExploreOverviewPane from "../ExploreOverviewPane"

function makeNode(config: Record<string, unknown>): SimpleNode {
  return {
    id: "explore_1",
    type: "explore",
    data: { label: "Explore Claims", description: "", nodeType: "explore", config },
  }
}

function makeReport(overrides: Partial<ExploreCacheReport> = {}): ExploreCacheReport {
  return {
    status: "ok",
    node_id: "explore_1",
    upstream_node_id: "source_1",
    source: "pricing",
    dataframe_cache_key: "explore_dataset:abc123",
    row_count: 1234,
    column_count: 12,
    generated_at: 1710000000,
    columns: [],
    ...overrides,
  }
}

afterEach(cleanup)

describe("ExploreOverviewPane", () => {
  it("renders no-cards empty state when overview is empty", () => {
    render(<ExploreOverviewPane node={makeNode({})} report={null} />)
    expect(screen.getByTestId("explore-overview-pane")).toBeInTheDocument()
    expect(screen.getByText(/No cards enabled/i)).toBeInTheDocument()
    expect(screen.getByText(/Overview tab in the config panel/i)).toBeInTheDocument()
    expect(screen.queryByTestId("explore-dataset-header-card")).not.toBeInTheDocument()
  })

  it("renders no-data empty state when toggle is on but report is null", () => {
    render(
      <ExploreOverviewPane
        node={makeNode({ overview: { dataset_header: true } })}
        report={null}
      />,
    )
    expect(screen.getByText(/No cached data yet/i)).toBeInTheDocument()
    expect(screen.getByText(/Process & cache full data/i)).toBeInTheDocument()
    expect(screen.queryByTestId("explore-dataset-header-card")).not.toBeInTheDocument()
  })

  it("renders the dataset header card when toggle is on and report present", () => {
    render(
      <ExploreOverviewPane
        node={makeNode({ overview: { dataset_header: true } })}
        report={makeReport()}
      />,
    )
    expect(screen.getByTestId("explore-dataset-header-card")).toBeInTheDocument()
    expect(screen.queryByText(/No cards enabled/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/No cached data yet/i)).not.toBeInTheDocument()
  })

  it("renders both cards stacked when both toggles are on", () => {
    render(
      <ExploreOverviewPane
        node={makeNode({ overview: { dataset_header: true, schema: true } })}
        report={makeReport()}
      />,
    )
    expect(screen.getByTestId("explore-dataset-header-card")).toBeInTheDocument()
    expect(screen.getByTestId("explore-schema-table-card")).toBeInTheDocument()
  })

  it("renders only the schema card when only schema toggle is on", () => {
    render(
      <ExploreOverviewPane
        node={makeNode({ overview: { schema: true } })}
        report={makeReport()}
      />,
    )
    expect(screen.getByTestId("explore-schema-table-card")).toBeInTheDocument()
    expect(screen.queryByTestId("explore-dataset-header-card")).not.toBeInTheDocument()
  })

  it("renders no-cards empty state when overview has dataset_header: false and schema: false", () => {
    render(
      <ExploreOverviewPane
        node={makeNode({ overview: { dataset_header: false, schema: false } })}
        report={makeReport()}
      />,
    )
    expect(screen.getByText(/No cards enabled/i)).toBeInTheDocument()
    expect(screen.queryByTestId("explore-dataset-header-card")).not.toBeInTheDocument()
    expect(screen.queryByTestId("explore-schema-table-card")).not.toBeInTheDocument()
  })

  it("no-data empty-state body does not name a specific card", () => {
    render(
      <ExploreOverviewPane
        node={makeNode({ overview: { schema: true } })}
        report={null}
      />,
    )
    const body = screen.getByText(/Process & cache full data/i)
    expect(body.textContent).not.toMatch(/dataset header/i)
  })

  it("renders dataset_header card before schema card in DOM", () => {
    render(
      <ExploreOverviewPane
        node={makeNode({ overview: { dataset_header: true, schema: true } })}
        report={makeReport()}
      />,
    )
    const header = screen.getByTestId("explore-dataset-header-card")
    const schema = screen.getByTestId("explore-schema-table-card")
    const relationship = header.compareDocumentPosition(schema)
    expect(relationship & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })
})
