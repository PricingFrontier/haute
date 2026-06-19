/**
 * Render tests for the v2 OutputEditor.
 *
 * The OUTPUT node config is v2: { outputMapping: Entry[], outputFormat }.
 * Each incoming edge (= source FRAME) is one collapsible block; rows inside a
 * block map a frame column → an output_path. Tests cover:
 *   - one block per incoming frame + empty state when there are none;
 *   - add / remove row, per-frame + per-row enable;
 *   - Save round-trips the four-field v2 shape with `[:]` paths;
 *   - a v1 `{ fields: [...] }` config shows the migration banner and Save
 *     writes v2 (one entry per former field);
 *   - Infer adds Inferred pilled rows;
 *   - an invalid path surfaces an error.
 *
 * Like the ApiInputEditor suite, a stateful harness echoes onUpdate back into
 * the `config` prop (NodePanel does this) so writeBack round-trips and
 * multi-step interactions accumulate.
 */
import { describe, it, expect, vi, afterEach } from "vitest"
import { render as rtlRender, screen, fireEvent, cleanup } from "@testing-library/react"
import { useState } from "react"
import OutputEditor from "../../panels/editors/OutputEditor"
import { GraphProvider } from "../../panels/GraphContext"
import type { SimpleNode, SimpleEdge } from "../../panels/editors"

afterEach(cleanup)

// ─── Graph fixtures ───────────────────────────────────────────────

// A single-port source (null sourceHandle) whose columns come from _columns.
const SINGLE_PORT_NODES: SimpleNode[] = [
  {
    id: "upstream",
    data: {
      label: "Upstream Node",
      description: "",
      nodeType: "polars",
      _columns: [
        { name: "premium", dtype: "Float64" },
        { name: "area", dtype: "String" },
        { name: "power", dtype: "Int64" },
      ],
    },
  },
]
const SINGLE_PORT_EDGES: SimpleEdge[] = [
  { id: "e1", source: "upstream", target: "output_1" },
]

// A multi-frame setup: an apiInput source emitting two tables (policies,
// drivers) plus a polars source. Two incoming edges, one per emitted frame.
const MULTI_FRAME_NODES: SimpleNode[] = [
  {
    id: "api",
    data: {
      label: "API Input",
      description: "",
      nodeType: "apiInput",
      config: {
        tables: [
          {
            path: "$[:]",
            label: "policies",
            emit: true,
            columns: [
              { name: "policy_id", path: "$[:].policy_id", type: "int", status: "Inferred", selected: true },
              { name: "premium", path: "$[:].premium", type: "float", status: "Inferred", selected: true },
            ],
          },
          {
            path: "$[:].drivers[:]",
            label: "drivers",
            emit: true,
            columns: [
              { name: "driver_id", path: "$[:].drivers[:].driver_id", type: "int", status: "Inferred", selected: true },
            ],
          },
        ],
      },
    },
  },
]
const MULTI_FRAME_EDGES: SimpleEdge[] = [
  { id: "e-pol", source: "api", target: "output_1", sourceHandle: "policies" },
  { id: "e-drv", source: "api", target: "output_1", sourceHandle: "drivers" },
]

// A SINGLE-frame apiInput: ONE emit table, a null-handle edge, and NO `_columns`
// (not previewed yet). Its columns must come STRAIGHT from config.tables —
// otherwise Infer finds nothing, the user is forced to add a "" source_column
// row, and the backend crashes with missing=[''] (the bug Nick hit). Only
// `selected` columns surface.
const SINGLE_FRAME_API_NODES: SimpleNode[] = [
  {
    id: "api",
    data: {
      label: "API Input",
      description: "",
      nodeType: "apiInput",
      config: {
        tables: [
          {
            path: "$[:]",
            label: "quotes",
            emit: true,
            columns: [
              { name: "abi_code", path: "$[:].abi_code", type: "str", status: "Inferred", selected: true },
              { name: "premium", path: "$[:].premium", type: "float", status: "Inferred", selected: true },
              { name: "unselected", path: "$[:].unselected", type: "str", status: "Inferred", selected: false },
            ],
          },
        ],
      },
    },
  },
]
const SINGLE_FRAME_API_EDGES: SimpleEdge[] = [
  { id: "e-api", source: "api", target: "output_1" }, // null sourceHandle
]

