import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { act, cleanup, render, screen, waitFor } from "@testing-library/react"

vi.mock("../loadExtensionModule", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../loadExtensionModule")>()),
  loadExtensionModule: vi.fn(),
}))

import ExtensionView from "../ExtensionView"
import { loadExtensionModule, type ExtensionMountOptions, type ExtensionModule } from "../loadExtensionModule"
import useExtensionsStore, { PIPELINE_VIEW } from "../../stores/useExtensionsStore"

const obverse = {
  name: "obverse",
  label: "Obverse",
  api_base: "/api/extensions/obverse",
  entry_url: "/extensions/obverse/obverse-embed.js",
  ready: true,
  detail: null,
}

const load = vi.mocked(loadExtensionModule)

function extensionModule() {
  const unmount = vi.fn()
  const mount = vi.fn((_options: ExtensionMountOptions) => ({ unmount }))
  return { module: { mount } satisfies ExtensionModule, mount, unmount }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => {
    resolve = done
  })
  return { promise, resolve }
}

describe("ExtensionView", () => {
  let toolbar: HTMLDivElement

  beforeEach(() => {
    toolbar = document.createElement("div")
    document.body.append(toolbar)
    useExtensionsStore.setState({ extensions: [obverse], activeView: "obverse", toolbarSlot: toolbar })
  })

  afterEach(() => {
    cleanup()
    toolbar.remove()
    load.mockReset()
    useExtensionsStore.setState({ extensions: [], activeView: PIPELINE_VIEW, toolbarSlot: null })
  })

  it("mounts the extension into its host and the toolbar slot, then slots in the switcher", async () => {
    const { module, mount } = extensionModule()
    load.mockResolvedValue(module)

    render(<ExtensionView extension={obverse} />)

    await waitFor(() => expect(mount).toHaveBeenCalledOnce())
    const host = screen.getByTestId("extension-view-host")
    expect(load).toHaveBeenCalledWith("/extensions/obverse/obverse-embed.js")
    expect(mount).toHaveBeenCalledWith({
      main: host,
      toolbar,
      apiBase: "/api/extensions/obverse",
      switcherSlot: "haute-view-switcher",
    })
    const slotted = host.querySelector('[slot="haute-view-switcher"]')
    expect(slotted).toContainElement(screen.getByRole("group", { name: "Views" }))
    expect(screen.queryByRole("status")).toBeNull()
  })

  it("unmounts the extension when its view closes", async () => {
    const { module, mount, unmount } = extensionModule()
    load.mockResolvedValue(module)
    const view = render(<ExtensionView extension={obverse} />)
    await waitFor(() => expect(mount).toHaveBeenCalledOnce())

    view.unmount()

    expect(unmount).toHaveBeenCalledOnce()
  })

  it("never mounts an extension whose module arrives after its view closed", async () => {
    const { module, mount } = extensionModule()
    const pending = deferred<ExtensionModule>()
    load.mockReturnValue(pending.promise)
    const view = render(<ExtensionView extension={obverse} />)
    expect(screen.getByRole("status")).toHaveTextContent("Loading Obverse")

    view.unmount()
    await act(async () => pending.resolve(module))

    expect(mount).not.toHaveBeenCalled()
  })

  it("waits for the toolbar's slot before loading", async () => {
    const { module, mount } = extensionModule()
    load.mockResolvedValue(module)
    useExtensionsStore.setState({ toolbarSlot: null })
    render(<ExtensionView extension={obverse} />)
    expect(load).not.toHaveBeenCalled()

    act(() => useExtensionsStore.getState().setToolbarSlot(toolbar))

    await waitFor(() => expect(mount).toHaveBeenCalledOnce())
  })

  it("shows what to do for an unbuilt extension, without loading it, beside the switcher", () => {
    const unbuilt = { ...obverse, ready: false, detail: "obverse-embed.js does not exist. Build Obverse's front end, then reload." }

    render(<ExtensionView extension={unbuilt} />)

    expect(screen.getByRole("alert")).toHaveTextContent("Build Obverse's front end, then reload.")
    expect(screen.getByRole("group", { name: "Views" })).toBeInTheDocument()
    expect(load).not.toHaveBeenCalled()
  })

  it.each([
    ["a module that fails to load", () => load.mockRejectedValue(new Error("network down")), "network down"],
    [
      "a mount that throws",
      () => load.mockResolvedValue({ mount: () => { throw new Error("no root element") } }),
      "no root element",
    ],
  ])("shows %s beside the switcher", async (_case, arrange, reason) => {
    arrange()

    render(<ExtensionView extension={obverse} />)

    expect(await screen.findByRole("alert")).toHaveTextContent(`Obverse could not be loaded: ${reason}`)
    expect(screen.getByRole("group", { name: "Views" })).toBeInTheDocument()
  })
})
