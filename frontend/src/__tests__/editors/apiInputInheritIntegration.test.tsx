/**
 * Interaction tests for the inherit / cascade integration half of the API
 * Input editor: the frames table, the three picker entry points, the shared
 * insert step (insert-at-top, origin, confirmed), confirm / confirm-all,
 * edit-confirms, the re-infer reconciliation through the confirm gate, the
 * pendingInferred dismissal-on-use, and the invalid-frame render-gate treatment.
 *
 * Persistence assertions follow the AGENTS contract (§UI Test Assertions):
 * gestures drive a NON-mocked stateful harness and assert on the persisted
 * object handed to onUpdate.
 */
import { describe, it, expect, vi, afterEach, beforeEach } from "vitest"
import { render, screen, fireEvent, cleanup, waitFor } from "@testing-library/react"
import { useState } from "react"
import ApiInputEditor from "../../panels/editors/ApiInputEditor"

afterEach(cleanup)

vi.mock("../../panels/editors/_shared", async () => {
  const actual = await vi.importActual("../../panels/editors/_shared")
  return {
    ...actual,
    FileBrowser: () => <div data-testid="file-browser" />,
    SchemaPreview: () => <div data-testid="schema-preview" />,
  }
})

const mockInferJsonCacheSchema = vi.fn()

vi.mock("../../api/client", () => ({
  fetchDatabricksSchema: vi.fn(),
  buildJsonCache: vi.fn(),
  getJsonCacheProgress: vi.fn().mockResolvedValue({ active: false }),
  getJsonCacheStatus: vi.fn().mockResolvedValue({ cached: false }),
  getJsonCacheStatusForSchema: vi.fn().mockResolvedValue({ cached: false }),
  deleteJsonCache: vi.fn(),
  inferJsonCacheSchema: (...args: unknown[]) => mockInferJsonCacheSchema(...args),
  ApiError: class ApiError extends Error {},
}))

vi.mock("../../hooks/useSchemaFetch", () => ({
  useSchemaFetch: () => ({
    schema: null,
    setSchema: vi.fn(),
    loading: false,
    fetchForPath: vi.fn(),
  }),
}))

beforeEach(() => {
  mockInferJsonCacheSchema.mockReset()
})

function StatefulHarness({
  initialConfig,
  onUpdateSpy = () => {},
}: {
  initialConfig: Record<string, unknown>
  onUpdateSpy?: (keyOrUpdates: string | Record<string, unknown>, value?: unknown) => void
}) {
  const [config, setConfig] = useState(initialConfig)
  return (
    <ApiInputEditor
      config={config}
      onUpdate={(keyOrUpdates: string | Record<string, unknown>, value?: unknown) => {
        onUpdateSpy(keyOrUpdates, value)
        setConfig((prev) =>
          typeof keyOrUpdates === "string"
            ? { ...prev, [keyOrUpdates]: value }
            : { ...prev, ...keyOrUpdates },
        )
        return { ok: true as const }
      }}
      accentColor="#10b981"
    />
  )
}

type RawColumn = {
  name: string
  path: string
  type?: string
  status?: string
  selected?: boolean
  origin?: string
}

function col(overrides: RawColumn): Record<string, unknown> {
  return { type: "str", status: "Inferred", selected: true, ...overrides }
}

/** Root frame with an id key, plus a nested orders frame — the canonical
 * inherit/cascade shape ($[:].policy_id can go down into orders). */
const NESTED = {
  tables: [
    {
      path: "$[:]",
      label: "policies",
      emit: true,
      columns: [
        col({ name: "policy_id", path: "$[:].policy_id", type: "int" }),
        col({ name: "premium", path: "$[:].premium", type: "float" }),
      ],
    },
    {
      path: "$[:].orders[:]",
      label: "orders",
      emit: true,
      columns: [col({ name: "sku", path: "$[:].orders[:].sku" })],
    },
  ],
}

const persistedTables = (spy: ReturnType<typeof vi.fn>) =>
  (spy.mock.calls.at(-1)![0] as { tables: Array<Record<string, unknown>> }).tables

describe("frames table", () => {
  it("renders one row per frame with label, path, and column counts; hidden with no frames", () => {
    render(<StatefulHarness initialConfig={NESTED} />)
    fireEvent.click(screen.getByTestId("api-input-frames-toggle"))
    expect(screen.getByTestId("api-input-frames-row-0")).toHaveTextContent("policies")
    expect(screen.getByTestId("api-input-frames-row-0-count")).toHaveTextContent("2 cols")
    expect(screen.getByTestId("api-input-frames-row-1")).toHaveTextContent("orders")
    expect(screen.getByTestId("api-input-frames-row-1-count")).toHaveTextContent("1 col")

    cleanup()
    render(<StatefulHarness initialConfig={{ tables: [] }} />)
    expect(screen.queryByTestId("api-input-frames-table")).toBeNull()
  })

  it("counts invalid columns and names an invalid frame path without suppressing the row (render-gate)", () => {
    const config = {
      tables: [
        {
          path: "$[:]",
          label: "policies",
          emit: true,
          columns: [
            col({ name: "", path: "$[:].policy_id" }), // blank name → invalid
            col({ name: "premium", path: "$[:].premium" }),
          ],
        },
        // Persisted blank-path frame: must surface, greyed, with the failure
        // named and its entry points disabled.
        { path: "", label: "orphan", emit: false, columns: [] },
      ],
    }
    render(<StatefulHarness initialConfig={config} />)
    fireEvent.click(screen.getByTestId("api-input-frames-toggle"))
    expect(screen.getByTestId("api-input-frames-row-0-count")).toHaveTextContent(
      "2 cols, 1 invalid",
    )
    expect(screen.getByTestId("api-input-frames-row-1-path-error")).toHaveTextContent(
      /path is required/i,
    )
    expect(screen.getByTestId("api-input-frames-row-1-add-keys")).toBeDisabled()
  })

  it("a top-level frame shows no inherit affordance; a deep frame does", () => {
    render(<StatefulHarness initialConfig={NESTED} />)
    fireEvent.click(screen.getByTestId("api-input-frames-toggle"))
    expect(screen.queryByTestId("api-input-frames-row-0-inherit")).toBeNull()
    expect(screen.getByTestId("api-input-frames-row-1-inherit")).toBeEnabled()
  })
})

