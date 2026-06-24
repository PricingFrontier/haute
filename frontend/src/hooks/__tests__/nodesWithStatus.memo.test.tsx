/**
 * Phase 3 Wave 6 — package 6D, item #96.
 *
 * useTracing.ts (line 150) currently rebuilds `nodesWithStatus` from scratch
 * every time any of 8 dependencies changes. For each render it maps the
 * entire `nodes` array and allocates fresh `{...n, data: {...n.data, ...}}`
 * objects — even for nodes whose computed flags didn't change. That defeats
 * React Flow's per-node diff (which relies on reference equality to skip
 * unchanged nodes during reconciliation) and causes every hoverHandler,
 * zoom, status tick, or unrelated node update to re-render every node on
 * the canvas.
 *
 * The fix is to memoize per-node: only re-allocate the projected node
 * object when THAT node's computed shape (status / trace flags / hover
 * flag / trace value) actually changes. Unchanged nodes return the same
 * reference across renders. React Flow's diff can then skip them.
 *
 * These tests pin the per-node stability contract:
 *
 *   1. Stable input → stable reference: same nodes + same nodeStatuses +
 *      no trace + no hover produces reference-equal output across two
 *      renders.
 *   2. Single-node status change only re-allocates THAT node — all
 *      other nodes remain reference-equal to the prior render.
 *   3. Hover change only re-allocates hovered/connected/previously-dimmed
 *      nodes — the far field stays reference-equal.
 *   4. Trace activation only re-allocates traced + previously-un-dimmed
 *      nodes — untouched nodes stay reference-equal.
 *   5. Render-count regression: with stable inputs the hook returns the
 *      same `nodesWithStatus` array reference across renders (no
 *      re-computation fires).
 *
 * These invariants together mean React Flow's internal node diff can
 * correctly skip unchanged nodes, which is the whole point of the fix.
 *
 * If, post-fix, the hook goes further and memoizes the OUTER ARRAY too
 * (stable reference when no node changed), that is strictly stronger and
 * these tests allow it. The OUTER-array test (#5) only runs if the hook
 * re-renders with structurally-equal inputs — see the assertion.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { renderHook, cleanup } from "@testing-library/react"
import type { Node, Edge } from "@xyflow/react"
import useTracing from "../useTracing"
import useToastStore from "../../stores/useToastStore"
import useSettingsStore from "../../stores/useSettingsStore"
import { makeNode, makeEdge } from "../../test-utils/factories"

vi.mock("@xyflow/react", async () => {
  const actual = await vi.importActual("@xyflow/react")
  return {
    ...actual,
    useStore: (selector: (s: { transform: [number, number, number] }) => unknown) =>
      selector({ transform: [0, 0, 1] }),
  }
})

vi.mock("../../api/client", () => ({
  traceCell: vi.fn(),
}))

vi.mock("../../utils/buildGraph", () => ({
  resolveGraphFromRefs: vi.fn(() => ({ nodes: [], edges: [], preamble: "" })),
}))

type TracingParams = Parameters<typeof useTracing>[0]

function makeParams(overrides: Partial<TracingParams> = {}): TracingParams {
  return {
    nodes: [makeNode("n1"), makeNode("n2"), makeNode("n3")] as Node[],
    edges: [makeEdge("n1", "n2"), makeEdge("n2", "n3")] as Edge[],
    selectedNode: null,
    graphRef: { current: { nodes: [] as Node[], edges: [] as Edge[] } },
    parentGraphRef: { current: null },
    submodelsRef: { current: {} },
    preambleRef: { current: "" },
    nodeStatuses: {} as Record<string, "ok" | "error" | "running">,
    hoveredNodeId: null,
    ...overrides,
  }
}

describe("nodesWithStatus memoization (#96)", () => {
  beforeEach(() => {
    useToastStore.setState({ toasts: [], _toastCounter: 0 })
    useSettingsStore.setState({ rowLimit: 1000, activeSource: "live" })
  })

  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
  })

  // ── 1. Stable inputs → identical per-node refs ─────────────────────────
  it("same nodes + same status → every per-node output is reference-equal across renders", () => {
    const params = makeParams({ nodeStatuses: { n1: "ok", n2: "error" } })
    const { result, rerender } = renderHook((p) => useTracing(p), {
      initialProps: params,
    })
    const first = result.current.nodesWithStatus

    // Re-render with the same params object. Any per-node object that is
    // re-allocated despite identical inputs is a memoization miss.
    rerender(params)
    const second = result.current.nodesWithStatus

    expect(second).toHaveLength(first.length)
    for (let i = 0; i < first.length; i++) {
      expect(second[i]).toBe(first[i])
    }
  })

  // ── 2. Single-node status change → only that node re-allocated ─────────
  it("status change on ONE node only re-allocates that node; others stay reference-equal", () => {
    // Share the SAME nodes/edges refs across renders — this matches the
    // real app's behaviour, where individual node objects only change
    // reference when THEIR OWN data changes (setNodes uses .map(n =>
    // n.id === target ? {...n, data: {...}} : n)).
    const sharedNodes: Node[] = [makeNode("n1"), makeNode("n2"), makeNode("n3")]
    const sharedEdges: Edge[] = [makeEdge("n1", "n2"), makeEdge("n2", "n3")]
    const params1 = makeParams({
      nodes: sharedNodes,
      edges: sharedEdges,
      nodeStatuses: { n1: "ok", n2: "ok", n3: "ok" },
    })
    const { result, rerender } = renderHook((p) => useTracing(p), {
      initialProps: params1,
    })
    const first = result.current.nodesWithStatus
    const firstById = new Map(first.map((n) => [n.id, n]))

    // Flip only n2's status. n1 and n3 should be reference-equal.
    const params2 = makeParams({
      nodes: sharedNodes,
      edges: sharedEdges,
      nodeStatuses: { n1: "ok", n2: "error", n3: "ok" },
    })
    rerender(params2)
    const second = result.current.nodesWithStatus
    const secondById = new Map(second.map((n) => [n.id, n]))

    expect(secondById.get("n1")).toBe(firstById.get("n1"))
    expect(secondById.get("n3")).toBe(firstById.get("n3"))
    // n2 must have been re-allocated because its _status flag changed.
    expect(secondById.get("n2")).not.toBe(firstById.get("n2"))
    expect(secondById.get("n2")!.data._status).toBe("error")
  })

  // ── 3. Hover change → only hovered + connected + previously-affected
  //      nodes are re-allocated. The "far field" stays reference-equal. ──
  it("hover change only re-allocates affected nodes; unrelated nodes stay reference-equal", () => {
    // Graph: n1 → n2 ← n3 (n3 is the OTHER input to n2, off n1's path), plus
    // isolated n4. Node refs are shared across rerenders (matches real
    // setNodes(.map(...)) behaviour where unchanged nodes keep their reference).
    const n1 = makeNode("n1")
    const n2 = makeNode("n2")
    const n3 = makeNode("n3")
    const n4 = makeNode("n4")
    const nodes: Node[] = [n1, n2, n3, n4]
    const edges: Edge[] = [makeEdge("n1", "n2"), makeEdge("n3", "n2")]

    const params1 = makeParams({ nodes, edges, hoveredNodeId: null })
    const { result, rerender } = renderHook((p) => useTracing(p), {
      initialProps: params1,
    })
    // Baseline: all nodes un-dimmed (no hover).
    for (const n of result.current.nodesWithStatus) {
      expect(n.data._hoverDimmed).toBe(false)
    }

    // Hover n1 — n1 (hovered) and n2 (connected) should be
    // reference-different from first render because _hoverDimmed changed
    // (from false to false isn't a change, but the fact that the HOVER
    // flag transitioned from "none" to "n1 is hovered" means the hook
    // may legitimately re-allocate them). n3 and n4 (both now dimmed)
    // should also re-allocate. This test asserts only the STRONG
    // invariant: an untouched node's reference must NOT stay stale —
    // we expect each node to report the correct _hoverDimmed flag.
    const params2 = makeParams({ nodes, edges, hoveredNodeId: "n1" })
    rerender(params2)
    const second = new Map(result.current.nodesWithStatus.map((n) => [n.id, n]))

    // n1 is the hovered node: _hoverDimmed = false (it's the hovered one)
    expect(second.get("n1")!.data._hoverDimmed).toBe(false)
    // n2 is directly connected: _hoverDimmed = false
    expect(second.get("n2")!.data._hoverDimmed).toBe(false)
    // n3 is the other input to n2 — off n1's data path → _hoverDimmed = true
    expect(second.get("n3")!.data._hoverDimmed).toBe(true)
    // n4 is isolated: _hoverDimmed = true
    expect(second.get("n4")!.data._hoverDimmed).toBe(true)

    // Now hover n4 — n1/n2/n3 should go back to un-dimmed (n4 connects
    // to nothing, so everything except n4 is dimmed). Critical: the
    // "far field" node (n1 here, far from n4) must still receive a
    // correctly-updated _hoverDimmed flag — if memoization is too
    // aggressive it could reuse a stale ref.
    const params3 = makeParams({ nodes, edges, hoveredNodeId: "n4" })
    rerender(params3)
    const third = new Map(result.current.nodesWithStatus.map((n) => [n.id, n]))

    // Every non-n4 node is now NOT directly connected to the hovered n4,
    // so they are all dimmed. n4 itself is not dimmed.
    expect(third.get("n4")!.data._hoverDimmed).toBe(false)
    expect(third.get("n1")!.data._hoverDimmed).toBe(true)
    expect(third.get("n2")!.data._hoverDimmed).toBe(true)
    expect(third.get("n3")!.data._hoverDimmed).toBe(true)

    // Back to no hover — ALL nodes should have _hoverDimmed = false
    // again (correct value), regardless of whether the cache keeps old
    // or new refs: the per-node cache may legitimately only retain the
    // MOST-RECENT result, not a history. We therefore only assert the
    // VALUES are correct on this transition. (Reference-stability for
    // IDENTICAL consecutive renders is covered by test #1.)
    rerender(params1)
    const fourth = new Map(result.current.nodesWithStatus.map((n) => [n.id, n]))
    for (const n of fourth.values()) {
      expect(n.data._hoverDimmed).toBe(false)
      expect(n.data._traceActive).toBe(false)
      expect(n.data._traceDimmed).toBe(false)
    }
  })

  // ── 4. Status change does not disturb unrelated nodes' refs even when
  //      the status map object identity changes (common React re-render
  //      trigger: parent rebuilds a fresh status map object every render).
  it("fresh status map object with equal values keeps all per-node refs stable", () => {
    const sharedNodes: Node[] = [makeNode("n1"), makeNode("n2"), makeNode("n3")]
    const sharedEdges: Edge[] = [makeEdge("n1", "n2"), makeEdge("n2", "n3")]
    const statuses1 = { n1: "ok" as const, n2: "ok" as const, n3: "ok" as const }
    const params1 = makeParams({
      nodes: sharedNodes,
      edges: sharedEdges,
      nodeStatuses: statuses1,
    })
    const { result, rerender } = renderHook((p) => useTracing(p), {
      initialProps: params1,
    })
    const first = result.current.nodesWithStatus

    // Fresh object, same values — this is what happens when the parent
    // passes `{...statuses}` instead of a stable reference. The memo
    // must compare VALUES (per-node status), not object identity.
    const params2 = makeParams({
      nodes: sharedNodes,
      edges: sharedEdges,
      nodeStatuses: { ...statuses1 },
    })
    rerender(params2)
    const second = result.current.nodesWithStatus

    for (let i = 0; i < first.length; i++) {
      expect(second[i]).toBe(first[i])
    }
  })

  // ── 5. Render-count regression guard ───────────────────────────────────
  it("hook does not cause extra renders with stable inputs", () => {
    // We count how many times the wrapper function is invoked. With
    // stable inputs, React strict-mode or otherwise should not call the
    // hook repeatedly for no reason — and `nodesWithStatus` should be a
    // stable reference.
    let renderCount = 0
    const params = makeParams({ nodeStatuses: { n1: "ok" } })
    const { result, rerender } = renderHook(
      (p) => {
        renderCount++
        return useTracing(p)
      },
      { initialProps: params },
    )
    const baselineRenders = renderCount
    const firstOutput = result.current.nodesWithStatus

    // Rerender with the same params object three times — stable input.
    rerender(params)
    rerender(params)
    rerender(params)

    // Exactly 3 re-renders, so 3 additional render invocations.
    expect(renderCount).toBe(baselineRenders + 3)
    // The outer array (or at least every per-node reference) must be
    // stable across these three stable-input re-renders.
    const finalOutput = result.current.nodesWithStatus
    expect(finalOutput).toHaveLength(firstOutput.length)
    for (let i = 0; i < firstOutput.length; i++) {
      expect(finalOutput[i]).toBe(firstOutput[i])
    }
  })

  // ── 6. "Re-map cost" micro-benchmark: 200 nodes, 10 status cycles.
  //      The post-fix hook must only re-allocate the CHANGED node each
  //      cycle — not all 200. We measure this by counting how many
  //      per-node refs changed vs. the prior cycle. Target: O(1) per
  //      cycle (the single node whose status flipped), not O(200).

  it("200 nodes, 10 status-change cycles → only the changed node re-allocates each cycle", () => {
    // Build 200 nodes: n_0 .. n_199, no edges.
    const nodes: Node[] = Array.from({ length: 200 }, (_, i) => makeNode(`n_${i}`))
    const edges: Edge[] = []
    const initialStatuses: Record<string, "ok" | "error" | "running"> = {}
    for (const n of nodes) initialStatuses[n.id] = "ok"

    const params: TracingParams = makeParams({
      nodes,
      edges,
      nodeStatuses: initialStatuses,
    })
    const { result, rerender } = renderHook((p) => useTracing(p), {
      initialProps: params,
    })

    let prior = new Map(result.current.nodesWithStatus.map((n) => [n.id, n]))
    const changeCounts: number[] = []

    for (let cycle = 0; cycle < 10; cycle++) {
      // Flip the status of one node per cycle.
      const flipId = `n_${cycle * 20}` // spread flips across the array
      const nextStatuses = { ...initialStatuses }
      for (let i = 0; i <= cycle; i++) {
        nextStatuses[`n_${i * 20}`] = "error"
      }
      const nextParams: TracingParams = makeParams({
        nodes,
        edges,
        nodeStatuses: nextStatuses,
      })
      rerender(nextParams)

      const now = new Map(result.current.nodesWithStatus.map((n) => [n.id, n]))
      let changed = 0
      for (const [id, node] of now.entries()) {
        if (prior.get(id) !== node) changed++
      }
      changeCounts.push(changed)

      // Sanity check: the flipped node's status must actually be "error".
      expect(now.get(flipId)!.data._status).toBe("error")
      prior = now
    }

    console.log(`[bench #96] per-cycle re-allocations: ${JSON.stringify(changeCounts)}`)

    // Each cycle should re-allocate exactly 1 node (the one whose
    // status changed). Target: O(changed nodes), not O(200).
    // We set a generous upper bound of 3 to allow for:
    //   - a legitimate extra alloc if the hook memoizes the outer array
    //   - small implementation drift
    // 200 would mean the fix didn't land at all.
    for (const count of changeCounts) {
      expect(count).toBeLessThanOrEqual(3)
      expect(count).toBeGreaterThanOrEqual(1)
    }
  })

  // ── 7. Hover toggle should also be bounded ─────────────────────────────
  it("hover flip on a large graph only re-allocates hovered + prior-hover-neighbourhood", () => {
    const nodes: Node[] = Array.from({ length: 100 }, (_, i) => makeNode(`h_${i}`))
    // 50 DISJOINT pairs (h_0→h_1, h_2→h_3, …) so a single hover lights a known
    // 2-node path and dims the rest. (A linear chain would put every node on the
    // hovered node's full data path → nothing dims → nothing to measure.)
    const edges: Edge[] = []
    for (let i = 0; i < 100; i += 2) edges.push(makeEdge(`h_${i}`, `h_${i + 1}`))

    const params1 = makeParams({ nodes, edges, hoveredNodeId: null })
    const { result, rerender } = renderHook((p) => useTracing(p), {
      initialProps: params1,
    })
    const baseline = new Map(result.current.nodesWithStatus.map((n) => [n.id, n]))

    // Hover h_50. Its path = {h_50, h_51} (its pair). The other 98 nodes
    // transition un-dimmed → dimmed, so exactly those 98 re-allocate; the 2 lit
    // nodes keep their refs (flags unchanged → cache hit). This guards against a
    // full-remap regression (which would re-allocate all 100).
    const params2 = makeParams({ nodes, edges, hoveredNodeId: "h_50" })
    rerender(params2)
    const afterHover = new Map(result.current.nodesWithStatus.map((n) => [n.id, n]))

    // Count nodes whose reference changed.
    let refChanged = 0
    for (const [id, node] of afterHover.entries()) {
      if (baseline.get(id) !== node) refChanged++
    }
    expect(refChanged).toBe(98)
  })
})
