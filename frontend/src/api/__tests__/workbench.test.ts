/**
 * The workbook an Export writes from Preview's last pricing (specs/workbench, Control flow
 * 38): the request's body and the file's name, read from `filename*`, then `filename`.
 */
import { afterEach, describe, expect, it, vi } from "vitest"
import type { FormSpec } from "../types"
import { attachmentName, exportWorkbenchFile } from "../workbench"

const FORM = { name: "x" } as unknown as FormSpec
const PRICE = { sample: { policy: { state: "NY" } }, tables: { pricing: [{ premium: 1 }] } }

describe("exportWorkbenchFile", () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it("reads a file's name from filename*, else from filename, and none from neither", () => {
    expect(attachmentName("attachment; filename=\"Caf_.xlsx\"; filename*=UTF-8''Caf%C3%A9%20Ltd.xlsx")).toBe("Café Ltd.xlsx")
    expect(attachmentName('attachment; filename="Slip.xlsx"')).toBe("Slip.xlsx")
    expect(attachmentName("attachment")).toBeNull()
    expect(attachmentName(null)).toBeNull()
  })

  it("posts the form, the Export and the price, and answers the file by its name", async () => {
    const fetch = vi.fn(async () => new Response("PK", { status: 200, headers: { "Content-Disposition": 'attachment; filename="Slip.xlsx"' } }))
    vi.stubGlobal("fetch", fetch)

    const file = await exportWorkbenchFile(FORM, "w_out", PRICE)

    expect(file.fileName).toBe("Slip.xlsx")
    expect(await file.blob.text()).toBe("PK")
    const [url, init] = fetch.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe("/api/workbench/export")
    expect(init.method).toBe("POST")
    expect(JSON.parse(init.body as string)).toEqual({ form: FORM, component: "w_out", ...PRICE })
  })

  it("refuses an answer that names no file", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("PK", { status: 200 })))

    await expect(exportWorkbenchFile(FORM, "w_out", PRICE)).rejects.toThrow("The export's answer names no file")
  })
})
