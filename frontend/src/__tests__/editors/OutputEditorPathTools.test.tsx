/**
 * Tests for the OUTPUT editor's full-scope FRONTEND path tools:
 *   - INLINE PATH-EDIT (pencil/double-click → in-place input; Enter/tick apply,
 *     Escape/cross/blur cancel) doing prefix substitution across a frame's rows;
 *   - the SHARED FrameTableActions wired into the per-frame column table
 *     (Copy/Share/Paste) and the top-level frames-paths table;
 *   - the pure path helper `substitutePrefix`.
 *
 * A stateful harness echoes onUpdate back into `config` (as NodePanel does) so
 * writeBack round-trips and multi-step interactions accumulate.
 */
import { describe, it, expect, vi, afterEach, beforeEach } from "vitest"
import { render as rtlRender, screen, fireEvent, cleanup, waitFor } from "@testing-library/react"
import { useState } from "react"
import OutputEditor from "../../panels/editors/OutputEditor"
import { substitutePrefix } from "../../panels/editors/outputPathTools"
import { GraphProvider } from "../../panels/GraphContext"
import type { SimpleNode, SimpleEdge } from "../../panels/editors"

const originalClipboardDescriptor = Object.getOwnPropertyDescriptor(navigator, "clipboard")
const originalSecure = Object.getOwnPropertyDescriptor(globalThis, "isSecureContext")

function installClipboard(writeText = vi.fn().mockResolvedValue(undefined)) {
  Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true })
  Object.defineProperty(globalThis, "isSecureContext", { value: true, configurable: true })
  return writeText
}
function restoreClipboard() {
  if (originalClipboardDescriptor) Object.defineProperty(navigator, "clipboard", originalClipboardDescriptor)
  else Reflect.deleteProperty(navigator, "clipboard")
  if (originalSecure) Object.defineProperty(globalThis, "isSecureContext", originalSecure)
  else Reflect.deleteProperty(globalThis as object, "isSecureContext")
}

afterEach(() => {
  cleanup()
  restoreClipboard()
  vi.restoreAllMocks()
})

// ─── Graph fixtures (mirrors OutputEditor.test.tsx) ───────────────

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

function StatefulHarness({
  initialConfig,
  onUpdateSpy,
}: {
  initialConfig: Record<string, unknown>
  onUpdateSpy: (k: string | Record<string, unknown>, v?: unknown) => void
}) {
  const [config, setConfig] = useState(initialConfig)
  return (
    <GraphProvider allNodes={MULTI_FRAME_NODES} edges={MULTI_FRAME_EDGES}>
      <OutputEditor
        config={config}
        nodeId="output_1"
        onUpdate={(k, v) => {
          onUpdateSpy(k, v)
          setConfig((prev) => (typeof k === "string" ? { ...prev, [k]: v } : { ...prev, ...k }))
        }}
      />
    </GraphProvider>
  )
}

const lastConfig = (spy: ReturnType<typeof vi.fn>) =>
  spy.mock.calls[spy.mock.calls.length - 1][0] as {
    outputMapping: { source_port: string; source_column: string; output_path: string; enabled: boolean }[]
  }

const expand = (p: string) => fireEvent.click(screen.getByTestId(`${p}-toggle`))

// ─── pure helpers ─────────────────────────────────────────────────

describe("substitutePrefix", () => {
  it("replaces the leading prefix run only when the path starts with it", () => {
    expect(substitutePrefix("$[:].old.city", "$[:].old", "$[:].new")).toBe("$[:].new.city")
    expect(substitutePrefix("$[:].old", "$[:].old", "$[:].new")).toBe("$[:].new")
    // No match → unchanged (the "misses columns higher in the tree" case).
    expect(substitutePrefix("$[:].other.city", "$[:].old", "$[:].new")).toBe("$[:].other.city")
  })
  it("an empty old prefix is a no-op", () => {
    expect(substitutePrefix("$[:].x", "", "$[:].y")).toBe("$[:].x")
  })
})

// ─── INLINE PATH-EDIT (pencil/double-click → in-place input) ──────

