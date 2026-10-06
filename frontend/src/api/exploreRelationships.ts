/**
 * Explore target relationships and key checks (`POST /api/explore/relationships`).
 *
 * Lives outside api/client.ts because only the lazily loaded Relationships
 * pane uses it; it shares client.ts's transport through `post` and parses its
 * own response with the shared guard primitives.
 */

import { post } from "./client"
import type { GraphPayload, NodeDataPointResponse } from "./types"
import {
  expectArray,
  expectBoolean,
  expectNullableNumber,
  expectNullableString,
  expectNumber,
  expectPlainObject,
  expectString,
  expectStringLiteral,
  parseNodeDataPointResponse,
} from "../types/guards"

export interface ExploreRelationshipLevel {
  label: string
  kind: "value" | "bin" | "missing" | "other"
  rows: number
  weight: number
  target_mean: number | null
}

export interface ExploreRelationship {
  feature: string
  kind: "numeric" | "categorical"
  /** Weighted correlation ratio: the share of the target's variance the levels explain (0 to 1). */
  strength: number
  levels: ExploreRelationshipLevel[]
  levels_truncated: boolean
}

export interface ExploreKeyCheck {
  columns: string[]
  rows: number
  distinct_keys: number
  duplicate_rows: number
  null_key_rows: number
  unique: boolean
}

export interface ExploreRelationshipsResponse {
  status: "ok" | "cache_required"
  point: NodeDataPointResponse
  data_version: string | null
  total_rows: number
  target: string | null
  weight: string | null
  used_rows: number
  relationships: ExploreRelationship[]
  key_check: ExploreKeyCheck | null
}

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

const PARSER = "parseExploreRelationshipsResponse"
const LEVEL_KINDS = ["value", "bin", "missing", "other"] as const
const RELATIONSHIP_KINDS = ["numeric", "categorical"] as const
const STATUSES = ["ok", "cache_required"] as const

function parseLevel(value: unknown, field: string): ExploreRelationshipLevel {
  const obj = expectPlainObject(PARSER, value, field)
  return {
    label: expectString(PARSER, obj.label, `${field}.label`),
    kind: expectStringLiteral(PARSER, obj.kind, `${field}.kind`, LEVEL_KINDS),
    rows: expectNumber(PARSER, obj.rows, `${field}.rows`),
    weight: expectNumber(PARSER, obj.weight, `${field}.weight`),
    target_mean: expectNullableNumber(PARSER, obj.target_mean, `${field}.target_mean`),
  }
}

function parseRelationship(value: unknown, field: string): ExploreRelationship {
  const obj = expectPlainObject(PARSER, value, field)
  return {
    feature: expectString(PARSER, obj.feature, `${field}.feature`),
    kind: expectStringLiteral(PARSER, obj.kind, `${field}.kind`, RELATIONSHIP_KINDS),
    strength: expectNumber(PARSER, obj.strength, `${field}.strength`),
    levels: expectArray(PARSER, obj.levels, `${field}.levels`).map((level, index) =>
      parseLevel(level, `${field}.levels[${index}]`),
    ),
    levels_truncated: expectBoolean(PARSER, obj.levels_truncated, `${field}.levels_truncated`),
  }
}

function parseKeyCheck(value: unknown): ExploreKeyCheck | null {
  if (value === null || value === undefined) return null
  const field = "key_check"
  const obj = expectPlainObject(PARSER, value, field)
  return {
    columns: expectArray(PARSER, obj.columns, `${field}.columns`).map((column, index) =>
      expectString(PARSER, column, `${field}.columns[${index}]`),
    ),
    rows: expectNumber(PARSER, obj.rows, `${field}.rows`),
    distinct_keys: expectNumber(PARSER, obj.distinct_keys, `${field}.distinct_keys`),
    duplicate_rows: expectNumber(PARSER, obj.duplicate_rows, `${field}.duplicate_rows`),
    null_key_rows: expectNumber(PARSER, obj.null_key_rows, `${field}.null_key_rows`),
    unique: expectBoolean(PARSER, obj.unique, `${field}.unique`),
  }
}

export function parseExploreRelationshipsResponse(value: unknown): ExploreRelationshipsResponse {
  const obj = expectPlainObject(PARSER, value)
  return {
    status: expectStringLiteral(PARSER, obj.status, "status", STATUSES),
    point: parseNodeDataPointResponse(obj.point),
    data_version: expectNullableString(PARSER, obj.data_version, "data_version"),
    total_rows: expectNumber(PARSER, obj.total_rows, "total_rows"),
    target: expectNullableString(PARSER, obj.target, "target"),
    weight: expectNullableString(PARSER, obj.weight, "weight"),
    used_rows: expectNumber(PARSER, obj.used_rows, "used_rows"),
    relationships: expectArray(PARSER, obj.relationships, "relationships").map(
      (relationship, index) => parseRelationship(relationship, `relationships[${index}]`),
    ),
    key_check: parseKeyCheck(obj.key_check),
  }
}

/** Relate features to a target and check a key over the whole data point a node reads. */
export function getExploreRelationships(
  args: ExploreRelationshipsArgs,
): Promise<ExploreRelationshipsResponse> {
  const { signal, ...payload } = args
  // Up to 50 features over the whole dataset can outlast the default 30 s.
  return post<unknown>("/api/explore/relationships", payload, { signal, timeout: 300_000 }).then(
    parseExploreRelationshipsResponse,
  )
}