// Two DISTINCT single-port sources (null sourceHandle each), with distinct
// node labels. The backend keys each frame by `sanitize(source-node-label)`, so
// the editor must persist DISTINCT, non-empty `source_port`s — not "" for both
// (which would collapse the two frames and trip OutputMappingSchemaError on a
// genuine multi-frame OUTPUT).
const TWO_SINGLE_PORT_NODES: SimpleNode[] = [
  {
    id: "src_a",
    data: {
      label: "Source A",
      description: "",
      nodeType: "polars",
      _columns: [{ name: "alpha", dtype: "String" }],
    },
  },
  {
    id: "src_b",
    data: {
      label: "Source B",
      description: "",
      nodeType: "polars",
      _columns: [{ name: "beta", dtype: "String" }],
    },
  },
]
const TWO_SINGLE_PORT_EDGES: SimpleEdge[] = [
  { id: "e-a", source: "src_a", target: "output_1" },
  { id: "e-b", source: "src_b", target: "output_1" },
]

// Two distinct sources whose RESOLVED ports collide: two apiInputs each
// emitting a table with the SAME label "shared". The editor must block (banner)
// because both frames would write `source_port: "shared"` and merge on disk.
const COLLIDING_PORT_NODES: SimpleNode[] = [
  {
    id: "api_a",
    data: {
      label: "API A",
      description: "",
      nodeType: "apiInput",
      config: {
        tables: [
          { path: "$[:]", label: "shared", emit: true, columns: [{ name: "a", path: "$[:].a", type: "int", status: "Inferred", selected: true }] },
        ],
      },
    },
  },
  {
    id: "api_b",
    data: {
      label: "API B",
      description: "",
      nodeType: "apiInput",
      config: {
        tables: [
          { path: "$[:]", label: "shared", emit: true, columns: [{ name: "b", path: "$[:].b", type: "int", status: "Inferred", selected: true }] },
        ],
      },
    },
  },
]
const COLLIDING_PORT_EDGES: SimpleEdge[] = [
  { id: "e-ca", source: "api_a", target: "output_1", sourceHandle: "shared" },
  { id: "e-cb", source: "api_b", target: "output_1", sourceHandle: "shared" },
]

/** Renders OutputEditor wrapped in a GraphProvider. */
function render(
  element: React.ReactElement,
  opts: { allNodes?: SimpleNode[]; edges?: SimpleEdge[] } = {},
) {
  return rtlRender(
    <GraphProvider allNodes={opts.allNodes ?? []} edges={opts.edges ?? []}>
      {element}
    </GraphProvider>,
  )
}

/** Echoes onUpdate back into the `config` prop like NodePanel does, so a
 * `writeV2(...)` push becomes the next render's config. */
function StatefulHarness({
  initialConfig,
  onUpdateSpy,
  allNodes,
  edges,
}: {
  initialConfig: Record<string, unknown>
  onUpdateSpy: (keyOrUpdates: string | Record<string, unknown>, value?: unknown) => void
  allNodes: SimpleNode[]
  edges: SimpleEdge[]
}) {
  const [config, setConfig] = useState(initialConfig)
  return (
    <GraphProvider allNodes={allNodes} edges={edges}>
      <OutputEditor
        config={config}
        nodeId="output_1"
        onUpdate={(keyOrUpdates, value) => {
          onUpdateSpy(keyOrUpdates, value)
          setConfig((prev) =>
            typeof keyOrUpdates === "string"
              ? { ...prev, [keyOrUpdates]: value }
              : { ...prev, ...keyOrUpdates },
          )
        }}
      />
    </GraphProvider>
  )
}

const DEFAULT_PROPS = {
  config: {} as Record<string, unknown>,
  onUpdate: vi.fn(),
  nodeId: "output_1",
}

// Helper: expand a frame block so its rows render.
function expandFrame(prefix: string) {
  fireEvent.click(screen.getByTestId(`${prefix}-toggle`))
}

