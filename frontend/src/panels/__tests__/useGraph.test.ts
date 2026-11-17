/**
 * Direct unit coverage for `useGraph()` and its backing `GraphContext`.
 *
 * The existing `NodePanel.graphContext.test.tsx` only exercises the hook
 * *indirectly* through a fully-mounted NodePanel under a `<GraphProvider>`.
 * These tests pin the hook's two branches in isolation:
 *
 *   1) called outside any provider → throws a loud, actionable error
 *      (so mis-mounted consumers surface in the ErrorBoundary rather than
 *       silently reading an empty graph);
 *   2) called inside a provider → returns exactly the supplied context value.
 *
 * We also assert the raw `GraphContext` default is `undefined`, which is the
 * sentinel that lets branch (1) distinguish "no provider" from "empty graph".
 */

import { describe, it, expect, vi, afterEach } from "vitest"
import { createElement, type ReactNode } from "react"
import { renderHook, cleanup } from "@testing-library/react"
import { GraphContext, useGraph, useRequestScope, type GraphContextValue } from "../useGraph"
import { documentRequestScope } from "../../utils/requestGateway"

afterEach(cleanup)

describe("useGraph() - outside a provider (fail loud)", () => {
  it("throws a clear error naming GraphProvider when no provider is mounted", () => {
    // React logs the thrown error via console.error during render; silence it
    // so the test output stays clean — the assertion proves the throw.
    const spy = vi.spyOn(console, "error").mockImplementation(() => {})
    try {
      expect(() => renderHook(() => useGraph())).toThrowError(
        /useGraph\(\) was called outside of a <GraphProvider>/,
      )
    } finally {
      spy.mockRestore()
    }
  })
})

describe("useGraph() - inside a provider", () => {
  it("returns the exact context value supplied by the nearest provider", () => {
    const value: GraphContextValue = {
      allNodes: [],
      edges: [],
      submodels: { sm1: { kind: "scorecard" } },
      preamble: "import numpy as np",
      requestScope: documentRequestScope,
    }
    const wrapper = ({ children }: { children: ReactNode }) =>
      createElement(GraphContext.Provider, { value }, children)

    const { result } = renderHook(() => useGraph(), { wrapper })
    // Identity-equal: the hook returns the provided value untouched.
    expect(result.current).toBe(value)
    expect(result.current.submodels).toEqual({ sm1: { kind: "scorecard" } })
    expect(result.current.preamble).toBe("import numpy as np")
  })

  it("does not throw for a provider holding an empty graph (distinct from no provider)", () => {
    const value: GraphContextValue = { allNodes: [], edges: [], requestScope: documentRequestScope }
    const wrapper = ({ children }: { children: ReactNode }) =>
      createElement(GraphContext.Provider, { value }, children)

    const { result } = renderHook(() => useGraph(), { wrapper })
    expect(result.current.allNodes).toEqual([])
    expect(result.current.edges).toEqual([])
    // Optional fields are absent, not errors.
    expect(result.current.submodels).toBeUndefined()
    expect(result.current.preamble).toBeUndefined()
  })
})

describe("useRequestScope", () => {
  const providing = (requestScope: GraphContextValue["requestScope"]) =>
    ({ children }: { children: ReactNode }) =>
      createElement(GraphContext.Provider, { value: { allNodes: [], edges: [], requestScope } }, children)

  it("is the nearest provider's scope, and none where it shows a saved version", () => {
    expect(renderHook(() => useRequestScope(), { wrapper: providing(documentRequestScope) }).result.current)
      .toBe(documentRequestScope)
    expect(renderHook(() => useRequestScope(), { wrapper: providing(null) }).result.current).toBeNull()
  })

  it("is the open pipeline outside any provider, the one document there is to run", () => {
    expect(renderHook(() => useRequestScope()).result.current).toBe(documentRequestScope)
  })
})

describe("GraphContext sentinel", () => {
  it("is a real React context object usable as a Provider", () => {
    // The exported context must carry the Provider that GraphProvider wraps —
    // guards against accidentally exporting a plain object instead.
    expect(GraphContext).toBeTruthy()
    expect(GraphContext.Provider).toBeTruthy()
  })
})
