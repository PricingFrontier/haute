/**
 * Tests for the apiInput emit-port helpers.
 *
 * `apiInputFrameLabels` is the single source of truth for the
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
import * as apiInputPorts from "../apiInputPorts"
import {
  apiInputFrameLabels as apiInputFrameLabelsWithReserved,
  apiInputHasEmittingTable,
  edgeInputName,
} from "../apiInputPorts"
import { buildGraph } from "../buildGraph"
import type { SimpleEdge, SimpleNode } from "../../panels/editors/_shared"

const {
  apiInputLabelIssueMessage,
  sanitiseLabelForFilesystem,
} = apiInputPorts

const RESERVED_FRAME_LABELS = new Set([
  "False", "None", "True", "and", "as", "assert", "async", "await",
  "break", "class", "continue", "def", "del", "elif", "else", "except",
  "finally", "for", "from", "global", "if", "import", "in", "is",
  "lambda", "nonlocal", "not", "or", "pass", "raise", "return", "try",
  "while", "with", "yield",
])
const apiInputFrameLabels = (
  config: Parameters<typeof apiInputFrameLabelsWithReserved>[0],
) => apiInputFrameLabelsWithReserved(config, RESERVED_FRAME_LABELS)
const apiInputLabelIssue = (
  candidate: string,
  otherLabels: readonly string[],
) => apiInputPorts.apiInputLabelIssue(candidate, otherLabels, RESERVED_FRAME_LABELS)
const reconcileApiInputEdges = <E extends SimpleEdge>(
  args: Omit<Parameters<typeof apiInputPorts.reconcileApiInputEdges<E>>[0], "reservedLabels">,
) => apiInputPorts.reconcileApiInputEdges({ ...args, reservedLabels: RESERVED_FRAME_LABELS })
const migrateApiInputEdges = <E extends SimpleEdge>(
  args: Omit<Parameters<typeof apiInputPorts.migrateApiInputEdges<E>>[0], "reservedLabels">,
) => apiInputPorts.migrateApiInputEdges({ ...args, reservedLabels: RESERVED_FRAME_LABELS })
const applyApiInputConfigChange = <E extends SimpleEdge>(
  args: Omit<Parameters<typeof apiInputPorts.applyApiInputConfigChange<E>>[0], "reservedLabels">,
) => apiInputPorts.applyApiInputConfigChange({ ...args, reservedLabels: RESERVED_FRAME_LABELS })

// A table is a runtime port only if it is emit:true AND has >=1 selected
// column (matches the backend `load_v2_api_source`), so the helper gives a
// selected column by default; pass `columns` to model the no-column case.
const table = (
  label: string,
  emit: boolean,
  columns: Array<Record<string, unknown>> = [{ name: "c", selected: true }],
) => ({
  path: `$[:].${label}`,
  label,
  emit,
  columns,
})

describe("apiInputHasEmittingTable", () => {
  it("requires emit and a selected column on the same table", () => {
    expect(apiInputHasEmittingTable({ tables: [table("policies", true)] })).toBe(true)
    expect(apiInputHasEmittingTable({
      tables: [
        table("policies", true, [{ selected: false }]),
        table("drivers", false, [{ selected: true }]),
      ],
    })).toBe(false)
    expect(apiInputHasEmittingTable({ tables: [null, "invalid"] })).toBe(false)
  })
})

/** Same table, new label — the path stays put, exactly like a label commit. */
const renamed = (t: Record<string, unknown>, label: string) => ({ ...t, label })
const sourceNode = (
  nodeType: string,
  config: Record<string, unknown> = {},
): SimpleNode => ({
  id: "source_1",
  type: nodeType,
  data: {
    label: "Source node",
    description: "",
    nodeType,
    config,
    _defaultInputName: "server_source",
    _sourceHandleInputNames: nodeType === "apiInput" ? { quotes: "quotes" } : {},
  },
})
const sourceEdge = (sourceHandle: string | null): SimpleEdge => ({
  id: "edge_1",
  source: "source_1",
  target: "target_1",
  sourceHandle,
  targetHandle: null,
})
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

