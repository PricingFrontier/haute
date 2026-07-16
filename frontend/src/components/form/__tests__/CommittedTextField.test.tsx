import { describe, it, expect, vi, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup } from "@testing-library/react"
import CommittedTextField, { CommittedTextArea } from "../CommittedTextField"

afterEach(cleanup)

/** Fire one change event per character, exactly as a real keystroke-by-keystroke
 *  edit does — each with the cumulative value. */
function typeChars(input: HTMLElement, text: string) {
  for (let i = 1; i <= text.length; i++) {
    fireEvent.change(input, { target: { value: text.slice(0, i) } })
  }
}

describe("CommittedTextField — one commit per field edit (undo-atomicity)", () => {
  it("does not commit while typing; commits once on blur", () => {
    const onCommit = vi.fn()
    render(<CommittedTextField value="" onCommit={onCommit} data-testid="f" />)
    const input = screen.getByTestId("f") as HTMLInputElement

    typeChars(input, "sales")
    // The input reflects the draft live while typing...
    expect(input.value).toBe("sales")
    // ...but NOTHING is committed per keystroke — this is the fix: a 5-char
    // edit must not push 5 undo snapshots downstream.
    expect(onCommit).not.toHaveBeenCalled()

    fireEvent.blur(input)
    expect(onCommit).toHaveBeenCalledTimes(1)
    expect(onCommit).toHaveBeenCalledWith("sales")
  })

  it("commits once on Enter", () => {
    const onCommit = vi.fn()
    render(<CommittedTextField value="a" onCommit={onCommit} data-testid="f" />)
    const input = screen.getByTestId("f") as HTMLInputElement

    typeChars(input, "abc")
    expect(onCommit).not.toHaveBeenCalled()

    fireEvent.keyDown(input, { key: "Enter" })
    expect(onCommit).toHaveBeenCalledTimes(1)
    expect(onCommit).toHaveBeenCalledWith("abc")
  })

  it("skips a no-op commit (unchanged value never churns state / undo)", () => {
    const onCommit = vi.fn()
    render(<CommittedTextField value="keep" onCommit={onCommit} data-testid="f" />)
    const input = screen.getByTestId("f") as HTMLInputElement

    // Blur with no edit at all.
    fireEvent.blur(input)
    expect(onCommit).not.toHaveBeenCalled()

    // Edit then revert to the original before blur → still a no-op.
    fireEvent.change(input, { target: { value: "keepX" } })
    fireEvent.change(input, { target: { value: "keep" } })
    fireEvent.blur(input)
    expect(onCommit).not.toHaveBeenCalled()
  })

  it("drops a stale draft when the committed value changes underneath (undo mid-edit)", () => {
    const onCommit = vi.fn()
    const { rerender } = render(
      <CommittedTextField value="one" onCommit={onCommit} data-testid="f" />,
    )
    const input = screen.getByTestId("f") as HTMLInputElement

    // Start editing but don't commit.
    fireEvent.change(input, { target: { value: "one-DRAFT" } })
    expect(input.value).toBe("one-DRAFT")

    // The committed value changes out from under the open edit (e.g. an undo
    // restored a different value, or the panel re-targeted another node).
    rerender(<CommittedTextField value="two" onCommit={onCommit} data-testid="f" />)
    expect(input.value).toBe("two")

    // A blur now must NOT resurrect or commit the dead draft.
    fireEvent.blur(input)
    expect(onCommit).not.toHaveBeenCalled()
  })

  it("composes a caller-supplied onBlur / onKeyDown", () => {
    const onCommit = vi.fn()
    const onBlur = vi.fn()
    const onKeyDown = vi.fn()
    render(
      <CommittedTextField
        value=""
        onCommit={onCommit}
        onBlur={onBlur}
        onKeyDown={onKeyDown}
        data-testid="f"
      />,
    )
    const input = screen.getByTestId("f") as HTMLInputElement

    fireEvent.change(input, { target: { value: "x" } })
    fireEvent.keyDown(input, { key: "Enter" })
    expect(onKeyDown).toHaveBeenCalledTimes(1)
    expect(onCommit).toHaveBeenCalledWith("x")

    fireEvent.blur(input)
    expect(onBlur).toHaveBeenCalledTimes(1)
  })
})

describe("CommittedTextArea — commit on blur only (Enter is a newline)", () => {
  it("does not commit while typing; commits once on blur", () => {
    const onCommit = vi.fn()
    render(<CommittedTextArea value="" onCommit={onCommit} data-testid="t" />)
    const area = screen.getByTestId("t") as HTMLTextAreaElement

    typeChars(area, "select 1")
    expect(area.value).toBe("select 1")
    expect(onCommit).not.toHaveBeenCalled()

    fireEvent.blur(area)
    expect(onCommit).toHaveBeenCalledTimes(1)
    expect(onCommit).toHaveBeenCalledWith("select 1")
  })

  it("Enter does NOT commit — it is a newline in a textarea", () => {
    const onCommit = vi.fn()
    render(<CommittedTextArea value="a" onCommit={onCommit} data-testid="t" />)
    const area = screen.getByTestId("t") as HTMLTextAreaElement

    fireEvent.change(area, { target: { value: "a\nb" } })
    fireEvent.keyDown(area, { key: "Enter" })
    expect(onCommit).not.toHaveBeenCalled()

    fireEvent.blur(area)
    expect(onCommit).toHaveBeenCalledTimes(1)
    expect(onCommit).toHaveBeenCalledWith("a\nb")
  })
})
