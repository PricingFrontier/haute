import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { getExploreRelationships, parseExploreRelationshipsResponse } from "../exploreRelationships"
import { loadUiContractFixture } from "../../testSupport/uiContractFixtures"

const point = loadUiContractFixture<Record<string, unknown>>("node_data_point_response")

function response(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    status: "ok",
    point,
    data_version: "v1",
    total_rows: 10,
    target: "claims",
    weight: null,
    used_rows: 9,
    relationships: [
      {
        feature: "region",
        kind: "categorical",
        strength: 0.25,
        levels_truncated: false,
        levels: [
          { label: "north", kind: "value", rows: 6, weight: 6, target_mean: 1.5 },
          { label: "(missing)", kind: "missing", rows: 3, weight: 0, target_mean: null },
        ],
      },
    ],
    key_check: {
      columns: ["policy_id"],
      rows: 10,
      distinct_keys: 10,
      duplicate_rows: 0,
      null_key_rows: 0,
      unique: true,
    },
    ...overrides,
  }
}

describe("parseExploreRelationshipsResponse", () => {
  it("parses relationships, levels and the key check", () => {
    const parsed = parseExploreRelationshipsResponse(response())
    expect(parsed.relationships[0].levels.map((level) => [level.label, level.kind, level.target_mean]))
      .toEqual([["north", "value", 1.5], ["(missing)", "missing", null]])
    expect(parsed.key_check?.unique).toBe(true)
    expect(parsed.point.slot_key).toBe((point as { slot_key: string }).slot_key)
  })

  it("reads a response without a key check or relationships", () => {
    const parsed = parseExploreRelationshipsResponse(
      response({ status: "cache_required", relationships: [], key_check: null }),
    )
    expect(parsed.status).toBe("cache_required")
    expect(parsed.key_check).toBeNull()
  })

  it.each([
    ["an unknown status", { status: "partial" }, /status/],
    ["an unknown level kind", {
      relationships: [{ feature: "x", kind: "numeric", strength: 0, levels_truncated: false, levels: [{ label: "a", kind: "bucket", rows: 1, weight: 1, target_mean: 1 }] }],
    }, /levels\[0\]\.kind/],
    ["a key check without its counts", { key_check: { columns: ["id"], rows: 1 } }, /key_check\.distinct_keys/],
  ])("rejects %s", (_label, overrides, message) => {
    expect(() => parseExploreRelationshipsResponse(response(overrides))).toThrow(message)
  })
})

describe("getExploreRelationships", () => {
  let mockFetch: ReturnType<typeof vi.fn>
  beforeEach(() => {
    mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      statusText: "OK",
      json: () => Promise.resolve(response()),
    })
    globalThis.fetch = mockFetch as unknown as typeof fetch
  })
  afterEach(() => vi.restoreAllMocks())

  it("posts the question without its signal and parses the answer", async () => {
    const controller = new AbortController()
    const parsed = await getExploreRelationships({
      graph: { nodes: [], edges: [] },
      node_id: "explore",
      source: "live",
      target: "claims",
      weight: null,
      features: ["region"],
      key_columns: ["policy_id"],
      signal: controller.signal,
    })

    const [url, options] = mockFetch.mock.calls[0]
    expect(url).toBe("/api/explore/relationships")
    expect(options.method).toBe("POST")
    expect(JSON.parse(options.body)).toEqual({
      graph: { nodes: [], edges: [] },
      node_id: "explore",
      source: "live",
      target: "claims",
      weight: null,
      features: ["region"],
      key_columns: ["policy_id"],
    })
    expect(parsed.relationships[0].feature).toBe("region")
  })
})