describe("apiInputFrameLabels", () => {
  it("retires the split emit-port and visible-frame exports", () => {
    expect(apiInputPorts).not.toHaveProperty("apiInputEmitPortLabels")
    expect(apiInputPorts).not.toHaveProperty("apiInputVisibleFrameLabels")
  })

  it("returns [] for a config without a tables key", () => {
    expect(apiInputFrameLabels({ path: "x.json" })).toEqual([])
  })

  it("returns only emit:true table labels, in order", () => {
    const labels = apiInputFrameLabels({
      tables: [table("policies", true), table("drivers", false), table("vehicles", true)],
    })
    expect(labels).toEqual(["policies", "vehicles"])
  })

  // W1.4 — the backend keys ports by the raw label and hard-rejects blank
  // labels (`validate_v2_schema`). A synthesized `port_<idx>` handle could
  // therefore never resolve at runtime (executor KeyError). Blank-label
  // tables get NO handle; the editor shows the validation error instead.
  it("renders no port for a missing / blank label — never synthesizes port_<idx>", () => {
    const labels = apiInputFrameLabels({
      tables: [
        { path: "$[:]", emit: true, columns: [{ name: "c", selected: true }] },
        { path: "$[:].b", label: "   ", emit: true, columns: [{ name: "c", selected: true }] },
        { path: "$[:].c", label: "vehicles", emit: true, columns: [{ name: "c", selected: true }] },
      ],
    })
    expect(labels).toEqual(["vehicles"])
    expect(labels.some((l) => l.startsWith("port_"))).toBe(false)
  })

  it("returns no frame labels when every emitted label is blank", () => {
    // This config is backend-invalid (validate_v2_schema rejects blank
    // labels) and unreachable through the editor, which blocks blank
    // commits. No bindable id is invented for malformed state.
    const labels = apiInputFrameLabels({
      tables: [
        { path: "$[:]", emit: true, columns: [{ name: "c", selected: true }] },
        { path: "$[:].b", label: "", emit: true, columns: [{ name: "c", selected: true }] },
      ],
    })
    expect(labels).toEqual([])
  })

  it("excludes an emit:true table with no selected columns (matches backend runtime)", () => {
    const labels = apiInputFrameLabels({
      tables: [
        table("policies", true),
        table("drivers", true, [{ name: "x", selected: false }]),
        table("vehicles", true),
      ],
    })
    expect(labels).toEqual(["policies", "vehicles"])
  })

  it("returns the sole eligible label when another table has no selected columns", () => {
    const labels = apiInputFrameLabels({
      tables: [table("policies", true), table("drivers", true, [{ selected: false }])],
    })
    expect(labels).toEqual(["policies"])
  })

  // W1.4 — duplicate labels are rejected by the backend on save, and the
  // runtime keys ports by raw label, so a synthesized `dup__1` handle
  // could never exist server-side. Only the first occurrence is a port.
  it("gives duplicate labels a single port (first occurrence) — never synthesizes __<idx>", () => {
    const labels = apiInputFrameLabels({
      tables: [table("dup", true), table("dup", true)],
    })
    expect(labels).toEqual(["dup"])
  })
})

describe("apiInputFrameLabels eligibility", () => {
  it("returns [] when no table is runtime-eligible with a valid identifier label", () => {
    const config = {
      tables: [
        table("disabled", false),
        table("unselected", true, [{ name: "c", selected: false }]),
        { label: "missing-columns", emit: true },
        { label: "   ", emit: true, columns: [{ name: "c", selected: true }] },
        { emit: true, columns: [{ name: "c", selected: true }] },
        { label: 42, emit: true, columns: [{ name: "c", selected: true }] },
      ],
    }

    expect(apiInputFrameLabels(config)).toEqual([])
  })

  it("returns the sole eligible label explicitly", () => {
    const rawLabel = "quotes"
    const config = { tables: [table(rawLabel, true)] }

    expect(apiInputFrameLabels(config)).toEqual([rawLabel])
  })

  it("returns many eligible labels in order and excludes invalid and case-duplicate labels", () => {
    const firstRawLabel = "policies"
    const config = {
      tables: [
        table(firstRawLabel, true),
        // An ineligible earlier use of a raw label does not reserve it.
        table("eligible_later", false),
        table("disabled", false),
        table("unselected", true, [{ name: "c", selected: false }]),
        { emit: true, columns: [{ name: "c", selected: true }] },
        { label: "\t ", emit: true, columns: [{ name: "c", selected: true }] },
        table("drivers", true),
        table(firstRawLabel, true),
        table("eligible_later", true),
        table("quote id", true),
        table("café", true),
        table("class", true),
        table("Policies", true),
        table("vehicles", true),
      ],
    }

    expect(apiInputFrameLabels(config)).toEqual([
      firstRawLabel,
      "drivers",
      "eligible_later",
      "vehicles",
    ])
  })
})

