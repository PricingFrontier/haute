import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import useExtensionsStore, { PIPELINE_VIEW } from "../useExtensionsStore"
import useToastStore from "../useToastStore"

const obverse = {
  name: "obverse",
  label: "Obverse",
  api_base: "/api/extensions/obverse",
  entry_url: "/extensions/obverse/obverse-embed.js",
  ready: true,
  detail: null,
}

function respond(body: unknown, status = 200) {
  return vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }),
  )
}

describe("useExtensionsStore", () => {
  beforeEach(() => {
    useExtensionsStore.setState({ extensions: [], activeView: PIPELINE_VIEW, toolbarSlot: null })
    useToastStore.setState({ toasts: [], _toastCounter: 0 })
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it("loads the installed extensions from GET /api/extensions", async () => {
    const fetchSpy = respond({ extensions: [obverse] })

    await useExtensionsStore.getState().load()

    expect(String(fetchSpy.mock.calls[0][0])).toContain("/api/extensions")
    expect(useExtensionsStore.getState().extensions).toEqual([obverse])
    expect(useToastStore.getState().toasts).toEqual([])
  })

  it("leaves the store untouched when nothing is installed, so the editor does not re-render", async () => {
    respond({ extensions: [] })
    const listener = vi.fn()
    const unsubscribe = useExtensionsStore.subscribe(listener)

    await useExtensionsStore.getState().load()
    unsubscribe()

    expect(listener).not.toHaveBeenCalled()
    expect(useExtensionsStore.getState().extensions).toEqual([])
  })

  it("reports a failed list as one error toast and keeps the list empty", async () => {
    respond({ detail: "No such route: /api/extensions" }, 404)

    await useExtensionsStore.getState().load()

    expect(useExtensionsStore.getState().extensions).toEqual([])
    expect(useToastStore.getState().toasts).toEqual([
      expect.objectContaining({
        type: "error",
        text: "Could not list Haute extensions: No such route: /api/extensions",
      }),
    ])
  })

  it("rejects a list that breaks the generated contract", async () => {
    respond({ extensions: [{ ...obverse, ready: "yes" }] })

    await useExtensionsStore.getState().load()

    expect(useExtensionsStore.getState().extensions).toEqual([])
    expect(useToastStore.getState().toasts[0].text).toContain("ExtensionsResponse: invalid contract")
  })

  it("shows an installed extension's view and the pipeline again, and rejects anything else", () => {
    useExtensionsStore.setState({ extensions: [obverse] })
    const { showView } = useExtensionsStore.getState()

    showView("obverse")
    expect(useExtensionsStore.getState().activeView).toBe("obverse")
    showView(PIPELINE_VIEW)
    expect(useExtensionsStore.getState().activeView).toBe(PIPELINE_VIEW)

    expect(() => showView("forms")).toThrow('No installed extension is called "forms"')
    expect(useExtensionsStore.getState().activeView).toBe(PIPELINE_VIEW)
  })

  it("holds the toolbar element the active extension renders its controls into", () => {
    const slot = document.createElement("div")

    useExtensionsStore.getState().setToolbarSlot(slot)
    expect(useExtensionsStore.getState().toolbarSlot).toBe(slot)
    useExtensionsStore.getState().setToolbarSlot(null)
    expect(useExtensionsStore.getState().toolbarSlot).toBeNull()
  })
})
