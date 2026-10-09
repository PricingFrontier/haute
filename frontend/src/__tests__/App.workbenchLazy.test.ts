/**
 * Lazy-loading enforcement for the workbench's view (specs/workbench), a mirror of the
 * assistant panel's guard: App.tsx loads the view and its toolbar only through
 * React.lazy(import()), and the eager workbench chrome (the switcher, the status store
 * and the host) never statically imports the view, the schema editor, the form store or
 * the form's operations — keeping them out of the initial bundle within the bundle-size
 * gate.
 */

import { readFileSync } from "node:fs"
import path from "node:path"

import { describe, expect, it } from "vitest"

const SRC = path.resolve(__dirname, "..")
const appSource = readFileSync(path.join(SRC, "App.tsx"), "utf8")
// The lazy workbench chunks: only the view and its toolbar may import these statically.
const LAZY_WORKBENCH_MODULES =
  /(?:workbench\/WorkbenchView|workbench\/WorkbenchToolbar|workbench\/SchemaEditor|stores\/useWorkbenchFormStore|utils\/workbenchForm$)/
// Eager modules that take part in the view's host: they read the status store alone.
const EAGER_WORKBENCH_CHROME = [
  "App.tsx",
  "components/Toolbar.tsx",
  "components/ProjectControls.tsx",
  "workbench/ViewSwitcher.tsx",
  "stores/useWorkbenchStore.ts",
  "hooks/useWorkbenchTables.ts",
]

function staticImportsOf(source: string): string[] {
  return [...source.matchAll(/^import\s+(?:type\s+)?[^"']*["']([^"']+)["']/gms)].map(
    (match) => match[1],
  )
}

describe("workbench view lazy-loading guard", () => {
  it("App.tsx loads the view and its toolbar only through React.lazy(import())", () => {
    expect(appSource).toContain('import("./workbench/WorkbenchView")')
    expect(appSource).toContain('import("./workbench/WorkbenchToolbar")')
  })

  it("the eager workbench chrome imports neither the view, the schema editor, the form store nor the form's operations", () => {
    const offenders = EAGER_WORKBENCH_CHROME.flatMap((file) =>
      staticImportsOf(readFileSync(path.join(SRC, file), "utf8"))
        .filter((spec) => LAZY_WORKBENCH_MODULES.test(spec))
        .map((spec) => `${file}: ${spec}`),
    )
    expect(offenders).toEqual([])
  })
})
