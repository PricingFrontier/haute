import { describe, it, expect, vi, afterEach } from "vitest"
import { render, screen, waitFor, cleanup, fireEvent } from "@testing-library/react"

import ReadOnlyNodeConfig from "../ReadOnlyNodeConfig"
import { NODE_TYPES } from "../../utils/nodeTypes"

const observedInertCommitResults = vi.hoisted(() => vi.fn())

vi.mock("../../panels/LazyNodeEditors", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../panels/LazyNodeEditors")>()
  return {
    ...actual,
    ConstantEditor: ({
      onUpdate,
    }: {
      onUpdate: (keyOrUpdates: string | Record<string, unknown>, value?: unknown) => unknown
    }) => (
      <button
        type="button"
        data-testid="readonly-inert-commit-probe"
        onClick={() => observedInertCommitResults(onUpdate("value", 6))}
      >
        probe inert commit
      </button>
    ),
  }
})

afterEach(() => {
  cleanup()
  observedInertCommitResults.mockClear()
})

// Representatives whose lazy chunks resolve in jsdom: a no-context editor and
// one that reads useGraph() (proves the GraphProvider wiring). The heavier
// editors (CodeMirror/Databricks-backed: polars, sink, apiInput, …) share this
// exact prop wiring and are the same components NodePanel renders in production;
// their lazy chunks don't resolve under jsdom within a reasonable timeout, so
// they're covered by NodePanel's usage + browser verification rather than here.
const CASES: Array<{ type: string; config: Record<string, unknown> }> = [
  { type: NODE_TYPES.CONSTANT, config: { value: 5 } },
  { type: NODE_TYPES.OUTPUT, config: { outputMapping: [] } },
]

describe("ReadOnlyNodeConfig", () => {
  for (const { type, config } of CASES) {
    it(`renders the ${type} editor read-only without crashing`, async () => {
      render(<ReadOnlyNodeConfig nodeType={type} config={config} nodeId="n1" />)
      // The lazy editor resolves (the Suspense fallback clears) and nothing throws.
      await waitFor(
        () => expect(screen.queryByTestId("editor-loading")).not.toBeInTheDocument(),
        { timeout: 10000 },
      )
    })
  }

  it("supplies editors an inert successful commit callback", async () => {
    render(
      <ReadOnlyNodeConfig
        nodeType={NODE_TYPES.CONSTANT}
        config={{ value: 5 }}
        nodeId="n1"
      />,
    )

    fireEvent.click(await screen.findByTestId("readonly-inert-commit-probe"))

    expect(observedInertCommitResults).toHaveBeenCalledOnce()
    expect(observedInertCommitResults).toHaveBeenCalledWith({ ok: true })
  })

  it("falls back to a config dump for an unknown node type", async () => {
    render(<ReadOnlyNodeConfig nodeType="mysteryType" config={{ a: 1 }} nodeId="n1" />)
    await waitFor(() =>
      expect(screen.getByTestId("readonly-config-fallback")).toBeInTheDocument(),
    )
    expect(screen.getByTestId("readonly-config-fallback")).toHaveTextContent('"a": 1')
  })
})