describe("OutputEditor — inline path edit", () => {
  beforeEach(() => installClipboard())

  const singlePolicyConfig = {
    outputMapping: [
      { source_port: "policies", source_column: "policy_id", output_path: "$[:].policy_id", enabled: true },
      { source_port: "policies", source_column: "premium", output_path: "$[:].premium", enabled: true },
      // a different frame must NOT be touched by a policies-frame edit.
      { source_port: "drivers", source_column: "driver_id", output_path: "$[:].driver_id", enabled: true },
    ],
    outputFormat: "json",
  }

  it("the pencil opens the inline editor in place (no drawer), seeded with the header path", () => {
    render(<StatefulHarness initialConfig={singlePolicyConfig} onUpdateSpy={vi.fn()} />)
    // Not editing: static header path + pencil; no input yet.
    expect(screen.getByTestId("output-frame-0-header-path")).toBeTruthy()
    expect(screen.queryByTestId("output-frame-0-path-edit-input")).toBeNull()
    fireEvent.click(screen.getByTestId("output-frame-0-path-edit-toggle"))
    // Editing: input replaces the pencil; the static text + pencil are gone.
    const input = screen.getByTestId("output-frame-0-path-edit-input") as HTMLInputElement
    expect(input.value).toBe("$[:]") // common root of the policies rows
    expect(screen.queryByTestId("output-frame-0-header-path")).toBeNull()
    expect(screen.queryByTestId("output-frame-0-path-edit-toggle")).toBeNull()
    expect(screen.getByTestId("output-frame-0-path-edit-apply")).toBeTruthy()
    expect(screen.getByTestId("output-frame-0-path-edit-cancel")).toBeTruthy()
  })

  it("double-clicking the path text ALSO opens the inline editor", () => {
    render(<StatefulHarness initialConfig={singlePolicyConfig} onUpdateSpy={vi.fn()} />)
    fireEvent.doubleClick(screen.getByTestId("output-frame-0-header-path"))
    expect(screen.getByTestId("output-frame-0-path-edit-input")).toBeTruthy()
  })

  it("typing + Enter applies the substitution; the header-path text updates to the new prefix", () => {
    const onUpdateSpy = vi.fn()
    render(<StatefulHarness initialConfig={singlePolicyConfig} onUpdateSpy={onUpdateSpy} />)
    fireEvent.click(screen.getByTestId("output-frame-0-path-edit-toggle"))
    const input = screen.getByTestId("output-frame-0-path-edit-input") as HTMLInputElement
    fireEvent.change(input, { target: { value: "$[:].policies" } })
    fireEvent.keyDown(input, { key: "Enter" })

    const cfg = lastConfig(onUpdateSpy)
    const byCol = Object.fromEntries(cfg.outputMapping.map((e) => [e.source_column, e.output_path]))
    expect(byCol["policy_id"]).toBe("$[:].policies.policy_id")
    expect(byCol["premium"]).toBe("$[:].policies.premium")
    // The drivers frame is untouched.
    expect(byCol["driver_id"]).toBe("$[:].driver_id")
    // Edit mode closed and the header-path text now reflects the new prefix
    // (recomputed common root of the rewritten policies rows).
    expect(screen.queryByTestId("output-frame-0-path-edit-input")).toBeNull()
    expect(screen.getByTestId("output-frame-0-header-path").textContent).toBe("$[:].policies")
  })

  it("the tick applies the substitution", () => {
    const onUpdateSpy = vi.fn()
    render(<StatefulHarness initialConfig={singlePolicyConfig} onUpdateSpy={onUpdateSpy} />)
    fireEvent.click(screen.getByTestId("output-frame-0-path-edit-toggle"))
    const input = screen.getByTestId("output-frame-0-path-edit-input") as HTMLInputElement
    fireEvent.change(input, { target: { value: "$[:].policies" } })
    fireEvent.click(screen.getByTestId("output-frame-0-path-edit-apply"))

    const cfg = lastConfig(onUpdateSpy)
    const byCol = Object.fromEntries(cfg.outputMapping.map((e) => [e.source_column, e.output_path]))
    expect(byCol["policy_id"]).toBe("$[:].policies.policy_id")
  })

  it("Escape cancels — no substitution, edit mode closes, paths unchanged", () => {
    const onUpdateSpy = vi.fn()
    render(<StatefulHarness initialConfig={singlePolicyConfig} onUpdateSpy={onUpdateSpy} />)
    fireEvent.click(screen.getByTestId("output-frame-0-path-edit-toggle"))
    const input = screen.getByTestId("output-frame-0-path-edit-input") as HTMLInputElement
    fireEvent.change(input, { target: { value: "$[:].policies" } })
    fireEvent.keyDown(input, { key: "Escape" })

    expect(onUpdateSpy).not.toHaveBeenCalled()
    expect(screen.queryByTestId("output-frame-0-path-edit-input")).toBeNull()
    expect(screen.getByTestId("output-frame-0-header-path").textContent).toBe("$[:]")
  })

  it("the cross cancels — no substitution", () => {
    const onUpdateSpy = vi.fn()
    render(<StatefulHarness initialConfig={singlePolicyConfig} onUpdateSpy={onUpdateSpy} />)
    fireEvent.click(screen.getByTestId("output-frame-0-path-edit-toggle"))
    const input = screen.getByTestId("output-frame-0-path-edit-input") as HTMLInputElement
    fireEvent.change(input, { target: { value: "$[:].policies" } })
    fireEvent.click(screen.getByTestId("output-frame-0-path-edit-cancel"))

    expect(onUpdateSpy).not.toHaveBeenCalled()
    expect(screen.getByTestId("output-frame-0-header-path").textContent).toBe("$[:]")
  })

  it("blur cancels WITHOUT applying", () => {
    const onUpdateSpy = vi.fn()
    render(<StatefulHarness initialConfig={singlePolicyConfig} onUpdateSpy={onUpdateSpy} />)
    fireEvent.click(screen.getByTestId("output-frame-0-path-edit-toggle"))
    const input = screen.getByTestId("output-frame-0-path-edit-input") as HTMLInputElement
    fireEvent.change(input, { target: { value: "$[:].policies" } })
    fireEvent.blur(input)

    expect(onUpdateSpy).not.toHaveBeenCalled()
    expect(screen.getByTestId("output-frame-0-header-path").textContent).toBe("$[:]")
  })
})