describe("edgeInputName", () => {
  it("returns an apiInput frame handle verbatim", () => {
    expect(
      edgeInputName(sourceEdge("quotes"), sourceNode("apiInput"), {}),
    ).toBe("quotes")
  })

  it("returns an explicit unresolved marker for a null-handle apiInput edge", () => {
    expect(
      edgeInputName(sourceEdge(null), sourceNode("apiInput"), {}),
    ).toBe("<unresolved>")
  })

  it("uses the server-owned identity for a keyword/Unicode ordinary label", () => {
    const ordinarySource: SimpleNode = {
      ...sourceNode("polars"),
      data: {
        ...sourceNode("polars").data,
        label: "class café",
        _defaultInputName: "node_class_cafe",
      },
    }

    expect(
      edgeInputName(sourceEdge("unused-port"), ordinarySource, {}),
    ).toBe("node_class_cafe")
  })

  it("fails clearly when an ordinary source has no server-owned identity", () => {
    const ordinarySource = sourceNode("polars")
    delete ordinarySource.data._defaultInputName
    expect(() => edgeInputName(sourceEdge(null), ordinarySource, {})).toThrow(
      /no authoritative default input identity/i,
    )
  })

  it("resolves a drilled submodel Input edge through its existing frame row", () => {
    const boundarySource: SimpleNode = {
      id: "submodel-input",
      type: "submodelPort",
      data: {
        label: "INPUT",
        description: "",
        nodeType: "submodelPort",
        config: {},
        instanceId: "instance_pricing",
        definitionId: "definition_pricing",
        portDirection: "input",
        ports: [
          { id: "row-quote", label: "quote_info" },
          { id: "row-batch", label: "NB batch 2" },
        ],
        externalNodeIds: ["quote_api", "nb_batch"],
        _sourceHandleInputNames: {
          "row-quote": "row_quote",
          "row-batch": "row_batch",
        },
      },
    }

    expect(
      edgeInputName(
        { ...sourceEdge("row-quote"), source: boundarySource.id },
        boundarySource,
        {},
      ),
    ).toBe("row_quote")
    expect(
      edgeInputName(
        { ...sourceEdge("row-batch"), source: boundarySource.id },
        boundarySource,
        {},
      ),
    ).toBe("row_batch")
  })

  it("rejects a drilled submodel Input edge whose frame row is missing", () => {
    const boundarySource: SimpleNode = {
      id: "submodel-input",
      type: "submodelPort",
      data: {
        label: "INPUT",
        description: "",
        nodeType: "submodelPort",
        config: {},
        instanceId: "instance_pricing",
        definitionId: "definition_pricing",
        portDirection: "input",
        ports: [{ id: "row-quote", label: "quote_info" }],
        externalNodeIds: ["quote_api"],
        _sourceHandleInputNames: { "row-quote": "row_quote" },
      },
    }

    expect(() => edgeInputName(
      { ...sourceEdge("missing-row"), source: boundarySource.id },
      boundarySource,
      {},
    )).toThrow(/missing-row/)
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
  })
})