describe("inherit (pull one key onto one frame)", () => {
  it("inserts the key at the top, name transported from the inventory, inherited origin, confirmed", () => {
    const onUpdateSpy = vi.fn()
    render(<StatefulHarness initialConfig={NESTED} onUpdateSpy={onUpdateSpy} />)
    fireEvent.click(screen.getByTestId("api-input-frames-toggle"))
    fireEvent.click(screen.getByTestId("api-input-frames-row-1-inherit"))
    fireEvent.click(
      screen
        .getByTestId("key-picker-candidate-$[:].policy_id")
        .querySelector("input")!,
    )
    fireEvent.click(screen.getByTestId("key-picker-confirm"))

    const tables = persistedTables(onUpdateSpy)
    const orders = tables[1] as { columns: RawColumn[] }
    expect(orders.columns[0]).toEqual({
      name: "policy_id",
      path: "$[:].policy_id",
      type: "int",
      status: "Confirmed",
      selected: true,
      levels: null,
      origin: "inherited",
      key: true,
    })
    // Existing column pushed below the inserted one.
    expect(orders.columns[1].name).toBe("sku")
    // Chip renders on the new row.
    expect(screen.getByTestId("api-input-table-1-col-0-origin")).toHaveTextContent(
      "inherited",
    )
  })

  it("a key already on the frame is shown ticked and disabled", () => {
    const config = structuredClone(NESTED)
    config.tables[1].columns.unshift(
      col({ name: "policy_id", path: "$[:].policy_id", type: "int", origin: "inherited" }),
    )
    render(<StatefulHarness initialConfig={config} />)
    fireEvent.click(screen.getByTestId("api-input-frames-toggle"))
    fireEvent.click(screen.getByTestId("api-input-frames-row-1-inherit"))
    const box = screen
      .getByTestId("key-picker-candidate-$[:].policy_id")
      .querySelector("input")!
    expect(box).toBeChecked()
    expect(box).toBeDisabled()
  })
})

describe("cascade (push keys into all deeper frames)", () => {
  it("pushes the selected key into every deeper frame in one confirm, and is idempotent", () => {
    const onUpdateSpy = vi.fn()
    render(<StatefulHarness initialConfig={NESTED} onUpdateSpy={onUpdateSpy} />)
    fireEvent.click(screen.getByTestId("api-input-cascade-btn"))
    fireEvent.click(
      screen
        .getByTestId("key-picker-candidate-$[:].policy_id")
        .querySelector("input")!,
    )
    fireEvent.click(screen.getByTestId("key-picker-confirm"))

    let orders = persistedTables(onUpdateSpy)[1] as { columns: RawColumn[] }
    expect(orders.columns.map((c) => c.name)).toEqual(["policy_id", "sku"])
    expect(orders.columns[0].origin).toBe("inherited")
    const callsAfterFirst = onUpdateSpy.mock.calls.length

    // Second cascade of the same key: fully-cascaded keys are disabled, and
    // no write happens without a selectable candidate.
    fireEvent.click(screen.getByTestId("api-input-cascade-btn"))
    const box = screen
      .getByTestId("key-picker-candidate-$[:].policy_id")
      .querySelector("input")!
    expect(box).toBeChecked()
    expect(box).toBeDisabled()
    fireEvent.click(screen.getByTestId("key-picker-cancel"))
    expect(onUpdateSpy.mock.calls.length).toBe(callsAfterFirst)
    orders = persistedTables(onUpdateSpy)[1] as { columns: RawColumn[] }
    expect(orders.columns.filter((c) => c.name === "policy_id")).toHaveLength(1)
  })

  it("a salted-name collision on the destination gets a numeric suffix on the salted form", () => {
    const config = structuredClone(NESTED)
    // Destination already has an unrelated column NAMED policy_id.
    config.tables[1].columns.push(
      col({ name: "policy_id", path: "$[:].orders[:].policy_id", status: "Confirmed" }),
    )
    const onUpdateSpy = vi.fn()
    render(<StatefulHarness initialConfig={config} onUpdateSpy={onUpdateSpy} />)
    fireEvent.click(screen.getByTestId("api-input-cascade-btn"))
    fireEvent.click(
      screen
        .getByTestId("key-picker-candidate-$[:].policy_id")
        .querySelector("input")!,
    )
    fireEvent.click(screen.getByTestId("key-picker-confirm"))
    const orders = persistedTables(onUpdateSpy)[1] as { columns: RawColumn[] }
    expect(orders.columns[0].name).toBe("policy_id_2")
    expect(orders.columns[0].path).toBe("$[:].policy_id")
  })
})

