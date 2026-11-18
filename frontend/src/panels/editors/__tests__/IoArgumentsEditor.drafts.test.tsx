import { act, cleanup, fireEvent, render, screen } from "@testing-library/react"
import { useState } from "react"
import { afterEach, describe, expect, it } from "vitest"

import { commitOpenDrafts } from "../../../components/form/useCommittedDraft"
import { IoArgumentsEditor } from "../_IoFormatEditor"

afterEach(cleanup)

/** The arguments as the node holds them, each commit recorded. `epoch` re-renders the editor. */
function Stored({ initial, written }: { initial: Record<string, unknown>; written: Record<string, unknown>[]; epoch?: number }) {
  const [args, setArgs] = useState(initial)
  return (
    <IoArgumentsEditor
      value={args}
      argumentNames={["separator"]}
      context="CSV"
      onCommit={(next) => {
        written.push(next)
        setArgs(next)
      }}
    />
  )
}

describe("IoArgumentsEditor drafts", () => {
  it("lets a save commit a field's typing without replaying the list's older draft over it", () => {
    const written: Record<string, unknown>[] = []
    const { rerender } = render(<Stored initial={{}} written={written} epoch={1} />)
    fireEvent.click(screen.getByRole("button", { name: "Add argument" }))
    fireEvent.change(screen.getByLabelText("Argument 1 name"), { target: { value: "separator" } })
    fireEvent.blur(screen.getByLabelText("Argument 1 name"))
    // A row with a name and no value yet: the list keeps it as its draft.
    fireEvent.change(screen.getByLabelText("Argument 1 value"), { target: { value: '"|"' } })
    // The list renders again after its field began a draft, as a parent's render does.
    rerender(<Stored initial={{}} written={written} epoch={2} />)

    act(() => commitOpenDrafts())

    expect(written.at(-1)).toEqual({ separator: "|" })
    expect(screen.getByLabelText("Argument 1 value")).toHaveValue('"|"')
  })

  it("keeps what is typed when the node hands down equal arguments again", () => {
    const view = (args: Record<string, unknown>) => (
      <IoArgumentsEditor value={args} argumentNames={["separator"]} context="CSV" onCommit={() => {}} />
    )
    const { rerender } = render(view({ separator: "," }))
    const field = screen.getByLabelText("Argument 1 value")
    field.focus()
    fireEvent.change(field, { target: { value: '"|"' } })

    rerender(view({ separator: "," }))

    expect(screen.getByLabelText("Argument 1 value")).toBe(field)
    expect(field).toHaveValue('"|"')
  })
})
