/**
 * Tests for the apiInput emit-port helpers.
 *
 * `apiInputEmitPortLabels` is the single source of truth for the
 * right-edge port labels an apiInput node exposes (shared by
 * `PipelineNode`'s `_SourceHandles`, its body-label column, and the
 * edge reconciler). Port identity is the RAW table label — exactly the
 * string the backend keys runtime ports by (`_json_shred.shred_v2`),
 * codegen emits as `connect(..., source_port=...)`, and the parser
 * restores as `edge.sourceHandle`. The frontend therefore NEVER
 * synthesizes handle ids (`port_<idx>` / `label__<idx>` are gone —
 * CODE_REVIEW W1.4): a table whose label is blank or collides with an
 * earlier one simply has no bindable port, and the editor surfaces the
 * validation error instead.
 *
 * `reconcileApiInputEdges` prunes outgoing edges whose `sourceHandle`
 * no longer maps to a rendered port. `migrateApiInputEdges` +
 * `applyApiInputConfigChange` implement CODE_REVIEW W1.3: a committed
 * label rename REBINDS the edges bound to the old handle in the same
 * state update — rename is handle migration, never edge loss.
 */
import { readFileSync } from "node:fs"
import { fileURLToPath } from "node:url"
import { describe, expect, it } from "vitest"
import {
  apiInputEmitPortLabels,
  apiInputLabelIssue,
  apiInputLabelIssueMessage,
  applyApiInputConfigChange,
  migrateApiInputEdges,
  reconcileApiInputEdges,
  sanitiseLabelForFilesystem,
} from "../apiInputPorts"
import { buildGraph } from "../buildGraph"
import type { SimpleEdge, SimpleNode } from "../../panels/editors/_shared"

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

/** Same table, new label — the path stays put, exactly like a label commit. */
const renamed = (t: Record<string, unknown>, label: string) => ({ ...t, label })
const THIS_TEST_FILE = fileURLToPath(import.meta.url)
const OLD_SELF_REFERENTIAL_ASSERTION = [
  "expect(result.edges)",
  ".toBe(result.edges)",
].join("")

