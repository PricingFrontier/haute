/**
 * Facet test for the `previewColumnWidths` slice of useUIStore — per-node,
 * per-column DataPreview width overrides (px).
 *
 * Contract (design `datapreview-column-resize` §3.3 / §5.4-3):
 *   - view state only, in-memory, NEVER serialised into pipeline config;
 *   - keyed nodeId -> column name -> px;
 *   - set/clear/overwrite semantics with multi-node independence.
 */
import { describe, it, expect, beforeEach } from "vitest"
import useUIStore from "../useUIStore"

describe("useUIStore.previewColumnWidths", () => {
  beforeEach(() => {
    useUIStore.setState({ previewColumnWidths: {} })
  })

  it("defaults to an empty lookup", () => {
    expect(useUIStore.getState().previewColumnWidths).toEqual({})
  })

  it("setPreviewColumnWidth stores a width by node id and column name", () => {
    useUIStore.getState().setPreviewColumnWidth("n1", "premium", 320)
    expect(useUIStore.getState().previewColumnWidths).toEqual({ n1: { premium: 320 } })
  })

  it("overwrites an existing column entry without touching siblings", () => {
    useUIStore.getState().setPreviewColumnWidth("n1", "premium", 320)
    useUIStore.getState().setPreviewColumnWidth("n1", "age", 90)
    useUIStore.getState().setPreviewColumnWidth("n1", "premium", 480)
    expect(useUIStore.getState().previewColumnWidths).toEqual({
      n1: { premium: 480, age: 90 },
    })
  })

  it("keeps nodes independent (same column name on two nodes)", () => {
    useUIStore.getState().setPreviewColumnWidth("n1", "premium", 320)
    useUIStore.getState().setPreviewColumnWidth("n2", "premium", 600)
    expect(useUIStore.getState().previewColumnWidths).toEqual({
      n1: { premium: 320 },
      n2: { premium: 600 },
    })
  })

  it("clearPreviewColumnWidth removes only the named column", () => {
    useUIStore.getState().setPreviewColumnWidth("n1", "premium", 320)
    useUIStore.getState().setPreviewColumnWidth("n1", "age", 90)
    useUIStore.getState().clearPreviewColumnWidth("n1", "premium")
    expect(useUIStore.getState().previewColumnWidths).toEqual({ n1: { age: 90 } })
  })

  it("clearing the last column leaves an empty node map (harmless residue)", () => {
    useUIStore.getState().setPreviewColumnWidth("n1", "premium", 320)
    useUIStore.getState().clearPreviewColumnWidth("n1", "premium")
    expect(useUIStore.getState().previewColumnWidths).toEqual({ n1: {} })
  })

  it("clearing an unknown node or column is a no-op", () => {
    useUIStore.getState().setPreviewColumnWidth("n1", "premium", 320)
    useUIStore.getState().clearPreviewColumnWidth("ghost", "premium")
    useUIStore.getState().clearPreviewColumnWidth("n1", "ghost")
    expect(useUIStore.getState().previewColumnWidths).toEqual({ n1: { premium: 320 } })
  })

  it("does not mutate previous state objects (immutable update)", () => {
    useUIStore.getState().setPreviewColumnWidth("n1", "premium", 320)
    const before = useUIStore.getState().previewColumnWidths
    useUIStore.getState().setPreviewColumnWidth("n1", "premium", 480)
    expect(before).toEqual({ n1: { premium: 320 } })
    expect(useUIStore.getState().previewColumnWidths).not.toBe(before)
  })
})
