import { describe, expect, it, vi } from "vitest"
import { loadExtensionModule, mountExtension, type ExtensionMountOptions } from "../loadExtensionModule"

const moduleUrl = (source: string) => `data:text/javascript,${encodeURIComponent(source)}`

const options: ExtensionMountOptions = {
  main: document.createElement("div"),
  toolbar: document.createElement("div"),
  apiBase: "/api/extensions/obverse",
  switcherSlot: "haute-view-switcher",
  palette: { open: true, setOpen: vi.fn() },
  priceSample: vi.fn(),
}

describe("loadExtensionModule", () => {
  it("returns a module that exports mount", async () => {
    const module = await loadExtensionModule(moduleUrl("export function mount() { return { unmount() {} } }"))

    expect(typeof module.mount).toBe("function")
  })

  it("rejects a module without a mount function", async () => {
    const url = moduleUrl("export const mount = 1")

    await expect(loadExtensionModule(url)).rejects.toThrow(`${url} does not export a mount function`)
  })

  it("accepts a module that also exports save, and rejects a save that isn't a function", async () => {
    const mount = "export function mount() { return { unmount() {} } }"
    const module = await loadExtensionModule(moduleUrl(`${mount}
export async function save() { return true }`))
    expect(await module.save?.()).toBe(true)

    const url = moduleUrl(`${mount}
export const save = true`)
    await expect(loadExtensionModule(url)).rejects.toThrow(`${url} exports a save that is not a function`)
  })
})

describe("mountExtension", () => {
  it("passes the options through and returns the extension's handle", () => {
    const handle = { unmount: vi.fn() }
    const mount = vi.fn(() => handle)

    expect(mountExtension({ mount }, options)).toBe(handle)
    expect(mount).toHaveBeenCalledWith(options)
  })

  it("rejects a mount that returns no unmount function", () => {
    const mount = vi.fn(() => ({}) as never)

    expect(() => mountExtension({ mount }, options)).toThrow(
      "The extension's mount function did not return an object with an unmount function",
    )
  })
})