describe("OutputEditor — frame blocks", () => {
  it("renders the Response Mapping label", () => {
    render(<OutputEditor {...DEFAULT_PROPS} />)
    expect(screen.getByText("Response Mapping")).toBeTruthy()
  })

  it("shows the empty state when there are no incoming edges", () => {
    render(<OutputEditor {...DEFAULT_PROPS} />, { allNodes: SINGLE_PORT_NODES, edges: [] })
    expect(screen.getByTestId("output-empty-state")).toBeTruthy()
    expect(
      screen.getByText("Connect input frames to map them to the response."),
    ).toBeTruthy()
  })

  it("renders one block per incoming frame", () => {
    render(<OutputEditor {...DEFAULT_PROPS} />, {
      allNodes: MULTI_FRAME_NODES,
      edges: MULTI_FRAME_EDGES,
    })
    expect(screen.getByTestId("output-frame-0")).toBeTruthy()
    expect(screen.getByTestId("output-frame-1")).toBeTruthy()
    expect(screen.queryByTestId("output-frame-2")).toBeNull()
  })

  it("shows each frame's label (frame identity = sourceHandle)", () => {
    render(<OutputEditor {...DEFAULT_PROPS} />, {
      allNodes: MULTI_FRAME_NODES,
      edges: MULTI_FRAME_EDGES,
    })
    expect(screen.getByText("policies")).toBeTruthy()
    expect(screen.getByText("drivers")).toBeTruthy()
  })

  it("falls back to the source node label for a single-port (null handle) frame", () => {
    render(<OutputEditor {...DEFAULT_PROPS} />, {
      allNodes: SINGLE_PORT_NODES,
      edges: SINGLE_PORT_EDGES,
    })
    expect(screen.getByText("Upstream Node")).toBeTruthy()
  })

  it("surfaces the per-frame column set from the apiInput table matching the handle", () => {
    const config = {
      outputMapping: [
        { source_port: "policies", source_column: "policy_id", output_path: "$[:].policy_id", enabled: true },
      ],
      outputFormat: "json",
    }
    render(<OutputEditor {...DEFAULT_PROPS} config={config} />, {
      allNodes: MULTI_FRAME_NODES,
      edges: MULTI_FRAME_EDGES,
    })
    expandFrame("output-frame-0")
    const select = screen.getByTestId("output-frame-0-row-0-column") as HTMLSelectElement
    const optionValues = Array.from(select.options).map((o) => o.value)
    // policies columns: policy_id, premium (drivers' driver_id must NOT appear)
    expect(optionValues).toContain("policy_id")
    expect(optionValues).toContain("premium")
    expect(optionValues).not.toContain("driver_id")
  })
})

