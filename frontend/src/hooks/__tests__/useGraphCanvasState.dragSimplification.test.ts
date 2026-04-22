/**
 * Phase 2 Package 2D-6 — useGraphCanvasState drag handling simplification.
 *
 * The current implementation (useGraphCanvasState.ts:61-88) uses an `isDragging` ref
 * that persists across multiple NodeChange effects to decide whether to push
 * a snapshot at drag-start (and suppress snapshots mid-drag).  The refactor
 * will collapse this to a single per-effect inspection of the NodeChange[]
 * array: if the batch contains ONLY position changes whose `dragging` flag
 * is set (true or false), skip the snapshot; otherwise snapshot.
 *
 * These tests pin the observable behaviour so the refactor is a pure
 * simplification — no user-visible change.  Specifically:
 *
 *   1. A realistic ~60-event drag produces exactly one snapshot.
 *   2. A pure select change produces zero snapshots.
 *   3. An edge add change produces one snapshot.
 *   4. Batched structural edge changes produce exactly one snapshot.
 *   5. Ctrl+Z (undo) after a drag restores the pre-drag position.
 *   6. WebSocket-driven sync (position change without `dragging`) produces
 *      zero snapshots — regression guard for Phase 1H item #8.
 */
import { describe, it, expect, afterEach, vi } from "vitest"
import { renderHook, act, cleanup } from "@testing-library/react"
import type { NodeChange, EdgeChange } from "@xyflow/react"
import useGraphCanvasState from "../useGraphCanvasState"
import { makeNode, makeEdge } from "../../test-utils/factories"

/**
 * Count how many snapshots the hook has pushed by undoing until `canUndo`
 * flips to false.  After the loop, we've emptied `past`, so the caller
 * should use a fresh hook render if further operations are needed.
 */
function countSnapshots(
  result: { current: ReturnType<typeof useGraphCanvasState> },
): number {
  let count = 0
  while (result.current.canUndo) {
    act(() => {
      result.current.undo()
    })
    count++
    if (count > 200) throw new Error("undo loop exceeded safety bound")
  }
  return count
}