describe("add keys (inherit-attributes) and hand entry", () => {
  it("a hand-entered field requires BOTH path and type, then arrives manual + confirmed at the top", () => {
    const onUpdateSpy = vi.fn()
    render(<StatefulHarness initialConfig={NESTED} onUpdateSpy={onUpdateSpy} />)
    fireEvent.click(screen.getByTestId("api-input-frames-toggle"))
    fireEvent.click(screen.getByTestId("api-input-frames-row-1-add-keys"))

    const addBtn = screen.getByTestId("key-picker-manual-add")
    expect(addBtn).toBeDisabled()
    fireEvent.change(screen.getByTestId("key-picker-manual-path"), {
      target: { value: "$[:].orders[:].currency" },
    })
    expect(addBtn).toBeDisabled() // path alone is not a complete entry
    fireEvent.change(screen.getByTestId("key-picker-manual-type"), {
      target: { value: "str" },
    })
    expect(addBtn).toBeEnabled()
    fireEvent.click(addBtn)

    const orders = persistedTables(onUpdateSpy)[1] as { columns: RawColumn[] }
    expect(orders.columns[0]).toEqual({
      name: "currency",
      path: "$[:].orders[:].currency",
      type: "str",
      status: "Confirmed",
      selected: true,
      levels: null,
      origin: "manual",
      key: true,
    })
  })

  it("a hand-entered path transports the inventory's used name for it (salted leaf only as fallback)", () => {
    // The root frame carries $[:].policy_id under the user-edited name "pid";
    // hand-entering that path onto the orders frame must arrive as "pid" so the
    // field reads identically across frames (used-name transport, ruled
    // 2026-07-09 item 3.3), not as the salted leaf "policy_id".
    const config = {
      tables: [
        {
          path: "$[:]",
          label: "policies",
          emit: true,
          columns: [col({ name: "pid", path: "$[:].policy_id", type: "int" })],
        },
        {
          path: "$[:].orders[:]",
          label: "orders",
          emit: true,
          columns: [col({ name: "sku", path: "$[:].orders[:].sku" })],
        },
      ],
    }
    const onUpdateSpy = vi.fn()
    render(<StatefulHarness initialConfig={config} onUpdateSpy={onUpdateSpy} />)
    fireEvent.click(screen.getByTestId("api-input-frames-toggle"))
    fireEvent.click(screen.getByTestId("api-input-frames-row-1-add-keys"))
    fireEvent.change(screen.getByTestId("key-picker-manual-path"), {
      target: { value: "$[:].policy_id" },
    })
    fireEvent.change(screen.getByTestId("key-picker-manual-type"), {
      target: { value: "int" },
    })
    fireEvent.click(screen.getByTestId("key-picker-manual-add"))
    const orders = persistedTables(onUpdateSpy)[1] as { columns: RawColumn[] }
    expect(orders.columns[0]).toEqual({
      name: "pid",
      path: "$[:].policy_id",
      type: "int",
      status: "Confirmed",
      selected: true,
      levels: null,
      origin: "manual",
      key: true,
    })
  })

  it("rejects a sideways path with a visible error and no write", () => {
    const onUpdateSpy = vi.fn()
    render(<StatefulHarness initialConfig={NESTED} onUpdateSpy={onUpdateSpy} />)
    fireEvent.click(screen.getByTestId("api-input-frames-toggle"))
    fireEvent.click(screen.getByTestId("api-input-frames-row-1-add-keys"))
    fireEvent.change(screen.getByTestId("key-picker-manual-path"), {
      target: { value: "$[:].drivers[:].age" },
    })
    fireEvent.change(screen.getByTestId("key-picker-manual-type"), {
      target: { value: "int" },
    })
    expect(screen.getByTestId("key-picker-manual-error")).toHaveTextContent(
      /deeper than, or sideways/i,
    )
    expect(screen.getByTestId("key-picker-manual-add")).toBeDisabled()
    expect(onUpdateSpy).not.toHaveBeenCalled()
  })
})

describe("origin chips, confirm, confirm-all, edit-confirms", () => {
  it("confirm button confirms one column and disappears; confirm-all sweeps the frame", () => {
    const onUpdateSpy = vi.fn()
    render(<StatefulHarness initialConfig={NESTED} onUpdateSpy={onUpdateSpy} />)
    // Per-column confirm.
    fireEvent.click(screen.getByTestId("api-input-table-0-col-0-confirm"))
    let tables = persistedTables(onUpdateSpy)
    expect((tables[0] as { columns: RawColumn[] }).columns[0].status).toBe("Confirmed")
    expect(screen.queryByTestId("api-input-table-0-col-0-confirm")).toBeNull()
    // Confirm-all clears the rest and then disappears itself.
    fireEvent.click(screen.getByTestId("api-input-table-0-confirm-all"))
    tables = persistedTables(onUpdateSpy)
    expect(
      (tables[0] as { columns: RawColumn[] }).columns.every((c) => c.status === "Confirmed"),
    ).toBe(true)
    expect(screen.queryByTestId("api-input-table-0-confirm-all")).toBeNull()
  })

  it("editing a column's name confirms it and follows the row-id nomination", () => {
    const config = structuredClone(NESTED) as unknown as {
      tables: Array<Record<string, unknown>>
    }
    config.tables[0].row_id_column = "policy_id"
    const onUpdateSpy = vi.fn()
    render(<StatefulHarness initialConfig={config as never} onUpdateSpy={onUpdateSpy} />)
    const nameInput = screen.getByTestId("api-input-table-0-col-0-name")
    fireEvent.change(nameInput, { target: { value: "policy_ref" } })
    fireEvent.blur(nameInput)
    const table = persistedTables(onUpdateSpy)[0] as {
      row_id_column: string
      columns: RawColumn[]
    }
    expect(table.columns[0].status).toBe("Confirmed")
    expect(table.row_id_column).toBe("policy_ref")
  })
})

