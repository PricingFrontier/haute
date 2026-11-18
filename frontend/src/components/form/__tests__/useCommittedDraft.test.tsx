import { act, cleanup, fireEvent, render, renderHook, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import CommittedTextField from "../CommittedTextField"
import { commitOpenDrafts, isDraftOpen, useCommittedDraft, type CommitResult } from "../useCommittedDraft"

afterEach(cleanup)

describe("useCommittedDraft", () => {
  it("lets go of a draft when its owner changes, though the value is the same", () => {
    const onCommit = vi.fn()
    const hook = renderHook(({ owner }) => useCommittedDraft<string>("north", onCommit, { owner }), { initialProps: { owner: 1 } })
    act(() => hook.result.current.edit("south"))
    expect(hook.result.current.shown).toBe("south")
    hook.rerender({ owner: 2 })
    expect(hook.result.current).toMatchObject({ shown: "north", editing: false })
  })

  it("commits a value given in an event, once", () => {
    const onCommit = vi.fn()
    const hook = renderHook(() => useCommittedDraft<string>("a", onCommit))
    act(() => {
      expect(hook.result.current.commit("b")).toBe(true)
    })
    expect(onCommit).toHaveBeenCalledExactlyOnceWith("b")
    expect(hook.result.current.editing).toBe(false)
  })

  it("keeps a draft its own commit says stays, and lets it go on any other change", () => {
    // A list holding a row the stored value cannot: the commit names what the store brings back.
    const onCommit = vi.fn((): CommitResult<string> => ({ ok: true, stays: "stored-after" }))
    const hook = renderHook(({ value }) => useCommittedDraft(value, onCommit), { initialProps: { value: "stored" } })
    act(() => {
      hook.result.current.commit("typed, with a row the store cannot hold")
    })
    hook.rerender({ value: "stored-after" })
    expect(hook.result.current).toMatchObject({ shown: "typed, with a row the store cannot hold", editing: true })
    hook.rerender({ value: "undone" })
    expect(hook.result.current).toMatchObject({ shown: "undone", editing: false })
  })

  it("keeps a refused commit's draft open", () => {
    const hook = renderHook(() => useCommittedDraft<string>("1", (): CommitResult<string> => ({ ok: false })))
    act(() => hook.result.current.edit("x"))
    act(() => {
      expect(hook.result.current.commit()).toBe(false)
    })
    expect(hook.result.current).toMatchObject({ shown: "x", editing: true })
  })
})

describe("the open drafts a save commits", () => {
  it("commits a field being typed in that was never left, once, and then holds none", () => {
    const onCommit = vi.fn()
    render(<CommittedTextField aria-label="Suffix" value="_left" onCommit={onCommit} />)
    expect(isDraftOpen()).toBe(false)
    fireEvent.change(screen.getByLabelText("Suffix"), { target: { value: "_right" } })
    expect(isDraftOpen()).toBe(true)

    act(() => commitOpenDrafts())
    act(() => commitOpenDrafts())

    expect(onCommit).toHaveBeenCalledExactlyOnceWith("_right")
    expect(isDraftOpen()).toBe(false)
  })

  it("keeps a refused draft open, on screen", () => {
    render(<CommittedTextField aria-label="Rate" value="1" onCommit={() => ({ ok: false })} />)
    fireEvent.change(screen.getByLabelText("Rate"), { target: { value: "abc" } })
    act(() => commitOpenDrafts())
    expect(screen.getByLabelText("Rate")).toHaveValue("abc")
    expect(isDraftOpen()).toBe(true)
  })

  it("commits once when an Enter handler leaves the field before the Enter commits", () => {
    const onCommit = vi.fn()
    render(
      <CommittedTextField
        aria-label="Sheet name"
        value="Sheet 1"
        onCommit={onCommit}
        onKeyDown={(event) => {
          if (event.key === "Enter") event.currentTarget.blur()
        }}
      />,
    )
    const field = screen.getByLabelText("Sheet name")
    field.focus()
    fireEvent.change(field, { target: { value: "Quotes" } })
    fireEvent.keyDown(field, { key: "Enter" })
    expect(onCommit).toHaveBeenCalledExactlyOnceWith("Quotes")
  })
})
