/**
 * Regression battery for the Undo-atomicity class instance:
 * "Inline sidebar text fields commit every keystroke as a separate undo step"
 * (BUGS.md §Reported → §Fixed; MAGINOT_LINE "Undo atomicity — one user gesture,
 * multiple undo steps").
 *
 * Sibling of `useGraphStore.undoAtomicity.test.ts` (the delete/paste battery on
 * branch `undo-atomicity`). Where that one pins the node+edge gesture chokepoint
 * `setNodesAndEdges`, this one pins the inline-field chokepoint `CommittedTextField`:
 * a field edit commits ONCE (on blur/Enter), so it is exactly one undo step.
 *
 * These assert at the PERSISTENT boundary (AGENTS.md §UI Test Assertions): they
 * drive a real CommittedTextField whose `onCommit` routes through the SAME
 * history-aware `useGraphStore.setNodes` path every editor uses, then inspect the
 * store's `undoStack` and node state — not the editor's outgoing call argument.
 */
import { describe, it, expect, beforeEach, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup, act } from "@testing-library/react"
import useGraphStore, { resetGraphStoreForTests } from "../useGraphStore"
import { makeNode } from "../../test-utils/factories"
import CommittedTextField from "../../components/form/CommittedTextField"

const ID = "n1"

/** Minimal faithful stand-in for a sidebar label/config field: its value comes
 *  from the store, and a commit routes through the SAME history-aware setNodes
 *  path (App.onUpdateNode → store.setNodes) that every real editor uses. */
function LabelField() {
  const label = useGraphStore(
    (s) => (s.nodes.find((n) => n.id === ID)?.data.label ?? "") as string,
  )
  return (
    <CommittedTextField
      data-testid="label"
      value={label}
      onCommit={(v) =>
        useGraphStore
          .getState()
          .setNodes((nds) =>
            nds.map((n) => (n.id === ID ? { ...n, data: { ...n.data, label: v } } : n)),
          )
      }
    />
  )
}

function typeChars(input: HTMLElement, text: string) {
  for (let i = 1; i <= text.length; i++) {
    fireEvent.change(input, { target: { value: text.slice(0, i) } })
  }
}

function currentLabel(): string {
  return (useGraphStore.getState().nodes.find((n) => n.id === ID)?.data.label ?? "") as string
}

describe("useGraphStore — one undo per inline field edit", () => {
  beforeEach(resetGraphStoreForTests)
  afterEach(cleanup)

  it("a whole field edit is ONE undo step; one undo restores the pre-edit value", () => {
    useGraphStore.setState({ nodes: [makeNode(ID, "polars", { data: { label: "orig" } })] })
    render(<LabelField />)
    const input = screen.getByTestId("label")

    const before = useGraphStore.getState().undoStack.length

    typeChars(input, "sales")
    // Nothing is committed while typing → no snapshots pushed, value untouched.
    expect(useGraphStore.getState().undoStack.length).toBe(before)
    expect(currentLabel()).toBe("orig")

    fireEvent.blur(input)
    // The 5-keystroke edit collapsed to EXACTLY ONE undo snapshot.
    expect(useGraphStore.getState().undoStack.length).toBe(before + 1)
    expect(currentLabel()).toBe("sales")

    act(() => {
      void useGraphStore.getState().undo()
    })
    // A single undo reverts the WHOLE edit, not one character.
    expect(currentLabel()).toBe("orig")
    expect(useGraphStore.getState().undoStack.length).toBe(before)
  })

  it("edit size is irrelevant: a 12-char edit is still one undo step", () => {
    useGraphStore.setState({ nodes: [makeNode(ID, "polars", { data: { label: "" } })] })
    render(<LabelField />)
    const input = screen.getByTestId("label")

    const before = useGraphStore.getState().undoStack.length
    typeChars(input, "north-region")
    fireEvent.blur(input)

    expect(useGraphStore.getState().undoStack.length).toBe(before + 1)
    expect(currentLabel()).toBe("north-region")
  })

  it("a no-op edit (blur with no change) pushes nothing", () => {
    useGraphStore.setState({ nodes: [makeNode(ID, "polars", { data: { label: "keep" } })] })
    render(<LabelField />)
    const input = screen.getByTestId("label")

    const before = useGraphStore.getState().undoStack.length
    fireEvent.blur(input)
    expect(useGraphStore.getState().undoStack.length).toBe(before)

    // Type then revert to the original before blur → still nothing pushed.
    typeChars(input, "keepX")
    fireEvent.change(input, { target: { value: "keep" } })
    fireEvent.blur(input)
    expect(useGraphStore.getState().undoStack.length).toBe(before)
    expect(currentLabel()).toBe("keep")
  })

  it("guard: the pre-fix per-keystroke wiring would push one snapshot PER character", () => {
    // Pins WHY the fix is load-bearing: setNodes snapshots on every call, so
    // committing per keystroke (the old onChange→onUpdate wiring) floods the
    // undo stack. CommittedTextField is what collapses N calls into 1.
    useGraphStore.setState({ nodes: [makeNode(ID, "polars", { data: { label: "" } })] })
    const before = useGraphStore.getState().undoStack.length
    act(() => {
      for (const ch of "sales") {
        const next = currentLabel() + ch
        useGraphStore
          .getState()
          .setNodes((nds) =>
            nds.map((n) => (n.id === ID ? { ...n, data: { ...n.data, label: next } } : n)),
          )
      }
    })
    expect(useGraphStore.getState().undoStack.length).toBe(before + 5)
  })
})