describe("re-infer reconciliation through the confirm gate", () => {
  const configWithPath = (tables: unknown[]) => ({ path: "data.json", tables })

  it("confirmed and blank columns survive; stale non-confirmed go; fresh append de-dup-suffixed; new frame gets cascaded keys prepended", async () => {
    const initial = configWithPath([
      {
        path: "$[:]",
        label: "policies",
        emit: true,
        columns: [
          // Confirmed, path gone from data: SURVIVES.
          col({ name: "kept", path: "$[:].kept", status: "Confirmed", origin: "manual" }),
          // Non-confirmed, path gone: REMOVED.
          col({ name: "stale", path: "$[:].stale" }),
          // Structurally blank (mid-typing): SURVIVES (ruled 2026-07-09).
          col({ name: "", path: "$[:].half_typed" }),
          // The cascaded key (inherited elsewhere marks it cascade-all).
          col({ name: "policy_id", path: "$[:].policy_id", type: "int", status: "Confirmed" }),
        ],
      },
      {
        path: "$[:].orders[:]",
        label: "orders",
        emit: true,
        columns: [
          col({
            name: "policy_id",
            path: "$[:].policy_id",
            type: "int",
            status: "Confirmed",
            origin: "inherited",
          }),
        ],
      },
    ])
    mockInferJsonCacheSchema.mockResolvedValue({
      tables: [
        {
          path: "$[:]",
          label: "policies",
          emit: true,
          columns: [
            col({ name: "policy_id", path: "$[:].policy_id", type: "int" }),
            // Fresh column whose name collides with the user's kept one: the
            // FRESH side gets the suffix.
            col({ name: "kept", path: "$[:].other_kept" }),
          ],
        },
        { path: "$[:].orders[:]", label: "orders", emit: true, columns: [] },
        {
          // Frame new in this inference — a valid cascade destination for
          // $[:].policy_id, which arrives prepended.
          path: "$[:].claims[:]",
          label: "claims",
          emit: true,
          columns: [col({ name: "amount", path: "$[:].claims[:].amount", type: "float" })],
        },
      ],
    })

    const onUpdateSpy = vi.fn()
    render(<StatefulHarness initialConfig={initial} onUpdateSpy={onUpdateSpy} />)
    fireEvent.click(screen.getByTestId("api-input-infer-btn"))
    await waitFor(() =>
      expect(screen.getByTestId("api-input-infer-confirm")).toBeInTheDocument(),
    )
    fireEvent.click(screen.getByTestId("api-input-infer-confirm"))

    const tables = persistedTables(onUpdateSpy) as Array<{
      path: string
      columns: RawColumn[]
    }>
    const policies = tables.find((t) => t.path === "$[:]")!
    // Non-keys land in data-model (JSON appearance) order per the 2026-07-09
    // ordering ruling: the fresh inference ranks policy_id and other_kept
    // first; the kept survivor follows; the blank row (no rankable path)
    // holds the tail. Survival semantics are unchanged — kept keeps its name,
    // the fresh side takes the suffix, the blank is spared.
    expect(policies.columns.map((c) => c.name)).toEqual([
      "policy_id", // confirmed, also in fresh
      "kept_2", // fresh side suffixed, never the user's column
      "kept", // confirmed survivor keeps its name
      "", // blank mid-typing column survives
    ])
    expect(policies.columns.map((c) => c.path)).toEqual([
      "$[:].policy_id",
      "$[:].other_kept",
      "$[:].kept",
      "$[:].half_typed",
    ])
    const claims = tables.find((t) => t.path === "$[:].claims[:]")!
    expect(claims.columns.map((c) => c.name)).toEqual(["policy_id", "amount"])
    expect(claims.columns[0].origin).toBe("inherited")
    expect(claims.columns[0].status).toBe("Confirmed")
  })

  it("using an entry point while the replace gate is open DISMISSES the gate (ruled 2026-07-14)", async () => {
    const initial = configWithPath(structuredClone(NESTED).tables)
    mockInferJsonCacheSchema.mockResolvedValue({ tables: structuredClone(NESTED).tables })
    render(<StatefulHarness initialConfig={initial} />)
    fireEvent.click(screen.getByTestId("api-input-infer-btn"))
    await waitFor(() =>
      expect(screen.getByTestId("api-input-infer-confirm")).toBeInTheDocument(),
    )
    // A key act is an implicit "keep my tables": the pending replace is
    // cancelled (banner gone) and the picker opens.
    expect(screen.getByTestId("api-input-cascade-btn")).toBeEnabled()
    fireEvent.click(screen.getByTestId("api-input-cascade-btn"))
    expect(screen.queryByTestId("api-input-infer-confirm-banner")).toBeNull()
    expect(screen.getByTestId("key-picker-modal")).toBeInTheDocument()
  })
})

describe("frame-path edit re-checks columns (none left stranded)", () => {
  it("re-pointing a frame's path flags columns that no longer sit on its branch", () => {
    const config = structuredClone(NESTED)
    render(<StatefulHarness initialConfig={config} />)
    expect(screen.queryByTestId("api-input-table-1-col-0-frame-error")).toBeNull()
    const pathInput = screen.getByTestId("api-input-table-1-path")
    fireEvent.change(pathInput, { target: { value: "$[:].drivers[:]" } })
    fireEvent.blur(pathInput)
    expect(screen.getByTestId("api-input-table-1-col-0-frame-error")).toHaveTextContent(
      /deeper than, or sideways/i,
    )
  })
})

