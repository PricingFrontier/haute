/**
 * The browser side of the extension contract (specs/extensions). An
 * extension's module exports `mount`, which renders its view into elements the
 * editor gives it and returns a handle to unmount it again.
 */

/** The slot an extension puts at the bottom of its palette; the editor fills it with the view switcher. */
export const SWITCHER_SLOT = "haute-view-switcher"

export interface ExtensionMountOptions {
  /** Fills the area below the toolbar: the extension's view renders here. */
  main: HTMLElement
  /** The toolbar's space for the extension's own controls. */
  toolbar: HTMLElement
  /** Where the server mounts the extension's API, e.g. `/api/extensions/obverse`. */
  apiBase: string
  /** `SWITCHER_SLOT`. */
  switcherSlot: string
  /** The palette's state, which both views share: collapsing one collapses the other. */
  palette: ExtensionPalette
}

export interface ExtensionPalette {
  /** Whether the node palette was open when the view mounted. */
  open: boolean
  /** Open or collapse the palette; the slotted switcher turns compact while it is collapsed. */
  setOpen: (open: boolean) => void
}

export interface ExtensionHandle {
  unmount: () => void
}

export interface ExtensionModule {
  mount: (options: ExtensionMountOptions) => ExtensionHandle
  /**
   * The toolbar's Save while the view shows: write the view's unsaved work to the
   * project, resolving whether it saved. Without it, Save saves the pipeline.
   */
  save?: () => Promise<boolean>
}

const hasFunction = (value: unknown, name: string): boolean =>
  typeof value === "object" && value !== null && typeof (value as Record<string, unknown>)[name] === "function"

/** Import an extension's module from the URL the server lists. */
export async function loadExtensionModule(url: string): Promise<ExtensionModule> {
  // The extension serves its own module, so it is never part of the editor's bundle.
  const module: unknown = await import(/* @vite-ignore */ url)
  if (!hasFunction(module, "mount")) throw new Error(`${url} does not export a mount function`)
  if ((module as Record<string, unknown>).save !== undefined && !hasFunction(module, "save")) {
    throw new Error(`${url} exports a save that is not a function`)
  }
  return module as ExtensionModule
}

/** Mount the extension's view, checking it returned a handle the editor can unmount. */
export function mountExtension(module: ExtensionModule, options: ExtensionMountOptions): ExtensionHandle {
  const handle: unknown = module.mount(options)
  if (!hasFunction(handle, "unmount")) {
    throw new Error("The extension's mount function did not return an object with an unmount function")
  }
  return handle as ExtensionHandle
}
