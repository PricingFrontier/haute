import type {
  ExploreColumnStat,
  ExploreOverviewSummary,
  NodeDataProfile,
} from "../../api/types"

/**
 * What the Explore panes render: the shared profile of the data the node reads,
 * together with the point facts the panes label it with.
 *
 * Explore no longer owns a report of its own — the statistics come from the
 * `profile` analysis of a data point, which every consumer of that point
 * shares, and the point says which producer and source they describe.
 */
export interface ExploreDataView {
  row_count: number
  column_count: number
  columns: ExploreColumnStat[]
  overview_summary: ExploreOverviewSummary
  /** The version of the data these statistics describe. */
  data_version: string
  generated_at: number
  source: string
  producer_node_id: string
}

/**
 * Build the view, or return null when the profile does not describe the data
 * the point currently holds: showing it beside another version's labels would
 * attribute statistics to data they were not computed from.
 *
 * Every argument is a primitive or a stored object, so a caller can select them
 * from the shared store without creating a new object on every render.
 */
export function exploreDataView(
  profile: NodeDataProfile | null | undefined,
  dataVersion: string | null | undefined,
  producerNodeId: string | null | undefined,
  source: string,
): ExploreDataView | null {
  if (!profile || !producerNodeId || !dataVersion || dataVersion !== profile.data_version) {
    return null
  }
  return {
    row_count: profile.row_count,
    column_count: profile.column_count,
    columns: profile.columns,
    overview_summary: profile.overview_summary,
    data_version: profile.data_version,
    generated_at: profile.generated_at,
    source,
    producer_node_id: producerNodeId,
  }
}