describe("confirm-on-use (ruled 2026-07-09): keying a field confirms its carriers, source included", () => {
  it("cascading a key confirms the SOURCE column on its home frame", () => {
    const onUpdateSpy = vi.fn()
    render(<StatefulHarness initialConfig={NESTED} onUpdateSpy={onUpdateSpy} />)
    fireEvent.click(screen.getByTestId("api-input-cascade-btn"))
    fireEvent.click(
      screen.getByTestId("key-picker-candidate-$[:].policy_id").querySelector("input")!,
    )
    fireEvent.click(screen.getByTestId("key-picker-confirm"))
    const tables = persistedTables(onUpdateSpy) as Array<{ columns: RawColumn[] }>
    const source = tables[0].columns.find((c) => c.path === "$[:].policy_id")!
    expect(source.status).toBe("Confirmed")
    // ...and its origin pill is untouched (still inferred, now with the check).
    expect(source.origin).toBe("inferred")
    // The act also marks the source as a key (ruled 2026-07-09), and the key
    // glyph renders on both the source row and the destination row.
    expect((source as { key?: boolean }).key).toBe(true)
    expect(screen.getByTestId("api-input-table-0-col-0-key")).toBeInTheDocument()
    expect(screen.getByTestId("api-input-table-1-col-0-key")).toBeInTheDocument()
  })

  it("inheriting a key confirms the ancestor's source column too", () => {
    const onUpdateSpy = vi.fn()
    render(<StatefulHarness initialConfig={NESTED} onUpdateSpy={onUpdateSpy} />)
    fireEvent.click(screen.getByTestId("api-input-frames-toggle"))
    fireEvent.click(screen.getByTestId("api-input-frames-row-1-inherit"))
    fireEvent.click(
      screen.getByTestId("key-picker-candidate-$[:].policy_id").querySelector("input")!,
    )
    fireEvent.click(screen.getByTestId("key-picker-confirm"))
    const tables = persistedTables(onUpdateSpy) as Array<{ columns: RawColumn[] }>
    expect(tables[0].columns.find((c) => c.path === "$[:].policy_id")!.status).toBe(
      "Confirmed",
    )
  })
})

describe("no duplicate paths — ever (ruled 2026-07-09)", () => {
  const DRIVERS = {
    tables: [
      {
        path: "$[:]",
        label: "policies",
        emit: true,
        columns: [col({ name: "policy_id", path: "$[:].policy_id", type: "int" })],
      },
      {
        path: "$[:].drivers[:]",
        label: "drivers",
        emit: true,
        columns: [
          col({ name: "age", path: "$[:].drivers[:].age", type: "int" }),
          col({ name: "licence", path: "$[:].drivers[:].licence" }),
        ],
      },
    ],
  }

  it("hand-entering an EXISTING path greys the type, promotes the column to the top, confirms it, and keeps name/type/origin", () => {
    const onUpdateSpy = vi.fn()
    render(<StatefulHarness initialConfig={DRIVERS} onUpdateSpy={onUpdateSpy} />)
    fireEvent.click(screen.getByTestId("api-input-frames-toggle"))
    fireEvent.click(screen.getByTestId("api-input-frames-row-1-add-keys"))
    fireEvent.change(screen.getByTestId("key-picker-manual-path"), {
      target: { value: "$[:].drivers[:].licence" },
    })
    // Existing path: type select greyed with the already-exists treatment, Add
    // enabled without a type.
    expect(screen.getByTestId("key-picker-manual-type")).toBeDisabled()
    expect(screen.getByTestId("key-picker-manual-exists")).toBeInTheDocument()
    const addBtn = screen.getByTestId("key-picker-manual-add")
    expect(addBtn).toBeEnabled()
    fireEvent.click(addBtn)

    const drivers = persistedTables(onUpdateSpy)[1] as { columns: RawColumn[] }
    expect(drivers.columns.map((c) => c.path)).toEqual([
      "$[:].drivers[:].licence", // promoted to the top…
      "$[:].drivers[:].age",
    ])
    expect(drivers.columns.filter((c) => c.path === "$[:].drivers[:].licence")).toHaveLength(1)
    expect(drivers.columns[0]).toEqual({
      name: "licence", // …keeping its internal field-name,
      path: "$[:].drivers[:].licence",
      type: "str", // …its type,
      status: "Confirmed", // …confirmed,
      selected: true,
      levels: null,
      origin: "inferred", // …and its inferred pill — NOT manual.
      key: true, // …now tracked as a key.
    })
  })

  it("after keying a field, a re-infer does not re-add it in duplicate", async () => {
    const initial = { path: "data.json", tables: structuredClone(DRIVERS).tables }
    mockInferJsonCacheSchema.mockResolvedValue({
      tables: structuredClone(DRIVERS).tables,
    })
    const onUpdateSpy = vi.fn()
    render(<StatefulHarness initialConfig={initial} onUpdateSpy={onUpdateSpy} />)
    // Key licence via hand entry (promote+confirm), then re-infer + replace.
    fireEvent.click(screen.getByTestId("api-input-frames-toggle"))
    fireEvent.click(screen.getByTestId("api-input-frames-row-1-add-keys"))
    fireEvent.change(screen.getByTestId("key-picker-manual-path"), {
      target: { value: "$[:].drivers[:].licence" },
    })
    fireEvent.click(screen.getByTestId("key-picker-manual-add"))
    fireEvent.click(screen.getByTestId("key-picker-cancel"))
    fireEvent.click(screen.getByTestId("api-input-infer-btn"))
    await waitFor(() =>
      expect(screen.getByTestId("api-input-infer-confirm")).toBeInTheDocument(),
    )
    fireEvent.click(screen.getByTestId("api-input-infer-confirm"))
    const drivers = (persistedTables(onUpdateSpy) as Array<{ path: string; columns: RawColumn[] }>).find(
      (t) => t.path === "$[:].drivers[:]",
    )!
    expect(
      drivers.columns.filter((c) => c.path === "$[:].drivers[:].licence"),
    ).toHaveLength(1)
  })

  it("the picker checkbox route cannot duplicate either — an on-frame path is ticked and disabled", () => {
    render(<StatefulHarness initialConfig={DRIVERS} onUpdateSpy={vi.fn()} />)
    fireEvent.click(screen.getByTestId("api-input-frames-toggle"))
    fireEvent.click(screen.getByTestId("api-input-frames-row-1-add-keys"))
    const box = screen
      .getByTestId("key-picker-candidate-$[:].drivers[:].licence")
      .querySelector("input")!
    expect(box).toBeChecked()
    expect(box).toBeDisabled()
  })
})