describe("OutputEditor — rows: add / remove / enable / save", () => {
  it("Add row appends a blank enabled entry for that frame and saves v2", () => {
    const onUpdateSpy = vi.fn()
    render(
      <StatefulHarness
        initialConfig={{ outputMapping: [], outputFormat: "json" }}
        onUpdateSpy={onUpdateSpy}
        allNodes={MULTI_FRAME_NODES}
        edges={MULTI_FRAME_EDGES}
      />,
    )
    expandFrame("output-frame-0")
    fireEvent.click(screen.getByTestId("output-frame-0-add-row"))

    expect(onUpdateSpy).toHaveBeenCalledTimes(1)
    const arg = onUpdateSpy.mock.calls[0][0] as { outputMapping: Record<string, unknown>[] }
    expect(arg.outputMapping).toHaveLength(1)
    expect(arg.outputMapping[0]).toEqual({
      source_port: "policies",
      source_column: "",
      output_path: "",
      enabled: true,
    })
  })

  it("remove row drops the entry and saves the remainder", () => {
    const onUpdateSpy = vi.fn()
    render(
      <StatefulHarness
        initialConfig={{
          outputMapping: [
            { source_port: "policies", source_column: "policy_id", output_path: "$[:].policy_id", enabled: true },
            { source_port: "policies", source_column: "premium", output_path: "$[:].premium", enabled: true },
          ],
          outputFormat: "json",
        }}
        onUpdateSpy={onUpdateSpy}
        allNodes={MULTI_FRAME_NODES}
        edges={MULTI_FRAME_EDGES}
      />,
    )
    expandFrame("output-frame-0")
    fireEvent.click(screen.getByTestId("output-frame-0-row-0-remove"))

    expect(onUpdateSpy).toHaveBeenCalledTimes(1)
    const arg = onUpdateSpy.mock.calls[0][0] as { outputMapping: { source_column: string }[] }
    expect(arg.outputMapping).toHaveLength(1)
    expect(arg.outputMapping[0].source_column).toBe("premium")
  })

  it("per-row enable toggle flips just that entry's enabled flag", () => {
    const onUpdateSpy = vi.fn()
    render(
      <StatefulHarness
        initialConfig={{
          outputMapping: [
            { source_port: "policies", source_column: "policy_id", output_path: "$[:].policy_id", enabled: true },
          ],
          outputFormat: "json",
        }}
        onUpdateSpy={onUpdateSpy}
        allNodes={MULTI_FRAME_NODES}
        edges={MULTI_FRAME_EDGES}
      />,
    )
    expandFrame("output-frame-0")
    fireEvent.click(screen.getByTestId("output-frame-0-row-0-enabled"))

    const arg = onUpdateSpy.mock.calls[0][0] as { outputMapping: { enabled: boolean }[] }
    expect(arg.outputMapping[0].enabled).toBe(false)
  })

  it("per-frame enable toggle flips every row in the frame at once", () => {
    const onUpdateSpy = vi.fn()
    render(
      <StatefulHarness
        initialConfig={{
          outputMapping: [
            { source_port: "policies", source_column: "policy_id", output_path: "$[:].policy_id", enabled: true },
            { source_port: "policies", source_column: "premium", output_path: "$[:].premium", enabled: true },
            { source_port: "drivers", source_column: "driver_id", output_path: "$[:].driver_id", enabled: true },
          ],
          outputFormat: "json",
        }}
        onUpdateSpy={onUpdateSpy}
        allNodes={MULTI_FRAME_NODES}
        edges={MULTI_FRAME_EDGES}
      />,
    )
    // Toggle the policies frame off.
    fireEvent.click(screen.getByTestId("output-frame-0-enable"))

    const arg = onUpdateSpy.mock.calls[0][0] as {
      outputMapping: { source_port: string; enabled: boolean }[]
    }
    const policies = arg.outputMapping.filter((e) => e.source_port === "policies")
    const drivers = arg.outputMapping.filter((e) => e.source_port === "drivers")
    expect(policies.every((e) => e.enabled === false)).toBe(true)
    // drivers untouched
    expect(drivers.every((e) => e.enabled === true)).toBe(true)
  })

  it("editing a column + path round-trips the four-field v2 shape with a [:] path", () => {
    const onUpdateSpy = vi.fn()
    render(
      <StatefulHarness
        initialConfig={{
          outputMapping: [
            { source_port: "policies", source_column: "", output_path: "", enabled: true },
          ],
          outputFormat: "json",
        }}
        onUpdateSpy={onUpdateSpy}
        allNodes={MULTI_FRAME_NODES}
        edges={MULTI_FRAME_EDGES}
      />,
    )
    expandFrame("output-frame-0")

    fireEvent.change(screen.getByTestId("output-frame-0-row-0-column"), {
      target: { value: "policy_id" },
    })
    const pathInput = screen.getByTestId("output-frame-0-row-0-path") as HTMLInputElement
    fireEvent.change(pathInput, { target: { value: "$[:].policy_ref" } })
    fireEvent.blur(pathInput)

    const arg = onUpdateSpy.mock.calls[onUpdateSpy.mock.calls.length - 1][0] as {
      outputMapping: Record<string, unknown>[]
      outputFormat: string
    }
    expect(arg.outputFormat).toBe("json")
    const entry = arg.outputMapping[0]
    // Exactly the four persisted fields — no `status`, no v1 residue.
    expect(Object.keys(entry).sort()).toEqual(
      ["enabled", "output_path", "source_column", "source_port"].sort(),
    )
    expect(entry.source_column).toBe("policy_id")
    expect(entry.output_path).toBe("$[:].policy_ref")
    expect(String(entry.output_path)).toContain("[:]")
  })
})

describe("OutputEditor — v1 migration", () => {
  it("a v1 { fields } config shows the migration banner", () => {
    render(<OutputEditor {...DEFAULT_PROPS} config={{ fields: ["premium", "area"] }} />, {
      allNodes: SINGLE_PORT_NODES,
      edges: SINGLE_PORT_EDGES,
    })
    expect(screen.getByTestId("output-migration-banner")).toBeTruthy()
    expect(
      screen.getByText(/legacy format; saving will convert it/),
    ).toBeTruthy()
  })

  it("first Save on a v1 config writes v2: one entry per former field, [:] paths, enabled", () => {
    const onUpdateSpy = vi.fn()
    render(
      <StatefulHarness
        initialConfig={{ fields: ["premium", "area"] }}
        onUpdateSpy={onUpdateSpy}
        allNodes={SINGLE_PORT_NODES}
        edges={SINGLE_PORT_EDGES}
      />,
    )
    // The single-port frame migrates fields with source_port "".
    expandFrame("output-frame-0")
    // Adding a row triggers a writeBack of the (migrated) working copy plus
    // the new row — the migration is applied on the first save.
    fireEvent.click(screen.getByTestId("output-frame-0-add-row"))

    const arg = onUpdateSpy.mock.calls[0][0] as {
      outputMapping: { source_column: string; output_path: string; enabled: boolean; source_port: string }[]
    }
    const migrated = arg.outputMapping.filter((e) => e.source_column !== "")
    // Both former fields present, as [:] paths, enabled.
    expect(migrated.map((e) => e.source_column).sort()).toEqual(["area", "premium"])
    for (const e of migrated) {
      expect(e.output_path).toBe(`$[:].${e.source_column}`)
      expect(e.enabled).toBe(true)
    }
  })

  it("the migration banner disappears after the first Save", () => {
    const onUpdateSpy = vi.fn()
    render(
      <StatefulHarness
        initialConfig={{ fields: ["premium"] }}
        onUpdateSpy={onUpdateSpy}
        allNodes={SINGLE_PORT_NODES}
        edges={SINGLE_PORT_EDGES}
      />,
    )
    expect(screen.getByTestId("output-migration-banner")).toBeTruthy()
    expandFrame("output-frame-0")
    fireEvent.click(screen.getByTestId("output-frame-0-add-row"))
    // After save the config is now v2-shaped (and the banner is silenced).
    expect(screen.queryByTestId("output-migration-banner")).toBeNull()
  })
})

