/**
 * Browser source awaits a promise, handles it, or drops it on purpose with `void`: a
 * rejection nobody waits for would otherwise vanish. These probes lint snippets through
 * the real config, with the type information the two rules need.
 */
import { describe, expect, it } from "vitest"
import { lintProbe } from "./lintProbe"

async function promiseRules(code: string): Promise<string[]> {
  return (await lintProbe(code, undefined, { types: true }))
    .map((message) => message.ruleId ?? "")
    .filter((rule) => rule.includes("promise"))
}

const LOAD = "async function load(): Promise<number> { return 1 }\n"

describe("promise rules", () => {
  it.each([
    ["a call nothing awaits", "export function press(): void { load() }", "@typescript-eslint/no-floating-promises"],
    [
      "an async function where a void callback is expected",
      "export const handlers: { onClick: () => void } = { onClick: load }",
      "@typescript-eslint/no-misused-promises",
    ],
  ])("rejects %s in browser source", async (_case, code, rule) => {
    expect(await promiseRules(LOAD + code)).toEqual([rule])
  })

  it("accepts a promise dropped with void or awaited", async () => {
    const code = LOAD + [
      "export function press(): void { void load() }",
      "export async function wait(): Promise<number> { return await load() }",
      "export const handlers: { onClick: () => void } = { onClick: () => void load() }",
    ].join("\n")
    expect(await promiseRules(code)).toEqual([])
  })
}, 120_000)