// ─── per-frame + top-level table actions ──────────────────────────

describe("OutputEditor — wired FrameTableActions", () => {
  beforeEach(() => installClipboard())

  it("the per-frame Copy emits the frame's rows as tab-separated text", async () => {
    const writeText = installClipboard()
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
      />,
    )
    expand("output-frame-0")
    fireEvent.click(screen.getByTestId("output-frame-0-table-copy"))
    await waitFor(() => expect(writeText).toHaveBeenCalledTimes(1))
    const tsv = writeText.mock.calls[0][0] as string
    expect(tsv).toBe("column\tpath\tenabled\npolicy_id\t$[:].policy_id\ttrue")
  })

  it("the per-frame Share emits this frame's rows as schema JSON", async () => {
    const writeText = installClipboard()
    const onUpdateSpy = vi.fn()
    render(
      <StatefulHarness
        initialConfig={{
          outputMapping: [
            { source_port: "policies", source_column: "policy_id", output_path: "$[:].policy_id", enabled: true },
            { source_port: "drivers", source_column: "driver_id", output_path: "$[:].driver_id", enabled: true },
          ],
          outputFormat: "json",
        }}
        onUpdateSpy={onUpdateSpy}
      />,
    )
    expand("output-frame-0")
    fireEvent.click(screen.getByTestId("output-frame-0-table-share"))
    await waitFor(() => expect(writeText).toHaveBeenCalledTimes(1))
    const json = JSON.parse(writeText.mock.calls[0][0] as string)
    // Only the policies frame's row is in this frame's schema (not drivers).
    expect(json.outputMapping).toHaveLength(1)
    expect(json.outputMapping[0].source_column).toBe("policy_id")
  })

  it("Paste-in replaces the frame's rows from tab-separated text", () => {
    const onUpdateSpy = vi.fn()
    render(
      <StatefulHarness
        initialConfig={{
          outputMapping: [
            { source_port: "policies", source_column: "old", output_path: "$[:].old", enabled: true },
            { source_port: "drivers", source_column: "driver_id", output_path: "$[:].driver_id", enabled: true },
          ],
          outputFormat: "json",
        }}
        onUpdateSpy={onUpdateSpy}
      />,
    )
    expand("output-frame-0")
    fireEvent.click(screen.getByTestId("output-frame-0-table-paste-toggle"))
    fireEvent.change(screen.getByTestId("output-frame-0-table-paste-input"), {
      target: { value: "column\tpath\tenabled\npolicy_id\t$[:].policy_id\ttrue\npremium\t$[:].premium\tfalse" },
    })
    fireEvent.click(screen.getByTestId("output-frame-0-table-paste-apply"))

    const cfg = lastConfig(onUpdateSpy)
    const policies = cfg.outputMapping.filter((e) => e.source_port === "policies")
    expect(policies.map((e) => e.source_column).sort()).toEqual(["policy_id", "premium"])
    // The header row was dropped; the enabled flag parsed (premium → false).
    const premium = policies.find((e) => e.source_column === "premium")
    expect(premium?.output_path).toBe("$[:].premium")
    expect(premium?.enabled).toBe(false)
    // drivers frame preserved.
    expect(cfg.outputMapping.some((e) => e.source_port === "drivers")).toBe(true)
  })

  it("the top-level frames-paths table exposes Copy/Share but no Paste-in", async () => {
    const writeText = installClipboard()
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
      />,
    )
    expect(screen.getByTestId("output-frames-table")).toBeTruthy()
    expect(screen.queryByTestId("output-frames-paste-toggle")).toBeNull()
    fireEvent.click(screen.getByTestId("output-frames-copy"))
    await waitFor(() => expect(writeText).toHaveBeenCalledTimes(1))
    const tsv = writeText.mock.calls[0][0] as string
    // One header + one row per frame (policies, drivers).
    expect(tsv.split("\n")[0]).toBe("frame\trows\troot_path")
    expect(tsv).toContain("policies")
    expect(tsv).toContain("drivers")
  })
})

function render(el: React.ReactElement) {
  return rtlRender(el)
}
