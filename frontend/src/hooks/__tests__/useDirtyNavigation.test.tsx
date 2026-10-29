/**
 * The git surfaces' dirty-navigation guard (specs/frontend-git-ui): a clean project goes
 * ahead at once; a dirty one asks, and Save first goes ahead only once the save has
 * answered true.
 */
import { beforeEach, describe, expect, it, vi } from "vitest"
import { act, renderHook } from "@testing-library/react"

import useDirtyNavigation from "../useDirtyNavigation"
import useGraphStore from "../../stores/useGraphStore"
import useWorkbenchStore from "../../stores/useWorkbenchStore"

beforeEach(() => {
  useGraphStore.setState({ dirty: false })
  useWorkbenchStore.setState({ formDirty: false })
})

function guarded(onSave?: () => Promise<boolean>) {
  const proceed = vi.fn()
  const hook = renderHook(() => useDirtyNavigation(onSave))
  act(() => hook.result.current.guard(proceed))
  return { proceed, result: hook.result }
}

describe("useDirtyNavigation", () => {
  it("goes ahead at once, asking nothing, while the project is clean", () => {
    const { proceed, result } = guarded(vi.fn())
    expect(proceed).toHaveBeenCalledOnce()
    expect(result.current.confirm).toBeNull()
  })

  it.each([
    ["the canvas", () => useGraphStore.setState({ dirty: true })],
    ["the workbench's form", () => useWorkbenchStore.setState({ formDirty: true })],
  ])("asks while %s has unsaved edits, and Cancel drops the action", (_where, makeDirty) => {
    makeDirty()
    const { proceed, result } = guarded(vi.fn())
    expect(result.current.confirm).not.toBeNull()
    expect(proceed).not.toHaveBeenCalled()

    act(() => result.current.confirm!.onCancel())
    expect(result.current.confirm).toBeNull()
    expect(proceed).not.toHaveBeenCalled()
  })

  it("Discard goes ahead without saving", () => {
    useGraphStore.setState({ dirty: true })
    const onSave = vi.fn()
    const { proceed, result } = guarded(onSave)

    act(() => result.current.confirm!.onDiscard())
    expect(proceed).toHaveBeenCalledOnce()
    expect(onSave).not.toHaveBeenCalled()
    expect(result.current.confirm).toBeNull()
  })

  it("Save first goes ahead only once the save answers true, a refused or failed save keeping the question", async () => {
    useGraphStore.setState({ dirty: true })
    const onSave = vi.fn()
      .mockResolvedValueOnce(false)
      .mockRejectedValueOnce(new Error("disk full"))
      .mockResolvedValueOnce(true)
    const { proceed, result } = guarded(onSave)

    await act(() => result.current.confirm!.onSave()) // answers false
    expect(proceed).not.toHaveBeenCalled()
    expect(result.current.confirm).not.toBeNull()

    await act(() => result.current.confirm!.onSave()) // throws
    expect(proceed).not.toHaveBeenCalled()
    expect(result.current.confirm).not.toBeNull()

    await act(() => result.current.confirm!.onSave())
    expect(onSave).toHaveBeenCalledTimes(3)
    expect(proceed).toHaveBeenCalledOnce()
    expect(result.current.confirm).toBeNull()
  })

  it("keeps the question open when there is no save to run", async () => {
    useWorkbenchStore.setState({ formDirty: true })
    const { proceed, result } = guarded()

    await act(() => result.current.confirm!.onSave())
    expect(proceed).not.toHaveBeenCalled()
    expect(result.current.confirm).not.toBeNull()
  })
})
