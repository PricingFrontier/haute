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

  it("marks a repeat error on the same line after an edit cleared the mark", async () => {
    let editorView: EditorView | null = null
    const editor = (errorRevision: object) => (
      <CodeMirrorEditor
        defaultValue={"x = 1\ny = oops"}
        onChange={vi.fn()}
        errorLine={2}
        errorRevision={errorRevision}
        onEditorView={(view) => {
          editorView = view
        }}
      />
    )
    const marked = () => editorView!.dom.querySelectorAll(".cm-lintRange-error").length
    const view = render(editor({ run: 1 }))
    expect(marked()).toBe(1)

    // An edit clears the mark: the user is fixing the code.
    await act(async () => {
      editorView!.dispatch({ changes: { from: 0, insert: "#" } })
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(marked()).toBe(0)

    // The next run fails on the same line: its result marks it again.
    view.rerender(editor({ run: 2 }))
    expect(marked()).toBe(1)
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

  it("reports each change at once with reportEachChange", async () => {
    const onChange = vi.fn()
    let editorView: EditorView | null = null

    render(
      <CodeMirrorEditor
        defaultValue=""
        onChange={onChange}
        reportEachChange
        onEditorView={(view) => {
          editorView = view
        }}
      />,
    )

    await act(async () => {
      editorView!.dispatch({ changes: { from: 0, insert: "a" } })
      editorView!.dispatch({ changes: { from: 1, insert: "b" } })
    })

    expect(onChange.mock.calls).toEqual([["a"], ["ab"]])
  })

  it("flushes a pending local document change on blur", async () => {
    const onChange = vi.fn()
    let editorView: EditorView | null = null

    render(
      <CodeMirrorEditor
        defaultValue="df = join_premiums"
        onChange={onChange}
        onEditorView={(view) => {
          editorView = view
        }}
      />,
    )

    expect(editorView).toBeInstanceOf(EditorView)

    await act(async () => {
      editorView!.dispatch({
        changes: {
          from: 0,
          to: editorView!.state.doc.length,
          insert: "df = sale_flag",
        },
      })
    })

    expect(onChange).not.toHaveBeenCalled()

    await act(async () => {
      editorView!.contentDOM.dispatchEvent(new Event("blur"))
    })

    expect(onChange).toHaveBeenCalledTimes(1)
    expect(onChange).toHaveBeenCalledWith("df = sale_flag")

    await act(async () => {
      await vi.advanceTimersByTimeAsync(150)
    })
    expect(onChange).toHaveBeenCalledTimes(1)
  })

  it("commits a pending local document change when the editor unmounts", async () => {
    const onChange = vi.fn()
    let editorView: EditorView | null = null

    const { unmount } = render(
      <CodeMirrorEditor
        defaultValue=""
        onChange={onChange}
        onEditorView={(view) => {
          editorView = view
        }}
      />,
    )

    await act(async () => {
      editorView!.dispatch({ changes: { from: 0, insert: "df = x" } })
    })
    expect(onChange).not.toHaveBeenCalled()

    unmount()
    expect(onChange).toHaveBeenCalledTimes(1)
    expect(onChange).toHaveBeenCalledWith("df = x")
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