describe("key toggle + keys-section ordering (ruled 2026-07-09)", () => {
  const THREE_LEVEL = {
    tables: [
      {
        path: "$[:]",
        label: "policies",
        emit: true,
        columns: [
          col({ name: "policy_id", path: "$[:].policy_id", type: "int" }),
          col({ name: "premium", path: "$[:].premium", type: "float" }),
        ],
      },
      {
        path: "$[:].orders[:]",
        label: "orders",
        emit: true,
        columns: [
          col({ name: "sku", path: "$[:].orders[:].sku" }),
          col({ name: "amount", path: "$[:].orders[:].amount", type: "float" }),
          // Pre-existing keys out of ruled order: a full-depth key ABOVE a
          // shallower one.
          col({
            name: "order_ref",
            path: "$[:].orders[:].order_ref",
            status: "Confirmed",
            origin: "manual",
            key: true,
          } as never),
          col({
            name: "policy_id",
            path: "$[:].policy_id",
            type: "int",
            status: "Confirmed",
            origin: "inherited",
            key: true,
          } as never),
        ],
      },
    ],
  }

  it("toggling a row ON confirms it and moves it into the keys section; OFF returns it to data-model order, still confirmed", () => {
    const onUpdateSpy = vi.fn()
    render(<StatefulHarness initialConfig={structuredClone(NESTED)} onUpdateSpy={onUpdateSpy} />)
    // NESTED orders frame: [sku]. Toggle sku ON.
    fireEvent.click(screen.getByTestId("api-input-table-1-col-0-key"))
    let orders = persistedTables(onUpdateSpy)[1] as { columns: RawColumn[] }
    const sku = orders.columns.find((c) => c.name === "sku")! as RawColumn & { key?: boolean }
    expect(sku.key).toBe(true)
    expect(sku.status).toBe("Confirmed")
    // Toggle OFF: key cleared, confirmation NOT revoked.
    fireEvent.click(screen.getByTestId("api-input-table-1-col-0-key"))
    orders = persistedTables(onUpdateSpy)[1] as { columns: RawColumn[] }
    const sku2 = orders.columns.find((c) => c.name === "sku")! as RawColumn & { key?: boolean }
    expect(sku2.key).toBe(false)
    expect(sku2.status).toBe("Confirmed")
  })

  it("a manually-added field is promotable to a key via the toggle (ruled 2026-07-09)", () => {
    const config = structuredClone(NESTED)
    // A hand-entered spelling variant, exactly the tested `license` case:
    // manual origin, full-depth, not a key.
    config.tables[1].columns.push(
      col({
        name: "license",
        path: "$[:].orders[:].license",
        status: "Confirmed",
        origin: "manual",
      }),
    )
    const onUpdateSpy = vi.fn()
    render(<StatefulHarness initialConfig={config} onUpdateSpy={onUpdateSpy} />)
    fireEvent.click(screen.getByTestId("api-input-table-1-col-1-key"))
    const orders = persistedTables(onUpdateSpy)[1] as { columns: RawColumn[] }
    const lic = orders.columns.find((c) => c.name === "license")! as RawColumn & {
      key?: boolean
      origin?: string
    }
    expect(lic.key).toBe(true)
    expect(lic.origin).toBe("manual") // promotion doesn't rewrite its origin
    // It joined the keys section (top), ahead of the non-key sku.
    expect(orders.columns.map((c) => c.name)).toEqual(["license", "sku"])
  })

  it("any key act re-sorts the keys section: shallower keys first, full-depth keys after in add order, non-keys in data-model order", () => {
    const onUpdateSpy = vi.fn()
    render(
      <StatefulHarness initialConfig={structuredClone(THREE_LEVEL)} onUpdateSpy={onUpdateSpy} />,
    )
    // Toggle `amount` (full-depth) into the keys — this reorders the frame.
    fireEvent.click(screen.getByTestId("api-input-table-1-col-1-key"))
    const orders = persistedTables(onUpdateSpy)[1] as { columns: RawColumn[] }
    expect(orders.columns.map((c) => c.name)).toEqual([
      "policy_id", // shallower key rises above the full-depth ones
      "order_ref", // full-depth keys in add order…
      "amount", // …the newly toggled one last
      "sku", // non-keys in data-model order
    ])
  })
})

describe("Add Column arrives blank and inherits its name from the committed path (ruled 2026-07-09)", () => {
  it("creates a blank row (flagged invalid), then derives the name when a valid path commits", () => {
    const onUpdateSpy = vi.fn()
    render(<StatefulHarness initialConfig={structuredClone(NESTED)} onUpdateSpy={onUpdateSpy} />)
    fireEvent.click(screen.getByTestId("api-input-table-1-add-col"))
    let orders = persistedTables(onUpdateSpy)[1] as { columns: RawColumn[] }
    const blank = orders.columns.find((c) => c.path === "")! as RawColumn & {
      origin?: string
      status?: string
    }
    expect(blank.name).toBe("")
    expect(blank.origin).toBe("manual")
    expect(blank.status).toBe("Inferred") // unconfirmed until completed
    const blankIdx = orders.columns.findIndex((c) => c.path === "")
    // Commit a dotted path onto the blank row: name derives from the salted
    // leaf (meta.currency → meta_currency) and edit-confirms fires.
    const pathInput = screen.getByTestId(`api-input-table-1-col-${blankIdx}-path`)
    fireEvent.change(pathInput, { target: { value: "$[:].orders[:].meta.currency" } })
    fireEvent.blur(pathInput)
    orders = persistedTables(onUpdateSpy)[1] as { columns: RawColumn[] }
    const filled = orders.columns.find(
      (c) => c.path === "$[:].orders[:].meta.currency",
    )! as RawColumn & { status?: string }
    expect(filled.name).toBe("meta_currency")
    expect(filled.status).toBe("Confirmed")
    // Completing the row keeps it a plain manual column: Add Column is schema
    // entry, not key machinery — the key marker (ruled 2026-07-09 item 14c)
    // covers cascade / inherit / add-keys picks / picker hand entries, and a
    // completed row can still be keyed via its row toggle.
    expect((filled as RawColumn & { origin?: string }).origin).toBe("manual")
    expect((filled as RawColumn & { key?: boolean }).key).not.toBe(true)
  })
})

