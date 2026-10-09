import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"

vi.mock("../loadExtensionModule", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../loadExtensionModule")>()),
  loadExtensionModule: vi.fn(),
}))

// What the panels show has their own tests; the view owns where they open.
vi.mock("../../panels/GitPanel", () => ({
  default: ({ onClose, onSave }: { onClose: () => void; onSave: () => Promise<boolean> }) => (
    <div data-testid="git-panel-stub">
      <button onClick={onClose}>Close Git</button>
      <button onClick={() => void onSave()}>Save from Git</button>
    </div>
  ),
}))
vi.mock("../../panels/assistant/AssistantPanel", () => ({
  default: ({ isInsideSubmodel, readOnly }: { isInsideSubmodel: boolean; readOnly: boolean }) => (
    <div data-testid="assistant-panel-stub" data-inside-submodel={String(isInsideSubmodel)} data-read-only={String(readOnly)} />
  ),
}))

import type { ExtensionInfo } from "../../api/types"
import ExtensionView from "../ExtensionView"
import {
  loadExtensionModule,
  type ExtensionMountOptions,
  type ExtensionModule,
  type PricedSample,
  type WorkbenchTables,
} from "../loadExtensionModule"
import useExtensionsStore, { PIPELINE_VIEW } from "../../stores/useExtensionsStore"
import useUIStore from "../../stores/useUIStore"