describe("apiInputLabelIssue", () => {
  it("accepts ASCII identifiers and Python soft keywords", () => {
    for (const label of [
      "vehicles",
      "driver_claims",
      "_private",
      "MixedCase9",
      "match",
      "case",
      "type",
      "_",
    ]) {
      expect(apiInputLabelIssue(label, ["policies", "drivers"])).toBeNull()
    }
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

  it.each(["quote id", "1st_frame", "with-hyphen", "white\tspace", "café", "用户"])(
    "rejects non-ASCII or non-identifier label %j with the ASCII rule",
    (label) => {
      const issue = apiInputLabelIssue(label, [])
      expect(issue).not.toBeNull()
      if (issue === null) throw new Error("expected an ASCII-identifier issue")
      expect(apiInputLabelIssueMessage(issue)).toMatch(/ascii.*identifier/i)
    },
  )

  it("rejects every Python hard keyword while retaining soft keywords", () => {
    for (const keyword of RESERVED_FRAME_LABELS) {
      const issue = apiInputLabelIssue(keyword, [])
      expect(issue).not.toBeNull()
      if (issue === null) throw new Error("expected a hard-keyword issue")
      expect(apiInputLabelIssueMessage(issue)).toMatch(/keyword/i)
    }
  })

  it("rejects labels differing only in case (one parquet file on macOS/Windows)", () => {
    // "Drivers.parquet" and "drivers.parquet" are the SAME file on the
    // case-insensitive filesystems macOS and Windows default to — the
    // shred write would silently clobber one table's data. Mirrors the
    // backend B2 casefolded comparison. Keep the verdict and user-facing
    // conflict detail pinned without coupling the test to an issue tag.
    const issue = apiInputLabelIssue("Drivers", ["drivers"])
    expect(issue).not.toBeNull()
    if (issue === null) throw new Error("expected a case-only collision")
    const message = apiInputLabelIssueMessage(issue)
    expect(message).toMatch(/drivers/i)
    expect(message).toMatch(/case-insensitive/i)
    // Genuinely distinct labels still pass.
    expect(apiInputLabelIssue("Drivers", ["policies"])).toBeNull()
  })

  it("produces a user-facing message per issue kind, and null for none", () => {
    expect(apiInputLabelIssueMessage(null)).toBeNull()
    expect(apiInputLabelIssueMessage({ kind: "blank" })).toMatch(/required/i)
    expect(apiInputLabelIssueMessage({ kind: "duplicate", other: "drivers" })).toMatch(/drivers/)
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

  it("keeps a sole frame's labelled handle and preserves edge-array identity", () => {
    const labelledEdge = outgoing("policies")
    const edges = [labelledEdge]
    const result = reconcileApiInputEdges({
      nodeId: "api_1",
      config: { tables: [table("policies", true)] },
      edges,
    })

    expect(result.removed).toEqual([])
    expect(result.edges).toBe(edges)
    expect(result.edges[0]).toBe(labelledEdge)
  })

  it("removes an edge when its table's emit is toggled off", () => {
    // User unticks 'drivers'; the still-labelled policies frame remains,
    // while only the drivers edge becomes orphaned.
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
        { ...table("policies", true), path: "$[:].swapped[:]", label: "quotes" },
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

  it("migrates a sole labelled frame exactly like a multi-frame input", () => {
    const singlePrev = { tables: [table("policies", true)] }
    const singleNext = { tables: [renamed(table("policies", true), "quotes")] }
    const policiesEdge = edgeFrom("api_1", "policies")
    const result = migrateApiInputEdges({
      nodeId: "api_1",
      prevConfig: singlePrev,
      nextConfig: singleNext,
      edges: [policiesEdge],
    })
    expect(result.rebound).toEqual([
      { edge: policiesEdge, from: "policies", to: "quotes" },
    ])
    expect(result.edges[0].sourceHandle).toBe("quotes")
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
        { ...table("dup", true), path: "$[:].a[:]" },
        { ...table("dup", true), path: "$[:].b[:]" },
        table("drivers", true),
      ],
    }
    const dupNext = {
      tables: [
        { ...table("dup", true), path: "$[:].a[:]" },
        { ...table("dup", true), path: "$[:].b[:]", label: "fresh" },
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
        { ...table("dup", true), path: "$[:].a[:]" },
        { ...table("dup", true), path: "$[:].b[:]" },
        table("drivers", true),
      ],
    }
    const dupNext = {
      tables: [
        { ...table("dup", true), path: "$[:].a[:]", label: "fresh" },
        { ...table("dup", true), path: "$[:].b[:]" },
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
    expect(result.removed.map((r) => r.edge)).toEqual([driversEdge, keptNull])
    expect(result.edges).toEqual([])
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
    const labels = apiInputFrameLabels(config)
    // Raw labels, no transformation of any kind.
    expect(labels).toEqual(["policies", "drivers"])

    const reloaded = labels.map((label, i) =>
      edgeFrom("api_1", label, `e_reloaded_${i}`),
    )
    // buildGraph preserves canonical handle values while cloning the live
    // graph at the outbound request boundary.
    const nodes: SimpleNode[] = [
      {
        id: "api_1",
        type: "apiInput",
        data: { label: "quotes", description: "", nodeType: "apiInput", config },
      },
    ]
    const requestEdges = buildGraph(nodes, reloaded).edges
    expect(requestEdges).toStrictEqual(reloaded)
    expect(requestEdges).not.toBe(reloaded)

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

describe("canonical submodel boundary resolution", () => {
  it("uses immutable public port ids for canonical drilled Input names", () => {
    const boundarySource: SimpleNode = {
      id: "canonical-input-boundary",
      type: "submodelPort",
      data: {
        label: "INPUT",
        description: "",
        nodeType: "submodelPort",
        config: {},
        instanceId: "instance_pricing",
        definitionId: "definition_pricing",
        portDirection: "input",
        ports: [{ id: "policy_data", label: "Policy data" }],
        externalNodeIds: ["quote_api"],
        _sourceHandleInputNames: { policy_data: "policy_data" },
      },
    }

    expect(
      edgeInputName(
        { ...sourceEdge("policy_data"), source: boundarySource.id },
        boundarySource,
        {},
      ),
    ).toBe("policy_data")
  })

  it("resolves an arbitrary-id occurrence output through its occurrence name", () => {
    const child: SimpleNode = {
      ...sourceNode("polars"),
      id: "child_output",
      data: {
        ...sourceNode("polars").data,
        label: "Canonical output frame",
      },
    }
    const occurrence: SimpleNode = {
      ...sourceNode("submodel"),
      id: "instance_pricing_secondary",
      data: {
        ...sourceNode("submodel").data,
        config: {
          definitionId: "definition_pricing",
          alias: "pricing_secondary",
        },
        _sourceHandleInputNames: {
          "out__written_premium": "pricing_secondary",
        },
      },
    }
    const definition = {
      definitionId: "definition_pricing",
      file: "modules/pricing.py",
      graph: { nodes: [child], edges: [] },
      inputPorts: [],
      _inputPortInputNames: {},
      outputPorts: [
        {
          portId: "written_premium",
          label: "Written premium",
          source: { nodeId: child.id, handleId: null },
        },
      ],
    }

    expect(
      edgeInputName(
        { ...sourceEdge("out__written_premium"), source: occurrence.id },
        occurrence,
        { definition_pricing: definition },
      ),
    ).toBe("pricing_secondary")
  })

  it("matches every internal target of a canonical fan-out input port", () => {
    const external: SimpleNode = {
      ...sourceNode("polars"),
      id: "external_feed",
      data: { ...sourceNode("polars").data, label: "External feed" },
    }
    const firstTarget: SimpleNode = { ...sourceNode("polars"), id: "child_a" }
    const secondTarget: SimpleNode = { ...sourceNode("polars"), id: "child_b" }
    const occurrence: SimpleNode = {
      ...sourceNode("submodel"),
      id: "instance_pricing_secondary",
      data: {
        ...sourceNode("submodel").data,
        config: {
          definitionId: "definition_pricing",
          alias: "pricing_secondary",
        },
      },
    }
    const definition = {
      definitionId: "definition_pricing",
      file: "modules/pricing.py",
      graph: { nodes: [firstTarget, secondTarget], edges: [] },
      inputPorts: [
        {
          portId: "policy_data",
          label: "Policy data",
          targets: [
            { nodeId: firstTarget.id, handleId: null },
            { nodeId: secondTarget.id, handleId: "base" },
          ],
        },
      ],
      _inputPortInputNames: { policy_data: "policy_data" },
      outputPorts: [],
    }
    const edge: SimpleEdge = {
      id: "external_to_pricing",
      source: external.id,
      target: occurrence.id,
      sourceHandle: null,
      targetHandle: "in__policy_data",
    }

    expect(apiInputPorts.incomingEdgeInputNames({
      targetNodeId: secondTarget.id,
      boundaryNodeId: occurrence.id,
      nodes: [external, occurrence],
      edges: [edge],
      submodels: { definition_pricing: definition },
    })).toEqual(["policy_data"])
  })
})