it("does not regress to the old self-referential edge assertion", () => {
  // Tracker item 9.3: the old test compared result.edges to itself, which
  // passes even if `reconcileApiInputEdges` corrupts or copies the edge list.
  expect(readFileSync(THIS_TEST_FILE, "utf8")).not.toContain(
    OLD_SELF_REFERENTIAL_ASSERTION,
  )
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

  // W1.4 — the backend keys ports by the raw label and hard-rejects blank
  // labels (`validate_v2_schema`). A synthesized `port_<idx>` handle could
  // therefore never resolve at runtime (executor KeyError). Blank-label
  // tables get NO handle; the editor shows the validation error instead.
  it("renders no port for a missing / blank label — never synthesizes port_<idx>", () => {
    const labels = apiInputEmitPortLabels({
      tables: [
        { path: "$[*]", emit: true, columns: [{ name: "c", selected: true }] },
        { path: "$[*].b", label: "   ", emit: true, columns: [{ name: "c", selected: true }] },
        { path: "$[*].c", label: "vehicles", emit: true, columns: [{ name: "c", selected: true }] },
      ],
    })
    expect(labels).toEqual(["vehicles"])
    expect(labels.some((l) => l.startsWith("port_"))).toBe(false)
  })

  it("falls back to the single default handle when every emit label is blank (nothing bindable is synthesized)", () => {
    // This config is backend-invalid (validate_v2_schema rejects blank
    // labels) and unreachable through the editor, which blocks blank
    // commits; it can only arrive from a legacy disk file. We render the
    // legacy default handle rather than invent port ids the executor
    // could never resolve.
    const labels = apiInputEmitPortLabels({
      tables: [
        { path: "$[*]", emit: true, columns: [{ name: "c", selected: true }] },
        { path: "$[*].b", label: "", emit: true, columns: [{ name: "c", selected: true }] },
      ],
    })
    expect(labels).toEqual([])
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

  // W1.4 — duplicate labels are rejected by the backend on save, and the
  // runtime keys ports by raw label, so a synthesized `dup__1` handle
  // could never exist server-side. Only the first occurrence is a port.
  it("gives duplicate labels a single port (first occurrence) — never synthesizes __<idx>", () => {
    const labels = apiInputEmitPortLabels({
      tables: [table("dup", true), table("dup", true)],
    })
    expect(labels).toEqual(["dup"])
  })
})

describe("sanitiseLabelForFilesystem", () => {
  // Mirrors `haute._api_input_schema.sanitise_label_for_filesystem` so the
  // editor can pre-empt the backend's B2 (sanitised collision) rejection.
  it("keeps letters, digits, underscore, hyphen; replaces everything else with _", () => {
    expect(sanitiseLabelForFilesystem("drivers")).toBe("drivers")
    expect(sanitiseLabelForFilesystem("Driv-er_9")).toBe("Driv-er_9")
    expect(sanitiseLabelForFilesystem("a.b c/d")).toBe("a_b_c_d")
  })

  it("maps the empty label to _unnamed (backend parity)", () => {
    expect(sanitiseLabelForFilesystem("")).toBe("_unnamed")
  })

  it("replaces an astral character with ONE underscore (code-point parity with Python)", () => {
    // Python's regex sees "😀" as one code point → one "_". Without the
    // `u` flag, JS would see two UTF-16 surrogate units → "x__", and the
    // editor's collision verdicts would diverge from the backend's B2.
    expect(sanitiseLabelForFilesystem("x😀")).toBe("x_")
    // Consequence: "x😀" and "x🎉" DO collide (both sanitise to "x_"),
    // exactly as the backend reports.
    expect(apiInputLabelIssue("x😀", ["x🎉"])).toEqual({
      kind: "sanitised-collision",
      other: "x🎉",
      sanitised: "x_",
    })
  })
})

describe("apiInputLabelIssue", () => {
  it("accepts a unique non-blank label", () => {
    expect(apiInputLabelIssue("vehicles", ["policies", "drivers"])).toBeNull()
  })

  it("rejects blank and whitespace-only labels", () => {
    expect(apiInputLabelIssue("", ["policies"])).toEqual({ kind: "blank" })
    expect(apiInputLabelIssue("   ", ["policies"])).toEqual({ kind: "blank" })
  })

  it("rejects an exact duplicate of another table's label", () => {
    expect(apiInputLabelIssue("drivers", ["policies", "drivers"])).toEqual({
      kind: "duplicate",
      other: "drivers",
    })
  })

  it("rejects a label whose sanitised form collides with another table's (backend B2)", () => {
    // "driver.stats" and "driver_stats" both sanitise to "driver_stats":
    // the backend rejects this pair at save (both would write the same
    // parquet file), so the editor must surface it before commit.
    expect(apiInputLabelIssue("driver.stats", ["driver_stats"])).toEqual({
      kind: "sanitised-collision",
      other: "driver_stats",
      sanitised: "driver_stats",
    })
  })

  it("is case-sensitive (backend labels are exact strings)", () => {
    expect(apiInputLabelIssue("Drivers", ["drivers"])).toBeNull()
  })

  it("produces a user-facing message per issue kind, and null for none", () => {
    expect(apiInputLabelIssueMessage(null)).toBeNull()
    expect(apiInputLabelIssueMessage({ kind: "blank" })).toMatch(/required/i)
    expect(apiInputLabelIssueMessage({ kind: "duplicate", other: "drivers" })).toMatch(/drivers/)
    expect(
      apiInputLabelIssueMessage({
        kind: "sanitised-collision",
        other: "driver_stats",
        sanitised: "driver_stats",
      }),
    ).toMatch(/driver_stats/)
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

  it("removes the edge bound to a stale label when its table no longer exists", () => {
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
    // Tracker item 9.3: this test previously compared result.edges to
    // itself — a tautology. The real
    // contract it pretended to cover: when no outgoing edge of the node
    // is orphaned, the INPUT array reference is returned untouched and
    // foreign edges are never inspected, pruned, or copied.
    const otherEdge: SimpleEdge = {
      id: "e_other",
      source: "other_node",
      target: "polars_2",
      sourceHandle: "whatever",
      targetHandle: null,
    }
    const edges = [otherEdge]
    const result = reconcileApiInputEdges({
      nodeId: "api_1",
      config: { tables: [table("policies", true), table("drivers", true)] },
      edges,
    })
    expect(result.removed).toEqual([])
    expect(result.edges).toBe(edges)
    expect(result.edges[0]).toBe(otherEdge)
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

// ─── W1.3 — rename is migration, never loss ─────────────────────────

const edgeFrom = (
  source: string,
  sourceHandle: string | null,
  id = `e_${source}_${sourceHandle}`,
): SimpleEdge => ({
  id,
  source,
  target: "polars_2",
  sourceHandle,
  targetHandle: null,
})

describe("migrateApiInputEdges", () => {
  const prev = { tables: [table("policies", true), table("drivers", true)] }

  it("rebinds outgoing edges from the old label to the new one on an index-stable rename", () => {
    const next = {
      tables: [renamed(table("policies", true), "quotes"), table("drivers", true)],
    }
    const policiesEdge = edgeFrom("api_1", "policies")
    const driversEdge = edgeFrom("api_1", "drivers")
    const { edges, rebound } = migrateApiInputEdges({
      nodeId: "api_1",
      prevConfig: prev,
      nextConfig: next,
      edges: [policiesEdge, driversEdge],
    })
    expect(rebound).toEqual([{ edge: policiesEdge, from: "policies", to: "quotes" }])
    expect(edges.map((e) => e.sourceHandle)).toEqual(["quotes", "drivers"])
    // The untouched edge keeps its object identity.
    expect(edges[1]).toBe(driversEdge)
  })

  it("returns the same edges reference when no port was renamed", () => {
    const edges = [edgeFrom("api_1", "policies")]
    const result = migrateApiInputEdges({
      nodeId: "api_1",
      prevConfig: prev,
      nextConfig: prev,
      edges,
    })
    expect(result.rebound).toEqual([])
    expect(result.edges).toBe(edges)
  })

  it("never touches edges from other nodes, even if their handle matches the old label", () => {
    const next = {
      tables: [renamed(table("policies", true), "quotes"), table("drivers", true)],
    }
    const foreign = edgeFrom("other_node", "policies")
    const { edges, rebound } = migrateApiInputEdges({
      nodeId: "api_1",
      prevConfig: prev,
      nextConfig: next,
      edges: [foreign],
    })
    expect(rebound).toEqual([])
    expect(edges).toEqual([foreign])
  })

  it("does not infer renames across table add/remove (length change → no index identity)", () => {
    const next = { tables: [renamed(table("policies", true), "quotes")] }
    const policiesEdge = edgeFrom("api_1", "policies")
    const { rebound } = migrateApiInputEdges({
      nodeId: "api_1",
      prevConfig: prev,
      nextConfig: next,
      edges: [policiesEdge],
    })
    expect(rebound).toEqual([])
  })

  it("does not infer a rename when the table's path changed too (replaced, not renamed)", () => {
    const next = {
      tables: [
        { ...table("policies", true), path: "$[*].swapped[*]", label: "quotes" },
        table("drivers", true),
      ],
    }
    const { rebound } = migrateApiInputEdges({
      nodeId: "api_1",
      prevConfig: prev,
      nextConfig: next,
      edges: [edgeFrom("api_1", "policies")],
    })
    expect(rebound).toEqual([])
  })

  it("never rebinds onto a colliding handle (new label duplicates another port)", () => {
    // "policies" renamed to "drivers" — which the second table already
    // owns. The editor blocks this commit, but the migration must stay
    // conservative if such a config ever arrives: no rebinding to an
    // ambiguous handle. (The reconciler then prunes the stale edge with
    // a visible toast.)
    const next = {
      tables: [renamed(table("policies", true), "drivers"), table("drivers", true)],
    }
    const { rebound } = migrateApiInputEdges({
      nodeId: "api_1",
      prevConfig: prev,
      nextConfig: next,
      edges: [edgeFrom("api_1", "policies")],
    })
    expect(rebound).toEqual([])
  })

  it("ignores single-port nodes (edges are bound to the null default handle, not labels)", () => {
    const singlePrev = { tables: [table("policies", true)] }
    const singleNext = { tables: [renamed(table("policies", true), "quotes")] }
    const nullEdge = edgeFrom("api_1", null)
    const inputEdges = [nullEdge]
    const result = migrateApiInputEdges({
      nodeId: "api_1",
      prevConfig: singlePrev,
      nextConfig: singleNext,
      edges: inputEdges,
    })
    expect(result.rebound).toEqual([])
    // The INPUT array reference comes back untouched — no copy, no churn.
    expect(result.edges).toBe(inputEdges)
    expect(result.edges[0]).toBe(nullEdge)
  })

  // ── Duplicate-label ownership guards ──────────────────────────────
  //
  // With duplicate labels (pre-existing-invalid configs: the editor
  // blocks new ones, the backend rejects them on save), only the FIRST
  // eligible occurrence owns the port — and therefore the bound edges.
  // The migration must never move edges on behalf of a non-owner, and
  // must decline entirely when the old label still resolves afterwards.

  it("renaming a LATER duplicate (non-owner) never migrates the owner's edges", () => {
    // Tables: [dup, dup, drivers] — index 0 owns port "dup". The user
    // renames index 1 (whose label was never a bindable port).
    const dupPrev = {
      tables: [
        { ...table("dup", true), path: "$[*].a[*]" },
        { ...table("dup", true), path: "$[*].b[*]" },
        table("drivers", true),
      ],
    }
    const dupNext = {
      tables: [
        { ...table("dup", true), path: "$[*].a[*]" },
        { ...table("dup", true), path: "$[*].b[*]", label: "fresh" },
        table("drivers", true),
      ],
    }
    const dupEdge = edgeFrom("api_1", "dup")
    const result = applyApiInputConfigChange({
      nodeId: "api_1",
      prevConfig: dupPrev,
      nextConfig: dupNext,
      edges: [dupEdge],
    })
    // No rebind (index 1 was not the owner of "dup")…
    expect(result.rebound).toEqual([])
    // …and the owner's edge still resolves — untouched, not pruned.
    expect(result.removed).toEqual([])
    expect(result.edges[0]).toBe(dupEdge)
  })

  it("renaming the OWNER while a later duplicate survives declines to rebind (old label still resolves)", () => {
    // Tables: [dup, dup, drivers] — renaming index 0 to "fresh" leaves
    // "dup" alive as a port (now owned by index 1). Rebinding would
    // silently move the edge between two different tables; the
    // migration declines, and the edge keeps resolving against the
    // surviving "dup" port (so nothing is pruned either).
    const dupPrev = {
      tables: [
        { ...table("dup", true), path: "$[*].a[*]" },
        { ...table("dup", true), path: "$[*].b[*]" },
        table("drivers", true),
      ],
    }
    const dupNext = {
      tables: [
        { ...table("dup", true), path: "$[*].a[*]", label: "fresh" },
        { ...table("dup", true), path: "$[*].b[*]" },
        table("drivers", true),
      ],
    }
    const dupEdge = edgeFrom("api_1", "dup")
    const result = applyApiInputConfigChange({
      nodeId: "api_1",
      prevConfig: dupPrev,
      nextConfig: dupNext,
      edges: [dupEdge],
    })
    expect(result.rebound).toEqual([])
    expect(result.removed).toEqual([])
    expect(result.edges[0]).toBe(dupEdge)
  })
})

describe("applyApiInputConfigChange", () => {
  const prev = { tables: [table("policies", true), table("drivers", true)] }

  it("W1.3: a committed rename of a CONNECTED port keeps its edges, rebound to the new handle", () => {
    const next = {
      tables: [renamed(table("policies", true), "quotes"), table("drivers", true)],
    }
    const policiesEdge = edgeFrom("api_1", "policies")
    const driversEdge = edgeFrom("api_1", "drivers")
    const result = applyApiInputConfigChange({
      nodeId: "api_1",
      prevConfig: prev,
      nextConfig: next,
      edges: [policiesEdge, driversEdge],
    })
    // NOTHING is removed — rename is migration, not loss.
    expect(result.removed).toEqual([])
    expect(result.rebound).toEqual([{ edge: policiesEdge, from: "policies", to: "quotes" }])
    expect(result.edges.map((e) => e.sourceHandle).sort()).toEqual(["drivers", "quotes"])
  })

  it("still prunes genuinely orphaned edges (emit toggled off) with the rename machinery in place", () => {
    const next = { tables: [table("policies", true), table("drivers", false)] }
    const driversEdge = edgeFrom("api_1", "drivers")
    const keptNull = edgeFrom("api_1", null, "e_null")
    const result = applyApiInputConfigChange({
      nodeId: "api_1",
      prevConfig: prev,
      nextConfig: next,
      edges: [driversEdge, keptNull],
    })
    expect(result.rebound).toEqual([])
    expect(result.removed.map((r) => r.edge)).toEqual([driversEdge])
    // Back to single-port: the null default edge is the only valid one.
    expect(result.edges).toEqual([keptNull])
  })

  it("returns the input edges reference untouched when nothing changed", () => {
    const edges = [edgeFrom("api_1", "policies")]
    const result = applyApiInputConfigChange({
      nodeId: "api_1",
      prevConfig: prev,
      nextConfig: prev,
      edges,
    })
    expect(result.rebound).toEqual([])
    expect(result.removed).toEqual([])
    expect(result.edges).toBe(edges)
  })

  it("round-trip identity: parser-reloaded edges (sourceHandle == raw label) rebind 1:1 to rendered handles", () => {
    // Save side: codegen emits `connect(..., source_port=edge.sourceHandle)`
    // verbatim (src/haute/codegen.py). Load side: the parser restores
    // `sourceHandle = source_port` (src/haute/_graph_builders.py). So a
    // reloaded graph carries edges whose sourceHandle is the raw table
    // label — and the frontend must render handles in EXACTLY that space.
    const config = { tables: [table("policies", true), table("drivers", true)] }
    const labels = apiInputEmitPortLabels(config)
    // Raw labels, no transformation of any kind.
    expect(labels).toEqual(["policies", "drivers"])

    const reloaded = labels.map((label, i) =>
      edgeFrom("api_1", label, `e_reloaded_${i}`),
    )
    // buildGraph forwards edges verbatim to the backend payload — the
    // frontend never rewrites handles on save.
    const nodes: SimpleNode[] = [
      {
        id: "api_1",
        type: "apiInput",
        data: { label: "quotes", description: "", nodeType: "apiInput", config },
      },
    ]
    expect(buildGraph(nodes, reloaded).edges).toBe(reloaded)

    // And reconciliation against the same config keeps every reloaded
    // edge byte-identical: save → reload is a fixed point.
    const result = applyApiInputConfigChange({
      nodeId: "api_1",
      prevConfig: config,
      nextConfig: config,
      edges: reloaded,
    })
    expect(result.edges).toBe(reloaded)
    expect(result.rebound).toEqual([])
    expect(result.removed).toEqual([])
  })
})
