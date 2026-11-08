import { afterEach, describe, expect, it, vi } from "vitest"
import { renderHook, waitFor } from "@testing-library/react"
import useToastStore from "../../../../stores/useToastStore"
import { isGridHeaderRow, useTwoColumnGridClipboard } from "../useTwoColumnGridClipboard"
import { writeClipboardText } from "../tableClipboard"

const GRID = { name: "sample", fields: ["code", "group"], copyHeaders: ["Code", "Group"] } as const

const originalClipboard = Object.getOwnPropertyDescriptor(navigator, "clipboard")

describe("deferred clipboard text", () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    if (originalClipboard) Object.defineProperty(navigator, "clipboard", originalClipboard)
    else Reflect.deleteProperty(navigator, "clipboard")
  })

  it.each([true, false])("reserves the gesture and only writes validated text (valid: %s)", async (valid) => {
    let finish!: (text: string) => void
    let fail!: (error: Error) => void
    const text = new Promise<string>((resolve, reject) => { finish = resolve; fail = reject })
    let contents = "existing clipboard"
    class Item {
      readonly values: Record<string, Promise<string>>
      constructor(values: Record<string, Promise<string>>) { this.values = values }
    }
    vi.stubGlobal("ClipboardItem", Item)
    const write = vi.fn(async ([item]: Item[]) => { contents = await item.values["text/plain"] })
    Object.defineProperty(navigator, "clipboard", { value: { write }, configurable: true })
    const copying = writeClipboardText(text)
    expect(write).toHaveBeenCalledTimes(1)
    expect(contents).toBe("existing clipboard")
    if (valid) {
      finish("verified SQL")
      await copying
      expect(contents).toBe("verified SQL")
    } else {
      fail(new Error("Connection changed"))
      await expect(copying).rejects.toThrow("Connection changed")
      expect(contents).toBe("existing clipboard")
    }
  })
})

function pasteEvent(text: string) {
  return {
    clipboardData: { getData: () => text },
    preventDefault: vi.fn(),
    stopPropagation: vi.fn(),
  } as unknown as React.ClipboardEvent<HTMLInputElement> & {
    preventDefault: ReturnType<typeof vi.fn>
    stopPropagation: ReturnType<typeof vi.fn>
  }
}

function paste(rows: { code: string; group: string }[], text: string, rowIndex: number, fieldIndex: number) {
  const onUpdate = vi.fn()
  const { result } = renderHook(() => useTwoColumnGridClipboard(GRID, rows, onUpdate))
  const event = pasteEvent(text)
  result.current.handleCellPaste(event, rowIndex, fieldIndex)
  return { onUpdate, event }
}

describe("isGridHeaderRow", () => {
  it("reads the copy headers or the field names, in any case and spacing, as the header", () => {
    expect(isGridHeaderRow(GRID, [" code ", "GROUP"])).toBe(true)
    expect(isGridHeaderRow(GRID, ["Code", "Group", "extra"])).toBe(true)
    expect(isGridHeaderRow(GRID, ["code", "group"])).toBe(true)
  })

  it("does not read data, a partial header or a header in another order as the header", () => {
    expect(isGridHeaderRow(GRID, ["A1", "Group"])).toBe(false)
    expect(isGridHeaderRow(GRID, ["Code"])).toBe(false)
    expect(isGridHeaderRow(GRID, ["Group", "Code"])).toBe(false)
  })
})

describe("useTwoColumnGridClipboard", () => {
  afterEach(() => {
    if (originalClipboard) Object.defineProperty(navigator, "clipboard", originalClipboard)
    else Reflect.deleteProperty(navigator, "clipboard")
  })

  it("fills a pasted range from the cell pasted into, adding blank rows and dropping cells past the last column", () => {
    const { onUpdate, event } = paste([{ code: "A", group: "x" }, { code: "B", group: "y" }], "p\tlost\nq\n", 1, 1)

    expect(event.preventDefault).toHaveBeenCalled()
    expect(event.stopPropagation).toHaveBeenCalled()
    expect(onUpdate).toHaveBeenCalledWith([
      { code: "A", group: "x" },
      { code: "B", group: "p" },
      { code: "", group: "q" },
    ])
  })

  it("drops a copied header pasted into the first column but writes it into the second", () => {
    const rows = [{ code: "A", group: "x" }]

    expect(paste(rows, "Code\tGroup\nB\ty", 0, 0).onUpdate).toHaveBeenCalledWith([{ code: "B", group: "y" }])
    expect(paste(rows, "Code\nB", 0, 1).onUpdate).toHaveBeenCalledWith([
      { code: "A", group: "Code" },
      { code: "", group: "B" },
    ])
  })

  it("leaves one cell's text to the input, and writes nothing for a header alone", () => {
    const single = paste([{ code: "A", group: "x" }], "B", 0, 0)
    expect(single.event.preventDefault).not.toHaveBeenCalled()
    expect(single.onUpdate).not.toHaveBeenCalled()

    const header = paste([{ code: "A", group: "x" }], "Code\tGroup\n", 0, 0)
    expect(header.event.preventDefault).toHaveBeenCalled()
    expect(header.onUpdate).not.toHaveBeenCalled()
  })

  it("copies the grid under its headers, a missing field as an empty cell", () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true })
    const rows = [{ code: "A", group: "x" }, { code: "B" } as { code: string; group: string }]
    const { result } = renderHook(() => useTwoColumnGridClipboard(GRID, rows, vi.fn()))

    result.current.copyTsv()

    expect(writeText).toHaveBeenCalledWith("Code\tGroup\nA\tx\nB\t")
  })

  it("names the grid in the toast when the clipboard refuses", async () => {
    Object.defineProperty(navigator, "clipboard", { value: undefined, configurable: true })
    const { result } = renderHook(() => useTwoColumnGridClipboard(GRID, [], vi.fn()))

    result.current.copyTsv()

    await waitFor(() => {
      expect(useToastStore.getState().toasts).toContainEqual(
        expect.objectContaining({ type: "error", text: "Could not copy sample TSV: Clipboard API is not available" }),
      )
    })
  })
})