describe("OutputEditor — Infer (Inferred pills)", () => {
  it("Infer adds one row per frame column with [:] paths, flagged Inferred", () => {
    const onUpdateSpy = vi.fn()
    render(
      <StatefulHarness
        initialConfig={{ outputMapping: [], outputFormat: "json" }}
        onUpdateSpy={onUpdateSpy}
        allNodes={MULTI_FRAME_NODES}
        edges={MULTI_FRAME_EDGES}
      />,
    )
    expandFrame("output-frame-0")
    fireEvent.click(screen.getByTestId("output-frame-0-infer"))

    const arg = onUpdateSpy.mock.calls[0][0] as {
      outputMapping: { source_column: string; output_path: string }[]
    }
    // policies has two columns → two rows.
    expect(arg.outputMapping).toHaveLength(2)
    expect(arg.outputMapping.map((e) => e.source_column).sort()).toEqual([
      "policy_id",
      "premium",
    ])
    for (const e of arg.outputMapping) {
      expect(e.output_path).toBe(`$[:].${e.source_column}`)
    }
  })

  it("Infer maps a single-frame apiInput's columns from config (no preview/_columns needed)", () => {
    const onUpdateSpy = vi.fn()
    render(
      <StatefulHarness
        initialConfig={{ outputMapping: [], outputFormat: "json" }}
        onUpdateSpy={onUpdateSpy}
        allNodes={SINGLE_FRAME_API_NODES}
        edges={SINGLE_FRAME_API_EDGES}
      />,
    )
    expandFrame("output-frame-0")
    fireEvent.click(screen.getByTestId("output-frame-0-infer"))

    expect(onUpdateSpy).toHaveBeenCalled()
    const arg = onUpdateSpy.mock.calls[0][0] as {
      outputMapping: { source_column: string; output_path: string }[]
    }
    const cols = arg.outputMapping.map((e) => e.source_column).sort()
    // Selected config columns are mapped; the unselected one is excluded; no "" row.
    expect(cols).toEqual(["abi_code", "premium"])
    for (const e of arg.outputMapping) {
      expect(e.output_path).toBe(`$[:].${e.source_column}`)
    }
  })

  it("Inferred rows render the Inferred pill", () => {
    const onUpdateSpy = vi.fn()
    render(
      <StatefulHarness
        initialConfig={{ outputMapping: [], outputFormat: "json" }}
        onUpdateSpy={onUpdateSpy}
        allNodes={MULTI_FRAME_NODES}
        edges={MULTI_FRAME_EDGES}
      />,
    )
    expandFrame("output-frame-0")
    fireEvent.click(screen.getByTestId("output-frame-0-infer"))

    expect(screen.getByTestId("output-frame-0-row-0-pill").textContent).toBe("Inferred")
    expect(screen.getByTestId("output-frame-0-row-1-pill").textContent).toBe("Inferred")
  })

  it("editing an inferred row's path flips it to Confirmed (pill gone)", () => {
    const onUpdateSpy = vi.fn()
    render(
      <StatefulHarness
        initialConfig={{ outputMapping: [], outputFormat: "json" }}
        onUpdateSpy={onUpdateSpy}
        allNodes={MULTI_FRAME_NODES}
        edges={MULTI_FRAME_EDGES}
      />,
    )
    expandFrame("output-frame-0")
    fireEvent.click(screen.getByTestId("output-frame-0-infer"))
    expect(screen.getByTestId("output-frame-0-row-0-pill")).toBeTruthy()

    const pathInput = screen.getByTestId("output-frame-0-row-0-path") as HTMLInputElement
    fireEvent.change(pathInput, { target: { value: "$[:].policy_renamed" } })
    fireEvent.blur(pathInput)

    expect(screen.queryByTestId("output-frame-0-row-0-pill")).toBeNull()
  })
})

