/**
 * Explore target relationships and key checks (`POST /api/explore/relationships`).
 *
 * Lives outside api/client.ts because only the lazily loaded Relationships
 * pane uses it; it shares client.ts's transport through `post`, and the
 * generated explore validators check its response.
 */

import { post } from "./client"
import type { GraphPayload } from "./types"
import type { ExploreRelationshipsResponse } from "../generated/api-contracts.generated"
import { expectGeneratedContract } from "../types/generatedContractValidation"

export type {
  ExploreKeyCheck,
  ExploreRelationship,
  ExploreRelationshipLevel,
  ExploreRelationshipsResponse,
} from "../generated/api-contracts.generated"

export interface ExploreRelationshipsArgs {
  graph: GraphPayload
  node_id: string
  source: string
  target: string | null
  weight: string | null
  features: string[]
  key_columns: string[]
  signal?: AbortSignal
}

/** Relate features to a target and check a key over the whole data point a node reads. */
export function getExploreRelationships(
  args: ExploreRelationshipsArgs,
): Promise<ExploreRelationshipsResponse> {
  const { signal, ...payload } = args
  // Up to 50 features over the whole dataset can outlast the default 30 s.
  return post<unknown>("/api/explore/relationships", payload, { signal, timeout: 300_000 }).then(
    async (data) => expectGeneratedContract(
      "ExploreRelationshipsResponse",
      (await import("../generated/api-contracts.explore.validators.mjs")).validateExploreRelationshipsResponse,
      data,
    ),
  )
}