describe("hand-entry extras (ruled 2026-07-09)", () => {
  it("Add & close adds the field and closes the dialog in one click", () => {
    const onUpdateSpy = vi.fn()
    render(<StatefulHarness initialConfig={structuredClone(NESTED)} onUpdateSpy={onUpdateSpy} />)
    fireEvent.click(screen.getByTestId("api-input-frames-toggle"))
    fireEvent.click(screen.getByTestId("api-input-frames-row-1-add-keys"))
    fireEvent.change(screen.getByTestId("key-picker-manual-path"), {
      target: { value: "$[:].orders[:].currency" },
    })
    fireEvent.change(screen.getByTestId("key-picker-manual-type"), {
      target: { value: "str" },
    })
    fireEvent.click(screen.getByTestId("key-picker-manual-add-close"))
    expect(screen.queryByTestId("key-picker-modal")).toBeNull()
    const orders = persistedTables(onUpdateSpy)[1] as { columns: RawColumn[] }
    expect(orders.columns.some((c) => c.path === "$[:].orders[:].currency")).toBe(true)
  })

  it("the already-exists notice names the key status when the existing field is a key", () => {
    const config = structuredClone(NESTED)
    config.tables[1].columns.unshift(
      col({
        name: "policy_id",
        path: "$[:].policy_id",
        type: "int",
        status: "Confirmed",
        origin: "inherited",
        key: true,
      } as never),
    )
    render(<StatefulHarness initialConfig={config} />)
    fireEvent.click(screen.getByTestId("api-input-frames-toggle"))
    fireEvent.click(screen.getByTestId("api-input-frames-row-1-add-keys"))
    fireEvent.change(screen.getByTestId("key-picker-manual-path"), {
      target: { value: "$[:].policy_id" },
    })
    expect(screen.getByTestId("key-picker-manual-exists")).toHaveTextContent(
      /already added as a key/i,
    )
  })
})

describe("$value scalar-array frames accept broadcast keys (iteration-2 pin, backend W1 pattern)", () => {
  /** Root frame plus a scalar-array child ($[:].tags[:] whose one own-depth
   * column is the reserved $value leaf). The engine explicitly supports
   * ancestor columns on such a frame — they distribute the parent value over
   * the scalar rows — so the editor offers it as a cascade/inherit target. */
  const SCALAR_CHILD = {
    tables: [
      {
        path: "$[:]",
        label: "policies",
        emit: true,
        columns: [col({ name: "policy_id", path: "$[:].policy_id", type: "int" })],
      },
      {
        path: "$[:].tags[:]",
        label: "tags",
        emit: true,
        columns: [col({ name: "tag", path: "$[:].tags[:].$value" })],
      },
    ],
  }

  it("cascade pushes a shallower key onto the $value frame; the $value column is untouched", () => {
    const onUpdateSpy = vi.fn()
    render(<StatefulHarness initialConfig={structuredClone(SCALAR_CHILD)} onUpdateSpy={onUpdateSpy} />)
    fireEvent.click(screen.getByTestId("api-input-frames-toggle"))
    fireEvent.click(screen.getByTestId("api-input-cascade-btn"))
    fireEvent.click(
      screen.getByTestId("key-picker-candidate-$[:].policy_id").querySelector("input")!,
    )
    fireEvent.click(screen.getByTestId("key-picker-confirm"))
    const tags = persistedTables(onUpdateSpy)[1] as { columns: RawColumn[] }
    expect(tags.columns[0]).toEqual({
      name: "policy_id",
      path: "$[:].policy_id",
      type: "int",
      status: "Confirmed",
      selected: true,
      levels: null,
      origin: "inherited",
      key: true,
    })
    // The scalar column survives unrenamed and unconfirmed — the cascade only
    // added the broadcast key.
    expect(tags.columns[1]).toMatchObject({
      name: "tag",
      path: "$[:].tags[:].$value",
      status: "Inferred",
    })
  })

  it("the $value frame offers the inherit entry point, and the $value column itself is never a candidate", () => {
    render(<StatefulHarness initialConfig={structuredClone(SCALAR_CHILD)} />)
    fireEvent.click(screen.getByTestId("api-input-frames-toggle"))
    const inheritBtn = screen.getByTestId("api-input-frames-row-1-inherit")
    expect(inheritBtn).toBeEnabled()
    fireEvent.click(inheritBtn)
    expect(screen.getByTestId("key-picker-candidate-$[:].policy_id")).toBeInTheDocument()
    // The reserved leaf is a whole-frame scalar mode, not key material.
    expect(screen.queryByTestId("key-picker-candidate-$[:].tags[:].$value")).toBeNull()
  })
})