describe("OutputEditor — Clear", () => {
  it("Clear removes ALL of the frame's rows, leaving other frames untouched", () => {
    const onUpdateSpy = vi.fn()
    render(
      <StatefulHarness
        initialConfig={{
          outputMapping: [
            { source_port: "policies", source_column: "policy_id", output_path: "$[:].policy_id", enabled: true },
            { source_port: "policies", source_column: "premium", output_path: "$[:].premium", enabled: true },
            { source_port: "drivers", source_column: "driver_id", output_path: "$[:].driver_id", enabled: true },
          ],
          outputFormat: "json",
        }}
        onUpdateSpy={onUpdateSpy}
        allNodes={MULTI_FRAME_NODES}
        edges={MULTI_FRAME_EDGES}
      />,
    )
    expandFrame("output-frame-0")
    fireEvent.click(screen.getByTestId("output-frame-0-clear"))

    const arg = onUpdateSpy.mock.calls[onUpdateSpy.mock.calls.length - 1][0] as {
      outputMapping: { source_port: string }[]
    }
    // The policies frame is emptied; the drivers frame survives intact.
    expect(arg.outputMapping.some((e) => e.source_port === "policies")).toBe(false)
    expect(arg.outputMapping.filter((e) => e.source_port === "drivers")).toHaveLength(1)
    // The frame's rows are gone from the DOM and the empty-state hint shows.
    expect(screen.queryByTestId("output-frame-0-row-0")).toBeNull()
    expect(screen.getByText(/No fields mapped from this frame yet/)).toBeTruthy()
  })

  it("Clear is disabled when the frame has no rows", () => {
    render(
      <StatefulHarness
        initialConfig={{ outputMapping: [], outputFormat: "json" }}
        onUpdateSpy={vi.fn()}
        allNodes={MULTI_FRAME_NODES}
        edges={MULTI_FRAME_EDGES}
      />,
    )
    expandFrame("output-frame-0")
    const clear = screen.getByTestId("output-frame-0-clear") as HTMLButtonElement
    expect(clear.disabled).toBe(true)
  })

  it("clearing a frame drops its Inferred row-status (a fresh Infer re-pills cleanly)", () => {
    const onUpdateSpy = vi.fn()
    render(
      <StatefulHarness
        initialConfig={{ outputMapping: [], outputFormat: "json" }}
        onUpdateSpy={onUpdateSpy}
        allNodes={MULTI_FRAME_NODES}
        edges={MULTI_FRAME_EDGES}
      />,
    )
    expandFrame("output-frame-0")
    // Infer → two Inferred rows, then Clear them away.
    fireEvent.click(screen.getByTestId("output-frame-0-infer"))
    expect(screen.getByTestId("output-frame-0-row-0-pill")).toBeTruthy()
    fireEvent.click(screen.getByTestId("output-frame-0-clear"))
    expect(screen.queryByTestId("output-frame-0-row-0")).toBeNull()
    // A fresh Infer re-pills the rows (status keys for the cleared rows were
    // dropped, so the new rows are cleanly Inferred again).
    fireEvent.click(screen.getByTestId("output-frame-0-infer"))
    expect(screen.getByTestId("output-frame-0-row-0-pill").textContent).toBe("Inferred")
    expect(screen.getByTestId("output-frame-0-row-1-pill").textContent).toBe("Inferred")
  })
})