const obverse = {
  name: "obverse",
  label: "Obverse",
  api_base: "/api/extensions/obverse",
  entry_url: "/extensions/obverse/obverse-embed.js",
  ready: true,
  detail: null,
  quote_tables: false,
  response_tables: false,
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

const onSave = vi.fn(() => Promise.resolve(true))
const priceSample = vi.fn((_workbench: WorkbenchTables) => Promise.resolve<PricedSample>({ tables: {} }))
const workbench: WorkbenchTables = { tables: [], sample: { policy_details: { exposure: 100000 } }, response_tables: [] }

function view(extension: ExtensionInfo = obverse, price: (workbench: WorkbenchTables) => Promise<PricedSample> = priceSample) {
  return <ExtensionView extension={extension} onSave={onSave} isInsideSubmodel={false} readOnly={false} priceSample={price} />
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
    onSave.mockClear()
    priceSample.mockClear()
    useExtensionsStore.setState({ extensions: [], activeView: PIPELINE_VIEW, toolbarSlot: null })
    useUIStore.setState({ paletteOpen: true, gitOpen: false, assistantOpen: false })
  })

  it("mounts the extension into its host and the toolbar slot, then slots in the switcher", async () => {
    const { module, mount } = extensionModule()
    load.mockResolvedValue(module)

    render(view())

    await waitFor(() => expect(mount).toHaveBeenCalledOnce())
    const host = screen.getByTestId("extension-view-host")
    expect(load).toHaveBeenCalledWith("/extensions/obverse/obverse-embed.js")
    expect(mount).toHaveBeenCalledWith({
      main: host,
      toolbar,
      apiBase: "/api/extensions/obverse",
      switcherSlot: "haute-view-switcher",
      palette: { open: true, setOpen: useUIStore.getState().setPaletteOpen },
      priceSample: expect.any(Function),
    })
    const slotted = host.querySelector('[slot="haute-view-switcher"]')
    expect(slotted).toContainElement(screen.getByRole("group", { name: "Views" }))
    expect(screen.queryByRole("status")).toBeNull()
  })

  it("prices the sample with the editor's latest, without remounting the view", async () => {
    const { module, mount, unmount } = extensionModule()
    load.mockResolvedValue(module)
    const { rerender } = render(view())
    await waitFor(() => expect(mount).toHaveBeenCalledOnce())
    const later = vi.fn((_workbench: WorkbenchTables) => Promise.resolve({ tables: { pricing_output: [{ premium: 2000 }] } }))

    rerender(view(obverse, later))

    await expect(mount.mock.calls[0][0].priceSample(workbench)).resolves.toEqual({ tables: { pricing_output: [{ premium: 2000 }] } })
    expect(later).toHaveBeenCalledWith(workbench)
    expect(priceSample).not.toHaveBeenCalled()
    expect(mount).toHaveBeenCalledOnce()
    expect(unmount).not.toHaveBeenCalled()
  })

  it("shares the palette's state: the extension collapses it, the switcher turns compact, and nothing remounts", async () => {
    const { module, mount, unmount } = extensionModule()
    load.mockResolvedValue(module)
    useUIStore.setState({ paletteOpen: false })
    render(view())
    await waitFor(() => expect(mount).toHaveBeenCalledOnce())
    const { palette } = mount.mock.calls[0][0]
    expect(palette.open).toBe(false)

    act(() => palette.setOpen(true))
    expect(useUIStore.getState().paletteOpen).toBe(true)
    expect(within(screen.getByRole("group", { name: "Views" })).getByRole("button", { name: "Pricing" })).toHaveTextContent("Pricing")

    act(() => palette.setOpen(false))
    // Compact: icon buttons named by their labels, with no visible text.
    expect(within(screen.getByRole("group", { name: "Views" })).getByRole("button", { name: "Pricing" })).toHaveTextContent("")
    expect(mount).toHaveBeenCalledOnce()
    expect(unmount).not.toHaveBeenCalled()
  })

  it("opens the Git panel beside the view, and otherwise the Assistant panel", async () => {
    load.mockResolvedValue(extensionModule().module)
    useUIStore.setState({ gitOpen: true })
    render(<ExtensionView extension={obverse} onSave={onSave} isInsideSubmodel readOnly priceSample={priceSample} />)

    const git = within(screen.getByRole("complementary", { name: "Version control" }))
    fireEvent.click(await git.findByRole("button", { name: "Save from Git" }))
    expect(onSave).toHaveBeenCalledOnce()
    fireEvent.click(git.getByRole("button", { name: "Close Git" }))
    expect(useUIStore.getState().gitOpen).toBe(false)
    expect(screen.queryByTestId("git-panel-stub")).toBeNull()

    act(() => useUIStore.getState().setAssistantOpen(true))
    const assistant = await screen.findByTestId("assistant-panel-stub")
    expect(screen.getByRole("complementary", { name: "Assistant" })).toContainElement(assistant)
    expect(assistant).toHaveAttribute("data-inside-submodel", "true")
    expect(assistant).toHaveAttribute("data-read-only", "true")
  })

  it("unmounts the extension when its view closes", async () => {
    const { module, mount, unmount } = extensionModule()
    load.mockResolvedValue(module)
    const rendered = render(view())
    await waitFor(() => expect(mount).toHaveBeenCalledOnce())

    rendered.unmount()

    expect(unmount).toHaveBeenCalledOnce()
  })

  it("gives the toolbar's Save the module's save while the view is mounted", async () => {
    const { module, mount } = extensionModule()
    const save = vi.fn(() => Promise.resolve(true))
    load.mockResolvedValue({ ...module, save })
    const rendered = render(view())
    await waitFor(() => expect(mount).toHaveBeenCalledOnce())

    expect(useExtensionsStore.getState().viewSave).toBe(save)
    rendered.unmount()
    expect(useExtensionsStore.getState().viewSave).toBeNull()
  })

  it("never mounts an extension whose module arrives after its view closed", async () => {
    const { module, mount } = extensionModule()
    const pending = deferred<ExtensionModule>()
    load.mockReturnValue(pending.promise)
    const rendered = render(view())
    expect(screen.getByRole("status")).toHaveTextContent("Loading Obverse")

    rendered.unmount()
    await act(async () => pending.resolve(module))

    expect(mount).not.toHaveBeenCalled()
  })

  it("waits for the toolbar's slot before loading", async () => {
    const { module, mount } = extensionModule()
    load.mockResolvedValue(module)
    useExtensionsStore.setState({ toolbarSlot: null })
    render(view())
    expect(load).not.toHaveBeenCalled()

    act(() => useExtensionsStore.getState().setToolbarSlot(toolbar))

    await waitFor(() => expect(mount).toHaveBeenCalledOnce())
  })

  it("shows what to do for an unbuilt extension, without loading it, beside the switcher", () => {
    const unbuilt = { ...obverse, ready: false, detail: "obverse-embed.js does not exist. Build Obverse's front end, then reload." }

    render(view(unbuilt))

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

    render(view())

    expect(await screen.findByRole("alert")).toHaveTextContent(`Obverse could not be loaded: ${reason}`)
    expect(screen.getByRole("group", { name: "Views" })).toBeInTheDocument()
  })
})
