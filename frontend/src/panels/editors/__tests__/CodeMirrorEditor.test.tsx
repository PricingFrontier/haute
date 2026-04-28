import { EditorView } from "@codemirror/view"
import { act, cleanup, render } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import CodeMirrorEditor from "../CodeMirrorEditor"

describe("CodeMirrorEditor", () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    cleanup()
    vi.useRealTimers()
  })

  it("debounces local document changes before notifying callers", async () => {
    const onChange = vi.fn()
    let editorView: EditorView | null = null

    render(
      <CodeMirrorEditor
        defaultValue=""
        onChange={onChange}
        onEditorView={(view) => {
          editorView = view
        }}
      />,
    )

    expect(editorView).toBeInstanceOf(EditorView)

    await act(async () => {
      editorView!.dispatch({ changes: { from: 0, insert: "a" } })
      editorView!.dispatch({ changes: { from: 1, insert: "b" } })
    })

    expect(onChange).not.toHaveBeenCalled()

    await act(async () => {
      await vi.advanceTimersByTimeAsync(149)
    })
    expect(onChange).not.toHaveBeenCalled()

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1)
    })
    expect(onChange).toHaveBeenCalledTimes(1)
    expect(onChange).toHaveBeenCalledWith("ab")
  })

  it("does not emit onChange for external value syncs", async () => {
    const onChange = vi.fn()
    let editorView: EditorView | null = null

    const { rerender } = render(
      <CodeMirrorEditor
        defaultValue="old"
        onChange={onChange}
        onEditorView={(view) => {
          editorView = view
        }}
      />,
    )

    expect(editorView).toBeInstanceOf(EditorView)

    rerender(
      <CodeMirrorEditor
        defaultValue="new"
        onChange={onChange}
        onEditorView={(view) => {
          editorView = view
        }}
      />,
    )

    await act(async () => {
      await vi.advanceTimersByTimeAsync(200)
    })

    expect(editorView!.state.doc.toString()).toBe("new")
    expect(onChange).not.toHaveBeenCalled()
  })
})