describe("OutputEditor — path validation", () => {
  it("an invalid output_path surfaces an error and is never committed", () => {
    const onUpdateSpy = vi.fn()
    render(
      <StatefulHarness
        initialConfig={{
          outputMapping: [
            { source_port: "policies", source_column: "policy_id", output_path: "$[:].policy_id", enabled: true },
          ],
          outputFormat: "json",
        }}
        onUpdateSpy={onUpdateSpy}
        allNodes={MULTI_FRAME_NODES}
        edges={MULTI_FRAME_EDGES}
      />,
    )
    expandFrame("output-frame-0")
    const pathInput = screen.getByTestId("output-frame-0-row-0-path") as HTMLInputElement
    // `[0]` index selector is rejected by the backend grammar.
    fireEvent.change(pathInput, { target: { value: "$[0].policy_id" } })
    fireEvent.blur(pathInput)

    expect(screen.getByTestId("output-frame-0-row-0-path-error")).toBeTruthy()
    expect(pathInput.getAttribute("aria-invalid")).toBe("true")
    // The invalid path never reached config.
    expect(onUpdateSpy).not.toHaveBeenCalled()
  })

  it("a grammatically valid path without a [:] selector is refused", () => {
    const onUpdateSpy = vi.fn()
    render(
      <StatefulHarness
        initialConfig={{
          outputMapping: [
            { source_port: "policies", source_column: "policy_id", output_path: "$[:].policy_id", enabled: true },
          ],
          outputFormat: "json",
        }}
        onUpdateSpy={onUpdateSpy}
        allNodes={MULTI_FRAME_NODES}
        edges={MULTI_FRAME_EDGES}
      />,
    )
    expandFrame("output-frame-0")
    const pathInput = screen.getByTestId("output-frame-0-row-0-path") as HTMLInputElement
    fireEvent.change(pathInput, { target: { value: "$.policy_id" } })
    fireEvent.blur(pathInput)

    expect(screen.getByTestId("output-frame-0-row-0-path-error")).toBeTruthy()
    expect(onUpdateSpy).not.toHaveBeenCalled()
  })

  it("two enabled columns mapping to the same path surface a conflict note", () => {
    render(
      <OutputEditor
        {...DEFAULT_PROPS}
        config={{
          outputMapping: [
            { source_port: "policies", source_column: "policy_id", output_path: "$[:].x", enabled: true },
            { source_port: "policies", source_column: "premium", output_path: "$[:].x", enabled: true },
          ],
          outputFormat: "json",
        }}
      />,
      { allNodes: MULTI_FRAME_NODES, edges: MULTI_FRAME_EDGES },
    )
    expandFrame("output-frame-0")
    expect(screen.getByTestId("output-frame-0-row-0-path-conflict")).toBeTruthy()
    expect(screen.getByTestId("output-frame-0-row-1-path-conflict")).toBeTruthy()
  })

  it("a scalar leaf and an array container at the same name do NOT conflict (array-flag aware)", () => {
    // `$[:].obj` (scalar leaf) vs `$[:].obj[:].x` (array container) differ at
    // the `obj` segment's array flag, so the backend `_prefix_comparable`
    // accepts them — the editor must not false-flag a conflict.
    render(
      <OutputEditor
        {...DEFAULT_PROPS}
        config={{
          outputMapping: [
            { source_port: "policies", source_column: "policy_id", output_path: "$[:].obj", enabled: true },
            { source_port: "policies", source_column: "premium", output_path: "$[:].obj[:].x", enabled: true },
          ],
          outputFormat: "json",
        }}
      />,
      { allNodes: MULTI_FRAME_NODES, edges: MULTI_FRAME_EDGES },
    )
    expandFrame("output-frame-0")
    expect(screen.queryByTestId("output-frame-0-row-0-path-conflict")).toBeNull()
    expect(screen.queryByTestId("output-frame-0-row-1-path-conflict")).toBeNull()
  })
})

describe("OutputEditor — source_port derivation (blocker)", () => {
  it("two NULL-handle sources persist DISTINCT, non-empty source_ports = sanitised labels", () => {
    const onUpdateSpy = vi.fn()
    render(
      <StatefulHarness
        initialConfig={{ outputMapping: [], outputFormat: "json" }}
        onUpdateSpy={onUpdateSpy}
        allNodes={TWO_SINGLE_PORT_NODES}
        edges={TWO_SINGLE_PORT_EDGES}
      />,
    )
    // Infer each single-port frame (each has one cached column).
    expandFrame("output-frame-0")
    fireEvent.click(screen.getByTestId("output-frame-0-infer"))
    expandFrame("output-frame-1")
    fireEvent.click(screen.getByTestId("output-frame-1-infer"))

    const arg = onUpdateSpy.mock.calls[onUpdateSpy.mock.calls.length - 1][0] as {
      outputMapping: { source_port: string; source_column: string }[]
    }
    const ports = arg.outputMapping.map((e) => e.source_port)
    // Distinct, non-empty, equal to the sanitised source labels ("Source A" →
    // "Source_A", "Source B" → "Source_B") — NOT "" for both.
    expect(ports).toContain("Source_A")
    expect(ports).toContain("Source_B")
    expect(ports).not.toContain("")
    // The two frames did not collapse onto one port.
    expect(new Set(ports).size).toBe(2)
    // Each port carries exactly its own column.
    const byPort = Object.fromEntries(arg.outputMapping.map((e) => [e.source_port, e.source_column]))
    expect(byPort["Source_A"]).toBe("alpha")
    expect(byPort["Source_B"]).toBe("beta")
  })

  it("a v1 single-port migration persists the sanitised frame id (not \"\")", () => {
    const onUpdateSpy = vi.fn()
    render(
      <StatefulHarness
        initialConfig={{ fields: ["premium"] }}
        onUpdateSpy={onUpdateSpy}
        allNodes={SINGLE_PORT_NODES}
        edges={SINGLE_PORT_EDGES}
      />,
    )
    expandFrame("output-frame-0")
    fireEvent.click(screen.getByTestId("output-frame-0-add-row"))
    const arg = onUpdateSpy.mock.calls[0][0] as {
      outputMapping: { source_port: string; source_column: string }[]
    }
    const migrated = arg.outputMapping.find((e) => e.source_column === "premium")
    // "Upstream Node" → "Upstream_Node".
    expect(migrated?.source_port).toBe("Upstream_Node")
  })
})

