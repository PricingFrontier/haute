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
    overview_summary: {
      data_quality: { issue_count: 0, issues: [] },
      categorical_summary: [],
    },
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
    expect(screen.queryByTestId("explore-dataset-snapshot-card")).not.toBeInTheDocument()
  })

  it("renders no-data empty state when toggle is on but report is null", () => {
    render(
      <ExploreOverviewPane
        node={makeNode({ overview: { dataset_snapshot: true } })}
        report={null}
      />,
    )
    expect(screen.getByText(/No cached data yet/i)).toBeInTheDocument()
    expect(screen.getByText(/Process & cache full data/i)).toBeInTheDocument()
    expect(screen.queryByTestId("explore-dataset-snapshot-card")).not.toBeInTheDocument()
  })

  it("renders the dataset snapshot card when toggle is on and report present", () => {
    render(
      <ExploreOverviewPane
        node={makeNode({ overview: { dataset_snapshot: true } })}
        report={makeReport()}
      />,
    )
    expect(screen.getByTestId("explore-dataset-snapshot-card")).toBeInTheDocument()
    expect(screen.queryByText(/No cards enabled/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/No cached data yet/i)).not.toBeInTheDocument()
  })

  it("renders all overview cards stacked when toggles are on", () => {
    render(
      <ExploreOverviewPane
        node={makeNode({
          overview: {
            dataset_snapshot: true,
            schema: true,
            numeric_summary: true,
            categorical_summary: true,
            data_quality: true,
          },
        })}
        report={makeReport()}
      />,
    )
    expect(screen.getByTestId("explore-dataset-snapshot-card")).toBeInTheDocument()
    expect(screen.getByTestId("explore-schema-table-card")).toBeInTheDocument()
    expect(screen.getByTestId("explore-numeric-summary-card")).toBeInTheDocument()
    expect(screen.getByTestId("explore-categorical-summary-card")).toBeInTheDocument()
    expect(screen.getByTestId("explore-data-quality-card")).toBeInTheDocument()
  })

  it("renders the schema card when only the schema toggle is on", () => {
    render(
      <ExploreOverviewPane
        node={makeNode({ overview: { schema: true } })}
        report={makeReport()}
      />,
    )
    expect(screen.getByTestId("explore-schema-table-card")).toBeInTheDocument()
    expect(screen.queryByTestId("explore-dataset-snapshot-card")).not.toBeInTheDocument()
    expect(screen.queryByTestId("explore-data-quality-card")).not.toBeInTheDocument()
  })

  it("renders no-cards empty state when overview card toggles are false", () => {
    render(
      <ExploreOverviewPane
        node={makeNode({
          overview: {
            dataset_snapshot: false,
            schema: false,
            numeric_summary: false,
            categorical_summary: false,
            data_quality: false,
          },
        })}
        report={makeReport()}
      />,
    )
    expect(screen.getByText(/No cards enabled/i)).toBeInTheDocument()
    expect(screen.queryByTestId("explore-dataset-snapshot-card")).not.toBeInTheDocument()
    expect(screen.queryByTestId("explore-schema-table-card")).not.toBeInTheDocument()
    expect(screen.queryByTestId("explore-numeric-summary-card")).not.toBeInTheDocument()
    expect(screen.queryByTestId("explore-categorical-summary-card")).not.toBeInTheDocument()
    expect(screen.queryByTestId("explore-data-quality-card")).not.toBeInTheDocument()
  })

  it("no-data empty-state body does not name a specific card", () => {
    render(
      <ExploreOverviewPane
        node={makeNode({ overview: { data_quality: true } })}
        report={null}
      />,
    )
    const body = screen.getByText(/Process & cache full data/i)
    expect(body.textContent).not.toMatch(/dataset snapshot/i)
  })

  it("renders snapshot before schema before numeric before categorical before quality in DOM", () => {
    render(
      <ExploreOverviewPane
        node={makeNode({
          overview: {
            dataset_snapshot: true,
            schema: true,
            numeric_summary: true,
            categorical_summary: true,
            data_quality: true,
          },
        })}
        report={makeReport()}
      />,
    )
    const snapshot = screen.getByTestId("explore-dataset-snapshot-card")
    const schema = screen.getByTestId("explore-schema-table-card")
    const numeric = screen.getByTestId("explore-numeric-summary-card")
    const categorical = screen.getByTestId("explore-categorical-summary-card")
    const quality = screen.getByTestId("explore-data-quality-card")
    expect(snapshot.compareDocumentPosition(schema) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(schema.compareDocumentPosition(numeric) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(numeric.compareDocumentPosition(categorical) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(categorical.compareDocumentPosition(quality) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it("renders the numeric summary card when only its toggle is on", () => {
    render(
      <ExploreOverviewPane
        node={makeNode({ overview: { numeric_summary: true } })}
        report={makeReport()}
      />,
    )

    expect(screen.getByTestId("explore-numeric-summary-card")).toBeInTheDocument()
    expect(screen.queryByTestId("explore-dataset-snapshot-card")).not.toBeInTheDocument()
    expect(screen.queryByTestId("explore-schema-table-card")).not.toBeInTheDocument()
    expect(screen.queryByTestId("explore-data-quality-card")).not.toBeInTheDocument()
  })

  it("renders the categorical summary card when only its toggle is on", () => {
    render(
      <ExploreOverviewPane
        node={makeNode({ overview: { categorical_summary: true } })}
        report={makeReport()}
      />,
    )

    expect(screen.getByTestId("explore-categorical-summary-card")).toBeInTheDocument()
    expect(screen.queryByTestId("explore-dataset-snapshot-card")).not.toBeInTheDocument()
    expect(screen.queryByTestId("explore-schema-table-card")).not.toBeInTheDocument()
    expect(screen.queryByTestId("explore-data-quality-card")).not.toBeInTheDocument()
  })
})
