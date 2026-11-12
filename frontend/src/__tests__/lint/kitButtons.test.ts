/**
 * The kit's button classes are written only inside haute-ui (specs/frontend-shared):
 * elsewhere a button is a `Button`, a `ButtonLink`, a `ToolbarButton` or a `ToolbarLink`,
 * and a toolbar's status a `ToolbarStatus`, so a new button cannot start another copy of
 * its size and states. No file outside the kit is excused. These probes lint snippets
 * through the real config.
 */
import { describe, expect, it } from "vitest"
import { restrictedMessages, restrictedSelectorMessages } from "./probe"

const USE_THE_KIT = "Draw a button with haute-ui's Button, ButtonLink, ToolbarButton or ToolbarLink, and a toolbar's status with ToolbarStatus"

describe("the kit's button classes outside haute-ui", () => {
  it.each([
    ['export const a = "toolbar-btn px-2.5 py-1 text-[12px] font-medium rounded-md"'],
    ['export const a = "inline-flex toolbar-btn"'],
    // Each name alone, and with a suffix of any length.
    ['export const a = "toolbar-fill px-3"'],
    ['export const a = "toolbar-fill-save"'],
    ['export const a = "toolbar-fill-save-active"'],
    ['export const a = "toolbar-action w-auto"'],
    ['export const a = "toolbar-action-compact"'],
    ['export const a = "toolbar-btn-reset"'],
    ['export const a = "btn"'],
    ['export const a = "btn-icon"'],
    ['export const a = "btn-fill ml-2"'],
    ['export const a = "btn-v2"'],
    ['export const a = (wide: boolean) => `toolbar-btn ${wide ? "w-full" : "w-auto"}`'],
    // A template's escaped line break parts two classes as a space does.
    ['export const a = `toolbar-btn\\npx-2`'],
  ])("rejects %s", async (code) => {
    const messages = await restrictedMessages(code)
    expect(messages).toHaveLength(1)
    expect(messages[0]).toContain(USE_THE_KIT)
  })

  it("rejects the class on an element, and accepts the kit's components", async () => {
    const written = await restrictedMessages('export const a = <button className="toolbar-btn px-2">Run</button>', "src/panels/probe.tsx")
    expect(written).toHaveLength(1)
    expect(written[0]).toContain(USE_THE_KIT)

    const code = [
      'export const a = <Button className="ml-auto">Run</Button>',
      'export const b = <ButtonLink href="/" variant="fill">New quote</ButtonLink>',
      'export const c = <ToolbarButton className="w-auto">Refresh</ToolbarButton>',
      'export const d = <ToolbarLink href="/" className="w-auto">Quotes</ToolbarLink>',
      'export const e = <ToolbarStatus className="min-w-[136px]">Checking Git…</ToolbarStatus>',
    ].join("\n")
    expect(await restrictedMessages(code, "src/panels/probe.tsx")).toEqual([])
  })

  it("accepts the kit's other classes, index.css's own, and words that only look alike", async () => {
    const code = [
      'export const a = "toolbar flex-wrap gap-y-2"',
      'export const b = "toolbar-column w-fit"',
      'export const c = "toolbar-brand-name truncate"',
      'export const d = "toolbar-number-input w-28"',
      'export const e = "add-row-btn icon-danger-btn"',
      'export const f = "side-panel-title"',
      'export const g = "my-btn subtoolbar-btn button-btn toolbar-buttons btnx"',
      'export const h = "Press the toolbar button"',
    ].join("\n")
    expect(await restrictedMessages(code)).toEqual([])
  })

  it("lets the kit write its own classes, and holds it to every other ban", async () => {
    expect(await restrictedMessages('export const a = "toolbar-btn toolbar-action"', "src/haute-ui/probe.ts")).toEqual([])
    const coloured = await restrictedMessages('export const a = "toolbar-btn bg-red-500"', "src/haute-ui/probe.ts")
    expect(coloured).toHaveLength(1)
    expect(coloured[0]).toContain("the kit draws its own colours and states")
    const ordinary = await restrictedSelectorMessages("src/panels/probe.ts")
    expect(await restrictedSelectorMessages("src/haute-ui/probe.ts")).toEqual(ordinary.filter((message) => !message.includes(USE_THE_KIT)))
  })

  // Files with an override block of their own keep the ban, as do the eight files that
  // wrote the surface class with sizes of their own before it and were excused until
  // their buttons moved onto the kit's.
  it.each([
    ["src/hooks/useTracing.ts"],
    ["src/api/errors.ts"],
    ["src/utils/isoDate.ts"],
    ["src/utils/formatBytes.ts"],
    ["src/types/guards.ts"],
    ["src/app/QuoteView.tsx"],
    ["src/components/BranchIndicator.tsx"],
    ["src/components/SourcePipelineControls.tsx"],
    ["src/components/Toolbar.tsx"],
    ["src/workbench/ViewSwitcher.tsx"],
    ["src/components/PipelineSettingsModal.tsx"],
    ["src/components/Toast.tsx"],
    ["src/workbench/WorkbenchView.tsx"],
  ])(
    "keeps the ban in %s",
    async (filePath) => {
      const messages = await restrictedMessages('export const a = "toolbar-btn px-2"', filePath)
      expect(messages).toHaveLength(1)
      expect(messages[0]).toContain(USE_THE_KIT)
    },
  )

  it("does not police test files", async () => {
    expect(await restrictedMessages('export const a = "toolbar-btn px-2"', "src/panels/__tests__/probe.test.ts")).toEqual([])
  })
})