describe("OutputEditor — same-resolved-port collision (blocker)", () => {
  it("two sources resolving to the same port show a blocking banner", () => {
    render(<OutputEditor {...DEFAULT_PROPS} config={{ outputMapping: [], outputFormat: "json" }} />, {
      allNodes: COLLIDING_PORT_NODES,
      edges: COLLIDING_PORT_EDGES,
    })
    const banner = screen.getByTestId("output-duplicate-port-banner")
    expect(banner).toBeTruthy()
    expect(banner.textContent).toContain("shared")
  })

  it("no duplicate banner when resolved ports are distinct", () => {
    render(<OutputEditor {...DEFAULT_PROPS} config={{ outputMapping: [], outputFormat: "json" }} />, {
      allNodes: MULTI_FRAME_NODES,
      edges: MULTI_FRAME_EDGES,
    })
    expect(screen.queryByTestId("output-duplicate-port-banner")).toBeNull()
  })

  it("expand/collapse is isolated by edge.id even when ports collide", () => {
    // Both frames resolve to "shared"; expanding ONE must not expand the other
    // (expand state is keyed by edge.id, not the resolved port).
    render(<OutputEditor {...DEFAULT_PROPS} config={{ outputMapping: [], outputFormat: "json" }} />, {
      allNodes: COLLIDING_PORT_NODES,
      edges: COLLIDING_PORT_EDGES,
    })
    // Frame 0's expand-only affordances (Infer) appear once it is open.
    expandFrame("output-frame-0")
    expect(screen.getByTestId("output-frame-0-infer")).toBeTruthy()
    // Frame 1 stays collapsed — its Infer button is not rendered.
    expect(screen.queryByTestId("output-frame-1-infer")).toBeNull()
  })
})

describe("OutputEditor — Inferred pill survives earlier-row removal (major)", () => {
  it("removing an earlier row keeps the pill on the originally-inferred later row", () => {
    const onUpdateSpy = vi.fn()
    render(
      <StatefulHarness
        initialConfig={{ outputMapping: [], outputFormat: "json" }}
        onUpdateSpy={onUpdateSpy}
        allNodes={MULTI_FRAME_NODES}
        edges={MULTI_FRAME_EDGES}
      />,
    )
    expandFrame("output-frame-0")
    // Infer policies → two Inferred rows (policy_id @ row 0, premium @ row 1).
    fireEvent.click(screen.getByTestId("output-frame-0-infer"))
    expect(screen.getByTestId("output-frame-0-row-0-pill").textContent).toBe("Inferred")
    expect(screen.getByTestId("output-frame-0-row-1-pill").textContent).toBe("Inferred")

    // Confirm the FIRST row (edit its path) so the two rows differ in status:
    // row 0 → Confirmed (no pill), row 1 → still Inferred (pill).
    const row0Path = screen.getByTestId("output-frame-0-row-0-path") as HTMLInputElement
    fireEvent.change(row0Path, { target: { value: "$[:].policy_renamed" } })
    fireEvent.blur(row0Path)
    expect(screen.queryByTestId("output-frame-0-row-0-pill")).toBeNull()
    expect(screen.getByTestId("output-frame-0-row-1-pill").textContent).toBe("Inferred")

    // Remove the EARLIER row (row 0). The later (Inferred) row shifts down to
    // row 0 — and must KEEP its Inferred pill, not lose it or smear it.
    fireEvent.click(screen.getByTestId("output-frame-0-row-0-remove"))

    // Only one row remains; it is the originally-inferred `premium` row, still
    // pilled Inferred.
    const remaining = screen.getByTestId("output-frame-0-row-0-column") as HTMLSelectElement
    expect(remaining.value).toBe("premium")
    expect(screen.getByTestId("output-frame-0-row-0-pill").textContent).toBe("Inferred")
  })
})
