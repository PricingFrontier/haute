/**
 * Tests for useConstraintHandlers hook.
 *
 * Tests constraint add, remove, column rename, and value change operations.
 */
import { describe, it, expect, vi } from "vitest"
import { renderHook, act } from "@testing-library/react"
import { useConstraintHandlers } from "../../hooks/useConstraintHandlers"

const COLUMNS = [
  { name: "volume", dtype: "float64" },
  { name: "loss_ratio", dtype: "float64" },
  { name: "premium", dtype: "float64" },
]

describe("useConstraintHandlers", () => {
  // ─── handleAddConstraint ────────────────────────────────────────

  describe("handleAddConstraint", () => {
    it("adds first available column not already constrained", () => {
      const onUpdate = vi.fn()
      const { result } = renderHook(() =>
        useConstraintHandlers({}, "premium", COLUMNS, onUpdate),
      )

      act(() => result.current.handleAddConstraint())

      expect(onUpdate).toHaveBeenCalledWith("constraints", {
        volume: { min: 0 },
      })
    })

    it("skips the objective column", () => {
      const onUpdate = vi.fn()
      const { result } = renderHook(() =>
        useConstraintHandlers({}, "volume", COLUMNS, onUpdate),
      )

      act(() => result.current.handleAddConstraint())

      // Should skip "volume" (objective) and pick "loss_ratio"
      expect(onUpdate).toHaveBeenCalledWith("constraints", {
        loss_ratio: { min: 0 },
      })
    })

    it("skips already-constrained columns", () => {
      const onUpdate = vi.fn()
      const existing = { volume: { min: 0 } }
      const { result } = renderHook(() =>
        useConstraintHandlers(existing, "premium", COLUMNS, onUpdate),
      )

      act(() => result.current.handleAddConstraint())

      expect(onUpdate).toHaveBeenCalledWith("constraints", {
        volume: { min: 0 },
        loss_ratio: { min: 0 },
      })
    })

    it("generates synthetic name when all columns are used", () => {
      const onUpdate = vi.fn()
      const existing = {
        volume: { min: 0 },
        loss_ratio: { max: 1.1 },
      }
      const { result } = renderHook(() =>
        useConstraintHandlers(existing, "premium", COLUMNS, onUpdate),
      )

      act(() => result.current.handleAddConstraint())

      const call = onUpdate.mock.calls[0]
      expect(call[0]).toBe("constraints")
      expect(call[1]).toHaveProperty("constraint_3")
    })
  })

  // ─── handleRemoveConstraint ─────────────────────────────────────

  describe("handleRemoveConstraint", () => {
    it("removes the specified constraint", () => {
      const onUpdate = vi.fn()
      const existing = {
        volume: { min: 0 },
        loss_ratio: { max: 1.1 },
      }
      const { result } = renderHook(() =>
        useConstraintHandlers(existing, "premium", COLUMNS, onUpdate),
      )

      act(() => result.current.handleRemoveConstraint("volume"))

      expect(onUpdate).toHaveBeenCalledWith("constraints", {
        loss_ratio: { max: 1.1 },
      })
    })

    it("produces empty constraints when removing last one", () => {
      const onUpdate = vi.fn()
      const existing = { volume: { min: 0 } }
      const { result } = renderHook(() =>
        useConstraintHandlers(existing, "premium", COLUMNS, onUpdate),
      )

      act(() => result.current.handleRemoveConstraint("volume"))

      expect(onUpdate).toHaveBeenCalledWith("constraints", {})
    })

    it("removes the matching frontier range in the same update", () => {
      const onUpdate = vi.fn()
      const existing = {
        volume: { min: 0 },
        loss_ratio: { max: 1.1 },
      }
      const ranges = {
        volume: { min: 10, max: 20 },
        loss_ratio: { min: 30, max: 40 },
      }
      const { result } = renderHook(() =>
        useConstraintHandlers(existing, "premium", COLUMNS, onUpdate, ranges),
      )

      act(() => result.current.handleRemoveConstraint("volume"))

      expect(onUpdate).toHaveBeenCalledWith({
        constraints: { loss_ratio: { max: 1.1 } },
        frontier_ranges: { loss_ratio: { min: 30, max: 40 } },
      })
    })
  })

  // ─── handleConstraintColumnChange ───────────────────────────────

  describe("handleConstraintColumnChange", () => {
    it("renames a constraint column", () => {
      const onUpdate = vi.fn()
      const existing = {
        volume: { min: 0 },
        loss_ratio: { max: 1.1 },
      }
      const { result } = renderHook(() =>
        useConstraintHandlers(existing, "premium", COLUMNS, onUpdate),
      )

      act(() => result.current.handleConstraintColumnChange("volume", "premium"))

      expect(onUpdate).toHaveBeenCalledWith("constraints", {
        premium: { min: 0 },
        loss_ratio: { max: 1.1 },
      })
    })

    it("does nothing when old and new names are the same", () => {
      const onUpdate = vi.fn()
      const existing = { volume: { min: 0 } }
      const { result } = renderHook(() =>
        useConstraintHandlers(existing, "premium", COLUMNS, onUpdate),
      )

      act(() => result.current.handleConstraintColumnChange("volume", "volume"))

      expect(onUpdate).not.toHaveBeenCalled()
    })

    it("migrates the matching frontier range in the same update", () => {
      const onUpdate = vi.fn()
      const existing = {
        volume: { min: 0 },
        loss_ratio: { max: 1.1 },
      }
      const ranges = {
        volume: { min: 10, max: 20 },
        loss_ratio: { min: 30, max: 40 },
      }
      const { result } = renderHook(() =>
        useConstraintHandlers(existing, "premium", COLUMNS, onUpdate, ranges),
      )

      act(() => result.current.handleConstraintColumnChange("volume", "premium"))

      expect(onUpdate).toHaveBeenCalledWith({
        constraints: {
          premium: { min: 0 },
          loss_ratio: { max: 1.1 },
        },
        frontier_ranges: {
          premium: { min: 10, max: 20 },
          loss_ratio: { min: 30, max: 40 },
        },
      })
    })

    it("preserves order of other constraints", () => {
      const onUpdate = vi.fn()
      const existing = {
        a: { min: 0.8 },
        b: { max: 1.2 },
        c: { min: 0.95 },
      }
      const { result } = renderHook(() =>
        useConstraintHandlers(existing, "premium", COLUMNS, onUpdate),
      )

      act(() => result.current.handleConstraintColumnChange("b", "renamed_b"))

      const newConstraints = onUpdate.mock.calls[0][1]
      expect(Object.keys(newConstraints)).toEqual(["a", "renamed_b", "c"])
    })
  })

  // ─── handleConstraintValueChange ────────────────────────────────

  describe("handleConstraintValueChange", () => {
    it("updates constraint type and value as the single active threshold", () => {
      const onUpdate = vi.fn()
      const existing = { volume: { min: 0.9 } }
      const { result } = renderHook(() =>
        useConstraintHandlers(existing, "premium", COLUMNS, onUpdate),
      )

      act(() => result.current.handleConstraintValueChange("volume", "max", 1.1))

      expect(onUpdate).toHaveBeenCalledWith("constraints", {
        volume: { max: 1.1 },
      })
    })

    it("switches constraint type without preserving stale keys", () => {
      const onUpdate = vi.fn()
      const existing = { volume: { min: 0.9 } }
      const { result } = renderHook(() =>
        useConstraintHandlers(existing, "premium", COLUMNS, onUpdate),
      )

      act(() => result.current.handleConstraintValueChange("volume", "max", 500000))

      expect(onUpdate).toHaveBeenCalledWith("constraints", {
        volume: { max: 500000 },
      })
    })

    it("replaces removed price-contour 0.2 absolute keys", () => {
      const onUpdate = vi.fn()
      const existing = { volume: { min_abs: 1000 } }
      const { result } = renderHook(() =>
        useConstraintHandlers(existing, "premium", COLUMNS, onUpdate),
      )

      act(() => result.current.handleConstraintValueChange("volume", "min", 1200))

      expect(onUpdate).toHaveBeenCalledWith("constraints", {
        volume: { min: 1200 },
      })
    })
  })

  describe("handleAddConstraint without a free column", () => {
    it("never reuses the placeholder name of a constraint already there", () => {
      const onUpdate = vi.fn()
      // constraint_1 was removed, leaving constraint_2 as the only one.
      const existing = { constraint_2: { max: 5 } }
      const { result } = renderHook(() => useConstraintHandlers(existing, "premium", [], onUpdate))

      act(() => result.current.handleAddConstraint())

      expect(onUpdate).toHaveBeenCalledWith("constraints", {
        constraint_2: { max: 5 },
        constraint_3: { min: 0 },
      })
    })
  })

  // ─── handleConstraintKindChange ─────────────────────────────────

  describe("handleConstraintKindChange", () => {
    it("keeps the value between at least and at most", () => {
      const onUpdate = vi.fn()
      const { result } = renderHook(() =>
        useConstraintHandlers({ volume: { min_pct: 0.95 } }, "premium", COLUMNS, onUpdate),
      )

      act(() => result.current.handleConstraintKindChange("volume", "max_pct"))

      expect(onUpdate).toHaveBeenCalledWith("constraints", { volume: { max_pct: 0.95 } })
    })

    it("starts a percentage at the baseline and an absolute bound at 0", () => {
      const onUpdate = vi.fn()
      const { result, rerender } = renderHook(
        ({ constraints }) => useConstraintHandlers(constraints, "premium", COLUMNS, onUpdate),
        { initialProps: { constraints: { volume: { min: 950000 } } as Record<string, Record<string, number>> } },
      )

      act(() => result.current.handleConstraintKindChange("volume", "min_pct"))
      expect(onUpdate).toHaveBeenLastCalledWith("constraints", { volume: { min_pct: 1 } })

      rerender({ constraints: { volume: { max_pct: 0.95 } } })
      act(() => result.current.handleConstraintKindChange("volume", "max"))
      expect(onUpdate).toHaveBeenLastCalledWith("constraints", { volume: { max: 0 } })
    })

    it("restarts a swept constraint's range when the unit changes, in one update", () => {
      const onUpdate = vi.fn()
      const ranges = { volume: { min: 900000, max: 1100000 }, loss_ratio: { min: 0.6, max: 0.7 } }
      const { result } = renderHook(() =>
        useConstraintHandlers(
          { volume: { min: 1000000 }, loss_ratio: { max: 0.65 } },
          "premium",
          COLUMNS,
          onUpdate,
          ranges,
        ),
      )

      act(() => result.current.handleConstraintKindChange("volume", "min_pct"))

      expect(onUpdate).toHaveBeenCalledWith({
        constraints: { volume: { min_pct: 1 }, loss_ratio: { max: 0.65 } },
        frontier_ranges: { volume: { min: 1 }, loss_ratio: { min: 0.6, max: 0.7 } },
      })
    })

    it("gives a constraint without a bound key the chosen kind's starting value", () => {
      const onUpdate = vi.fn()
      const { result } = renderHook(() =>
        useConstraintHandlers({ volume: { min_abs: 1000 } }, "premium", COLUMNS, onUpdate),
      )

      act(() => result.current.handleConstraintKindChange("volume", "max"))

      expect(onUpdate).toHaveBeenCalledWith("constraints", { volume: { max: 0 } })
    })
  })
})
