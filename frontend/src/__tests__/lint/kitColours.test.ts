/**
 * haute-ui's classes keep their colours (specs/frontend-shared). The kit's component
 * stylesheets sit under Tailwind's utilities, so a utility written beside a kit class
 * wins: a layout utility is how a caller lays a kit element out, and a colour utility,
 * which would also outrank the kit's hover, pressed and disabled colours, is rejected.
 * These probes lint snippets through the real config.
 */
import { describe, expect, it } from "vitest"
import { restrictedMessages } from "./probe"

const KEEPS_ITS_COLOURS = "the kit draws its own colours and states"

describe("colour utilities beside a haute-ui class", () => {
  it.each([
    ['export const a = "toolbar-btn px-2.5 py-1 text-[12px] text-[var(--text-muted)] w-full"'],
    ['export const a = "toolbar-btn rounded-md bg-red-500"'],
    ['export const a = "toolbar-fill toolbar-fill-save hover:bg-[var(--accent-hover)]"'],
    ['export const a = "palette-item border-white"'],
    ['export const a = "side-panel-title text-(--text-muted)"'],
    ['export const a = "bg-transparent toolbar-action"'],
    ['export const a = (on: boolean) => `toolbar-btn px-2 ${on ? "bg-[var(--accent-soft)]" : ""}`'],
    ['export const a = (width: string) => `toolbar-btn text-[#fff] ${width}`'],
    // Behind a variant, with an opacity, marked important, or as an arbitrary property.
    ['export const a = "toolbar-btn hover:bg-red-500/50"'],
    ['export const a = "toolbar-btn [&:hover]:bg-red-500"'],
    ['export const a = "toolbar-btn data-[state=open]:text-[var(--text-accent)]"'],
    ['export const a = "toolbar-btn group-hover/row:border-white"'],
    ['export const a = "toolbar-btn bg-red-500!"'],
    ['export const a = "toolbar-btn !bg-[var(--accent)]"'],
    ['export const a = "toolbar-btn bg-black/40"'],
    ['export const a = "toolbar-btn [background-color:var(--accent)]"'],
    ['export const a = "toolbar-btn hover:[color:red]"'],
    ['export const a = "toolbar-btn bg-[red]"'],
    ['export const a = "toolbar-btn 2xl:bg-red-500"'],
    ['export const a = "toolbar-btn ring-offset-red-500"'],
    ['export const a = "toolbar divide-[var(--border)]"'],
    ['export const a = "toolbar-btn border-x-red-500"'],
    ['export const a = "toolbar-btn border-t-[var(--danger)]"'],
    ['export const a = "toolbar-btn inset-ring-white"'],
    // index.css's own state classes are unlayered, so they outrank the kit as a utility does.
    ['export const a = "toolbar-btn focus-ring"'],
    ['export const a = "toolbar-btn px-2 hover-bg"'],
    // Put together from separate strings.
    ['export const a = (danger: boolean) => classNames("toolbar-btn", danger && "bg-red-500")'],
    ['export const a = (danger: boolean) => classNames("toolbar-btn px-2", danger ? "text-[var(--danger)]" : "w-full")'],
    ['export const a = (on: boolean) => ["toolbar-fill", on && "bg-[var(--accent)]"].filter(Boolean).join(" ")'],
    ['export const a = (on: boolean) => classNames(on && "toolbar-btn", "bg-red-500")'],
    ['export const a = (width: string) => classNames(`toolbar-btn ${width} bg-red-500`, "px-2")'],
    ['export const a = (on: boolean) => [on ? "toolbar-btn" : "w-full", "text-white"].join(" ")'],
    ['export const a = (on: boolean) => "toolbar-btn " + (on ? "bg-[var(--accent)]" : "") + " w-full"'],
    ['export const a = (on: boolean) => classNames("bg-red-500", on && "toolbar-btn")'],
    ['export const a = (a: boolean, b: boolean) => classNames(a ? (b ? "text-white" : "px-2") : "px-1", "toolbar-btn")'],
  ])("rejects %s", async (code) => {
    const messages = await restrictedMessages(code)
    expect(messages).toHaveLength(1)
    expect(messages[0]).toContain(KEEPS_ITS_COLOURS)
  })

  it.each([
    ['export const a = <ToolbarButton className="bg-[var(--accent-soft)]">Run</ToolbarButton>'],
    ['export const a = <ToolbarColumn className="w-fit border-red-500" />'],
    ['export const a = (on: boolean) => <ToolbarButton className={on ? "text-[var(--danger)]" : undefined}>Run</ToolbarButton>'],
    ['export const a = (on: boolean) => <ToolbarButton className={classNames("w-auto", on && "hover:bg-red-500/50")}>Run</ToolbarButton>'],
    ['export const a = (on: boolean) => <span className={["toolbar-btn px-2", on && "text-[var(--text-muted)]"].join(" ")}>Git</span>'],
    ['export const a = <ToolbarButton className="focus-ring shrink-0">Refresh</ToolbarButton>'],
    ['export const a = <Button className="ml-auto bg-[var(--danger-soft)]">Delete</Button>'],
    ['export const a = <ButtonLink href="/" className="text-white">Quotes</ButtonLink>'],
    ['export const a = <span className="toolbar-btn btn hover:bg-[var(--bg-hover)]">Open</span>'],
  ])("rejects %s", async (code) => {
    const messages = await restrictedMessages(code, "src/panels/probe.tsx")
    expect(messages).toHaveLength(1)
    expect(messages[0]).toContain(KEEPS_ITS_COLOURS)
  })

  it("accepts layout utilities beside a kit class, and colour utilities away from one", async () => {
    const code = [
      'export const a = "toolbar-btn inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium disabled:opacity-50"',
      'export const b = "toolbar-btn toolbar-action w-auto"',
      'export const c = "toolbar-btn flex px-2.5 py-1 text-[12px] font-mono border-2 shadow-sm outline-none ring-2 truncate"',
      'export const d = "toolbar flex-wrap gap-y-2 [&>div]:shrink-0"',
      'export const e = "toolbar-brand-name max-w-full truncate leading-[1.2]"',
      // index.css's own class, not the kit's.
      'export const f = "toolbar-number-input w-28 text-right bg-[var(--bg-input)]"',
      'export const g = "rounded px-2 text-[var(--text-muted)] bg-[var(--bg-hover)]"',
      'export const h = "Workbench toolbar"',
      'export const i = (on: boolean) => `flex ${on ? "bg-[var(--accent-soft)]" : ""}`',
      // Utilities that share a colour utility's stem and set no colour.
      'export const p = "toolbar-btn border-x-2 border-t border-dashed divide-x-2"',
      'export const j = "toolbar-btn border-spacing-10 text-[length:var(--size)] shadow-[0_0_0_2px] outline-offset-2 ring-offset-2 [background-size:cover]"',
      'export const n = "toolbar-btn w-auto focus-visible:[outline:none] focus-visible:[box-shadow:0_0_0_2px_var(--accent-soft)]"',
      'export const o = "rounded focus-ring hover-bg"',
      'export const k = (wide: boolean) => classNames("toolbar-btn", wide && "w-full", "px-2")',
      'export const l = (on: boolean) => classNames("rounded", on && "bg-[var(--accent-soft)]")',
      'export const m = (on: boolean) => "toolbar-btn " + (on ? "w-full" : "w-auto")',
      // A kit class and a colour that are not members of one class list.
      'export const r = register("toolbar-btn", () => describe("bg-red-500"))',
      'export const s = "toolbar-btn " + describe(() => "bg-red-500")',
    ].join("\n")
    expect(await restrictedMessages(code)).toEqual([])
  })

  it("accepts a layout class on a kit component", async () => {
    const code = [
      'export const a = <ToolbarButton className="w-auto">Run</ToolbarButton>',
      'export const b = <ToolbarColumn className="ml-2 min-w-[136px]" />',
      'export const c = <button className="bg-[var(--accent)]">Run</button>',
      'export const f = <Button className="ml-auto self-start">Save note</Button>',
      'export const g = <ButtonLink href="/" variant="fill" className="ml-2">New quote</ButtonLink>',
      // A kit class and a colour on different elements of one component.
      'export const d = [1, 2].map((n) => <header key={n} className="toolbar"><span className="text-[var(--text-muted)]">{n}</span></header>)',
      'export const e = memo(() => <div className="toolbar-column"><p className="bg-[var(--bg-hover)]">x</p></div>)',
    ].join("\n")
    expect(await restrictedMessages(code, "src/panels/probe.tsx")).toEqual([])
  })

  // The kit's own files, and files with an override block of their own, keep the ban.
  it.each([
    ["src/haute-ui/probe.ts"],
    ["src/hooks/useTracing.ts"],
    ["src/api/errors.ts"],
    ["src/utils/isoDate.ts"],
    ["src/utils/formatBytes.ts"],
    ["src/types/guards.ts"],
  ])("keeps the ban in %s", async (filePath) => {
    const messages = await restrictedMessages('export const a = "toolbar-btn bg-red-500"', filePath)
    expect(messages).toHaveLength(1)
    expect(messages[0]).toContain(KEEPS_ITS_COLOURS)
  })

  it("does not police test files", async () => {
    expect(await restrictedMessages('export const a = "toolbar-btn bg-red-500"', "src/panels/__tests__/probe.test.ts")).toEqual([])
  })
})