describe("walk-up collision naming (ruled 2026-07-14)", () => {
  it("an inherited key whose transported name collides gets its enclosing array level prepended, not a numeric suffix", () => {
    // items already has its own (different-path) "sku"; inheriting the orders
    // frame's "sku" walks up: sku → orders_sku.
    const config = {
      tables: [
        {
          path: "$[:].orders[:]",
          label: "orders",
          emit: true,
          columns: [col({ name: "sku", path: "$[:].orders[:].sku" })],
        },
        {
          path: "$[:].orders[:].items[:]",
          label: "items",
          emit: true,
          columns: [col({ name: "sku", path: "$[:].orders[:].items[:].sku" })],
        },
      ],
    }
    const onUpdateSpy = vi.fn()
    render(<StatefulHarness initialConfig={config} onUpdateSpy={onUpdateSpy} />)
    fireEvent.click(screen.getByTestId("api-input-frames-toggle"))
    fireEvent.click(screen.getByTestId("api-input-frames-row-1-inherit"))
    fireEvent.click(
      screen.getByTestId("key-picker-candidate-$[:].orders[:].sku").querySelector("input")!,
    )
    fireEvent.click(screen.getByTestId("key-picker-confirm"))
    const items = persistedTables(onUpdateSpy)[1] as { columns: RawColumn[] }
    expect(items.columns[0].name).toBe("orders_sku")
    expect(items.columns[0].path).toBe("$[:].orders[:].sku")
  })

  it("a root-level key has no levels to prepend and falls back to the numeric suffix", () => {
    // Covered by the existing name-collision cascade test (policy_id_2); this
    // pin documents the fallback deliberately.
    const config = structuredClone(NESTED)
    config.tables[1].columns.unshift(
      col({ name: "policy_id", path: "$[:].orders[:].policy_id" }),
    )
    const onUpdateSpy = vi.fn()
    render(<StatefulHarness initialConfig={config} onUpdateSpy={onUpdateSpy} />)
    fireEvent.click(screen.getByTestId("api-input-frames-toggle"))
    fireEvent.click(screen.getByTestId("api-input-frames-row-1-inherit"))
    fireEvent.click(
      screen.getByTestId("key-picker-candidate-$[:].policy_id").querySelector("input")!,
    )
    fireEvent.click(screen.getByTestId("key-picker-confirm"))
    const orders = persistedTables(onUpdateSpy)[1] as { columns: RawColumn[] }
    const added = orders.columns.find((c) => c.path === "$[:].policy_id")!
    expect(added.name).toBe("policy_id_2")
  })
})

describe("cross-frame name-collision warning (ruled 2026-07-14)", () => {
  it("shades a name carried by a DIFFERENT path elsewhere; same-path reuse (name transport) is clean", () => {
    const config = {
      tables: [
        {
          path: "$[:]",
          label: "policies",
          emit: true,
          columns: [col({ name: "id", path: "$[:].policy_id", type: "int" })],
        },
        {
          path: "$[:].orders[:]",
          label: "orders",
          emit: true,
          columns: [
            // Same name, DIFFERENT path → collision warning on both carriers.
            col({ name: "id", path: "$[:].orders[:].order_id", type: "int" }),
            // Same name AND same path as the root's id would be transport; use
            // a distinct clean column to prove absence of false positives.
            col({ name: "sku", path: "$[:].orders[:].sku" }),
          ],
        },
      ],
    }
    render(<StatefulHarness initialConfig={config} />)
    expect(screen.getByTestId("api-input-table-0-col-0-name-collision")).toBeInTheDocument()
    expect(screen.getByTestId("api-input-table-1-col-0-name-collision")).toBeInTheDocument()
    expect(screen.queryByTestId("api-input-table-1-col-1-name-collision")).toBeNull()

    // The transport case: the same path carried under the same name on two
    // frames must NOT warn.
    cleanup()
    const transported = {
      tables: [
        {
          path: "$[:]",
          label: "policies",
          emit: true,
          columns: [col({ name: "policy_id", path: "$[:].policy_id", type: "int" })],
        },
        {
          path: "$[:].orders[:]",
          label: "orders",
          emit: true,
          columns: [col({ name: "policy_id", path: "$[:].policy_id", type: "int" })],
        },
      ],
    }
    render(<StatefulHarness initialConfig={transported} />)
    expect(screen.queryByTestId("api-input-table-0-col-0-name-collision")).toBeNull()
    expect(screen.queryByTestId("api-input-table-1-col-0-name-collision")).toBeNull()
  })
})

describe("salting toggle", () => {
  it("with salting off a dotted leaf falls back to its bare final segment", () => {
    const config = {
      tables: [
        {
          path: "$[:]",
          label: "policies",
          emit: true,
          // Inventory name comes from the column itself, so hand entry (which
          // derives from the leaf) is the surface that shows the toggle.
          columns: [col({ name: "premium", path: "$[:].premium" })],
        },
        { path: "$[:].orders[:]", label: "orders", emit: true, columns: [] },
      ],
    }
    const onUpdateSpy = vi.fn()
    render(<StatefulHarness initialConfig={config} onUpdateSpy={onUpdateSpy} />)
    fireEvent.click(screen.getByTestId("api-input-salt-toggle")) // off
    fireEvent.click(screen.getByTestId("api-input-frames-toggle"))
    fireEvent.click(screen.getByTestId("api-input-frames-row-1-add-keys"))
    fireEvent.change(screen.getByTestId("key-picker-manual-path"), {
      target: { value: "$[:].orders[:].meta.currency" },
    })
    fireEvent.change(screen.getByTestId("key-picker-manual-type"), {
      target: { value: "str" },
    })
    fireEvent.click(screen.getByTestId("key-picker-manual-add"))
    const orders = persistedTables(onUpdateSpy)[1] as { columns: RawColumn[] }
    expect(orders.columns[0].name).toBe("currency") // bare leaf, not meta_currency
  })
})