describe("useGraphCanvasState — Phase 2D-6 drag simplification", () => {
  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  // ────────────────────────────────────────────────────────────────────
  // 1. Full drag (~60 events) → exactly one snapshot.
  //
  // A real React Flow drag emits many `position` + `dragging:true` events
  // as the user moves the mouse, then one final `dragging:false` event.
  // The hook must batch these into a single undo entry so Ctrl+Z returns
  // to the pre-drag position in one step, not 60.
  // ────────────────────────────────────────────────────────────────────
  it("a single drag of ~60 position events produces exactly one snapshot", () => {
    const node = makeNode("A", "polars", { position: { x: 0, y: 0 } })
    const { result } = renderHook(() => useGraphCanvasState([node], []))

    // Mid-drag: 60 position changes with dragging: true
    for (let step = 1; step <= 60; step++) {
      const x = step * (100 / 60)
      const y = step * (100 / 60)
      act(() => {
        result.current.onNodesChange([
          { type: "position", id: "A", dragging: true, position: { x, y } },
        ])
      })
    }

    // Drag end: dragging: false at final position
    act(() => {
      result.current.onNodesChange([
        { type: "position", id: "A", dragging: false, position: { x: 100, y: 100 } },
      ])
    })

    // Exactly one snapshot was pushed for the entire drag
    expect(countSnapshots(result)).toBe(1)
  })

  // ────────────────────────────────────────────────────────────────────
  // 2. Click-select is not a snapshot.
  //
  // React Flow emits `{type: "select"}` NodeChange when the user clicks a
  // node.  This is pure UI state (no structural change) and must not push
  // an undo entry — otherwise Ctrl+Z would undo a click before any real
  // edit.
  // ────────────────────────────────────────────────────────────────────
  it("a single select NodeChange does not push a snapshot", () => {
    const { result } = renderHook(() => useGraphCanvasState([makeNode("A")], []))

    act(() => {
      result.current.onNodesChange([
        { type: "select", id: "A", selected: true } as NodeChange,
      ])
    })

    expect(result.current.canUndo).toBe(false)
  })

  // ────────────────────────────────────────────────────────────────────
  // 3. Edge add is a snapshot.
  //
  // Connecting two nodes via the edge handles emits an `{type: "add"}`
  // EdgeChange.  This IS a structural change and MUST push a snapshot so
  // the user can undo the connection.
  // ────────────────────────────────────────────────────────────────────
  it("a single edge add change pushes exactly one snapshot", () => {
    const { result } = renderHook(() =>
      useGraphCanvasState([makeNode("A"), makeNode("B")], []),
    )

    act(() => {
      result.current.onEdgesChange([
        { type: "add", item: makeEdge("A", "B", { id: "e1" }) } as EdgeChange,
      ])
    })

    expect(countSnapshots(result)).toBe(1)
  })

  // ────────────────────────────────────────────────────────────────────
  // 4. Multi-change batch → one snapshot.
  //
  // Adding 3 edges in a single onEdgesChange call (as happens when
  // programmatically restoring a saved state, or during a multi-select
  // paste) must push exactly one snapshot for the batch — not three.
  // This pins the existing `changes.some(hasStructural)` semantics.
  // ────────────────────────────────────────────────────────────────────
  it("three edge adds in a single batch push exactly one snapshot", () => {
    const { result } = renderHook(() =>
      useGraphCanvasState(
        [makeNode("A"), makeNode("B"), makeNode("C"), makeNode("D")],
        [],
      ),
    )

    act(() => {
      result.current.onEdgesChange([
        { type: "add", item: makeEdge("A", "B", { id: "e1" }) } as EdgeChange,
        { type: "add", item: makeEdge("B", "C", { id: "e2" }) } as EdgeChange,
        { type: "add", item: makeEdge("C", "D", { id: "e3" }) } as EdgeChange,
      ])
    })

    expect(countSnapshots(result)).toBe(1)
  })

  // ────────────────────────────────────────────────────────────────────
  // 5. Undo after drag restores the pre-drag position.
  //
  // The whole point of batching drag events is so Ctrl+Z returns to the
  // position the node had BEFORE the drag started.  If the snapshot is
  // mistakenly taken from a mid-drag frame, undo would land at (50, 50),
  // not (0, 0).  This is the behavioural contract users depend on.
  //
  // Note: useNodesState applies position changes in place; the snapshot
  // must shallow-clone nodes so the pre-drag (0, 0) position survives.
  // ────────────────────────────────────────────────────────────────────
  it("undo after a drag restores the pre-drag position", () => {
    const node = makeNode("A", "polars", { position: { x: 0, y: 0 } })
    const { result } = renderHook(() => useGraphCanvasState([node], []))

    // Drag from (0,0) → (100,100)
    act(() => {
      result.current.onNodesChange([
        { type: "position", id: "A", dragging: true, position: { x: 50, y: 50 } },
      ])
    })
    act(() => {
      result.current.onNodesChange([
        { type: "position", id: "A", dragging: true, position: { x: 100, y: 100 } },
      ])
    })
    act(() => {
      result.current.onNodesChange([
        { type: "position", id: "A", dragging: false, position: { x: 100, y: 100 } },
      ])
    })

    // Sanity: the drag actually moved the node
    expect(result.current.nodes[0].position).toEqual({ x: 100, y: 100 })

    // Ctrl+Z
    act(() => {
      result.current.undo()
    })

    // Back at pre-drag position
    expect(result.current.nodes[0].position).toEqual({ x: 0, y: 0 })
    // And no further undo is available — a single drag was a single entry
    expect(result.current.canUndo).toBe(false)
    expect(result.current.canRedo).toBe(true)
  })

  // ────────────────────────────────────────────────────────────────────
  // 6. WebSocket-sync: position change without `dragging` is NOT a snapshot.
  //
  // Phase 1H item #8 regression.  When a file-watcher WebSocket update
  // arrives, useWebSocketSync calls `setNodesRaw` directly (bypassing
  // history).  But React Flow can then emit follow-up position changes
  // with NO `dragging` flag as it re-measures node DOM sizes.  These must
  // not push snapshots — otherwise every file save would inject a bogus
  // undo entry and Ctrl+Z would rewind an untouched graph.
  //
  // The refactor must preserve this: the `dragging` flag is the only
  // signal that a position change is user-originated.  Absent it, no
  // snapshot.
  // ────────────────────────────────────────────────────────────────────
  it("a position change without dragging flag (e.g. from WS sync) pushes no snapshot", () => {
    const { result } = renderHook(() =>
      useGraphCanvasState([makeNode("A", "polars", { position: { x: 0, y: 0 } })], []),
    )

    // React Flow emitting a layout/measure position change — no dragging key
    act(() => {
      result.current.onNodesChange([
        { type: "position", id: "A", position: { x: 50, y: 50 } },
      ])
    })

    expect(result.current.canUndo).toBe(false)
  })

  // ────────────────────────────────────────────────────────────────────
  // 7. Drag followed by a structural change → two snapshots, in order.
  //
  // Regression guard: after the simplification, the hook must still
  // distinguish consecutive independent actions.  Drag the node, then
  // add an edge — undo should step back through the edge add first,
  // then the drag, landing at the original state in two Ctrl+Z presses.
  // ────────────────────────────────────────────────────────────────────
  it("drag then edge-add produces two distinct snapshots", () => {
    const nodeA = makeNode("A", "polars", { position: { x: 0, y: 0 } })
    const nodeB = makeNode("B", "polars", { position: { x: 200, y: 0 } })
    const { result } = renderHook(() => useGraphCanvasState([nodeA, nodeB], []))

    // Drag A to (100, 100)
    act(() => {
      result.current.onNodesChange([
        { type: "position", id: "A", dragging: true, position: { x: 50, y: 50 } },
      ])
    })
    act(() => {
      result.current.onNodesChange([
        { type: "position", id: "A", dragging: false, position: { x: 100, y: 100 } },
      ])
    })

    // Connect A → B
    act(() => {
      result.current.onEdgesChange([
        { type: "add", item: makeEdge("A", "B", { id: "e1" }) } as EdgeChange,
      ])
    })

    expect(countSnapshots(result)).toBe(2)
  })

  // ────────────────────────────────────────────────────────────────────
  // 8. Drag events arriving across two distinct React effects still
  //    yield exactly one snapshot — the drag-start detection must happen
  //    on the FIRST change with `dragging: true`, and the subsequent
  //    `dragging: true` events must not re-snapshot.  This is the exact
  //    behaviour the `isDragging` ref currently encodes and that the
  //    refactor preserves by checking the incoming batch only.
  // ────────────────────────────────────────────────────────────────────
  it("a drag split across two onNodesChange calls still yields one snapshot", () => {
    const node = makeNode("A", "polars", { position: { x: 0, y: 0 } })
    const { result } = renderHook(() => useGraphCanvasState([node], []))

    // First effect: dragging: true
    act(() => {
      result.current.onNodesChange([
        { type: "position", id: "A", dragging: true, position: { x: 10, y: 10 } },
      ])
    })

    // Second effect: dragging: true again (mid-drag)
    act(() => {
      result.current.onNodesChange([
        { type: "position", id: "A", dragging: true, position: { x: 20, y: 20 } },
      ])
    })

    // Final effect: dragging: false
    act(() => {
      result.current.onNodesChange([
        { type: "position", id: "A", dragging: false, position: { x: 30, y: 30 } },
      ])
    })

    expect(countSnapshots(result)).toBe(1)
  })
})
