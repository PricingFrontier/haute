/**
 * Tests for the apiInput emit-port helpers.
 *
 * `apiInputEmitPortLabels` is the single source of truth for the
 * right-edge port labels an apiInput node exposes (shared by
 * `PipelineNode`'s `_SourceHandles`, its body-label column, and the
 * edge reconciler). `reconcileApiInputEdges` prunes outgoing edges
 * whose `sourceHandle` no longer maps to a rendered port after the
 * user edits the node's emit/label/table set — Defect 1 (orphaned
 * edges on emit-off / rename / delete / single↔multi transitions).
 */
import { describe, expect, it } from "vitest"
import {
  apiInputEmitPortLabels,
  reconcileApiInputEdges,
} from "../apiInputPorts"
import type { SimpleEdge } from "../../panels/editors/_shared"

// A table is a runtime port only if it is emit:true AND has >=1 selected
// column (matches the backend `load_v2_api_source`), so the helper gives a
// selected column by default; pass `columns` to model the no-column case.
const table = (
  label: string,
  emit: boolean,
  columns: Array<Record<string, unknown>> = [{ name: "c", selected: true }],
) => ({
  path: `$[*].${label}`,
  label,
  emit,
  columns,
})

describe("apiInputEmitPortLabels", () => {
  it("returns [] for a config without a tables key", () => {
    expect(apiInputEmitPortLabels({ path: "x.json" })).toEqual([])
  })

  it("returns only emit:true table labels, in order", () => {
    const labels = apiInputEmitPortLabels({
      tables: [table("policies", true), table("drivers", false), table("vehicles", true)],
    })
    expect(labels).toEqual(["policies", "vehicles"])
  })

  it("substitutes port_<idx> for missing / blank labels", () => {
    const labels = apiInputEmitPortLabels({
      tables: [
        { path: "$[*]", emit: true, columns: [{ name: "c", selected: true }] },
        { path: "$[*].b", label: "   ", emit: true, columns: [{ name: "c", selected: true }] },
      ],
    })
    expect(labels).toEqual(["port_0", "port_1"])
  })

  it("excludes an emit:true table with no selected columns (matches backend runtime)", () => {
    const labels = apiInputEmitPortLabels({
      tables: [
        table("policies", true),
        table("drivers", true, [{ name: "x", selected: false }]),
        table("vehicles", true),
      ],
    })
    expect(labels).toEqual(["policies", "vehicles"])
  })

  it("falls back to single-port when only one emit:true table has selected columns", () => {
    // The backend emits a bare frame (length-1 emit set) here, so the canvas
    // must render the single default handle — not a labelled multi-port one.
    const labels = apiInputEmitPortLabels({
      tables: [table("policies", true), table("drivers", true, [{ selected: false }])],
    })
    expect(labels).toEqual([])
  })

  it("disambiguates duplicate labels with a __<idx> suffix", () => {
    const labels = apiInputEmitPortLabels({
      tables: [table("dup", true), table("dup", true)],
    })
    expect(labels).toEqual(["dup", "dup__1"])
  })
})

describe("reconcileApiInputEdges", () => {
  const outgoing = (sourceHandle: string | null, id = `e_${sourceHandle}`): SimpleEdge => ({
    id,
    source: "api_1",
    target: "polars_2",
    sourceHandle,
    targetHandle: null,
  })

  it("keeps everything when the node has < 2 emit tables and edges use the null default handle", () => {
    const edges = [outgoing(null)]
    const result = reconcileApiInputEdges({
      nodeId: "api_1",
      config: { tables: [table("policies", true)] },
      edges,
    })
    expect(result.removed).toEqual([])
    expect(result.edges).toBe(edges)
  })

  it("removes a multi-port edge when its table's emit is toggled off (now single-port)", () => {
    // Was 2 emit tables (multi-port). User unticks 'drivers' → only
    // 'policies' emits → single default (null) handle. The edge bound
    // to 'drivers' is orphaned.
    const driversEdge = outgoing("drivers")
    const result = reconcileApiInputEdges({
      nodeId: "api_1",
      config: { tables: [table("policies", true), table("drivers", false)] },
      edges: [driversEdge],
    })
    expect(result.edges).toEqual([])
    expect(result.removed.map((r) => r.edge)).toEqual([driversEdge])
    expect(result.removed[0].sourceHandle).toBe("drivers")
  })

  it("removes the edge bound to a renamed port label", () => {
    // 'policies' renamed to 'quotes'; the second emit table keeps the
    // node multi-port. Edge still points at the stale 'policies'.
    const staleEdge = outgoing("policies")
    const liveEdge = outgoing("drivers")
    const result = reconcileApiInputEdges({
      nodeId: "api_1",
      config: { tables: [table("quotes", true), table("drivers", true)] },
      edges: [staleEdge, liveEdge],
    })
    expect(result.edges).toEqual([liveEdge])
    expect(result.removed.map((r) => r.edge)).toEqual([staleEdge])
  })

  it("removes the edge bound to a deleted emit table", () => {
    const goneEdge = outgoing("vehicles")
    const liveEdge = outgoing("policies")
    const result = reconcileApiInputEdges({
      nodeId: "api_1",
      config: { tables: [table("policies", true), table("drivers", true)] },
      edges: [goneEdge, liveEdge],
    })
    expect(result.edges).toEqual([liveEdge])
    expect(result.removed.map((r) => r.edge)).toEqual([goneEdge])
  })

  it("removes a null-handle edge once the node becomes multi-port (single→multi)", () => {
    // Edge was created while the node had a single default (null)
    // handle. A 2nd emit table now makes the node multi-port, so the
    // null handle no longer renders and the edge is orphaned.
    const legacyEdge = outgoing(null, "e_legacy")
    const result = reconcileApiInputEdges({
      nodeId: "api_1",
      config: { tables: [table("policies", true), table("drivers", true)] },
      edges: [legacyEdge],
    })
    expect(result.edges).toEqual([])
    expect(result.removed.map((r) => r.edge)).toEqual([legacyEdge])
  })

  it("ignores edges that do not originate from the node", () => {
    const otherEdge: SimpleEdge = {
      id: "e_other",
      source: "other_node",
      target: "polars_2",
      sourceHandle: "whatever",
      targetHandle: null,
    }
    const result = reconcileApiInputEdges({
      nodeId: "api_1",
      config: { tables: [table("policies", true), table("drivers", true)] },
      edges: [otherEdge],
    })
    expect(result.removed).toEqual([])
    expect(result.edges).toBe(result.edges)
    expect(result.edges).toEqual([otherEdge])
  })

  it("returns the SAME edges array reference when nothing is orphaned (no churn)", () => {
    const edges = [outgoing("policies"), outgoing("drivers")]
    const result = reconcileApiInputEdges({
      nodeId: "api_1",
      config: { tables: [table("policies", true), table("drivers", true)] },
      edges,
    })
    expect(result.removed).toEqual([])
    expect(result.edges).toBe(edges)
  })
})
