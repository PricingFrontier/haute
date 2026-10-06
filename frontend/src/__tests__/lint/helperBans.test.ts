/**
 * The repository's ESLint config rejects local copies of the shared polling,
 * error-text, formatting and object-guard helpers, and exempts only each
 * helper's owning module. These probes lint snippets through the real config.
 */
import { ESLint } from "eslint"
import { beforeAll, describe, expect, it } from "vitest"

let eslint: ESLint

beforeAll(() => {
  eslint = new ESLint({ cwd: process.cwd() })
})

async function restrictedMessages(code: string, filePath = "src/panels/probe.ts"): Promise<string[]> {
  const [result] = await eslint.lintText(code, { filePath })
  return result.messages
    .filter((message) => message.ruleId === "no-restricted-syntax")
    .map((message) => message.message)
}

describe("helper bans", () => {
  it.each([
    ["for (;;) {}", "waitForJob"],
    ["while (true) { break }", "waitForJob"],
    ["setInterval(() => {}, 1000)", "waitForJob"],
    ["window.setInterval(() => {}, 1000)", "waitForJob"],
    ["export function errorDetail(e: unknown) { return String(e) }", "apiErrorMessage"],
    ["export const requestErrorDetail = (e: unknown) => String(e)", "apiErrorMessage"],
    ["export const errorMessage = function (e: unknown) { return String(e) }", "apiErrorMessage"],
    ["export function gitErrorMessage(e: unknown) { return String(e) }", "apiErrorMessage"],
    ["export const text = (e: { detail?: string; message: string }) => e.detail || e.message", "apiErrorMessage"],
    ["export const text = (e: { detail?: string; message: string }) => e.detail ?? e.message", "apiErrorMessage"],
    ["export function formatMemory(b: number) { return `${b}` }", "formatBytes"],
    ["export const formatSize = (b: number) => `${b}`", "formatBytes"],
    ["export function formatElapsed(s: number) { return `${s}` }", "formatDuration"],
    ["export const isRecord = (v: unknown) => typeof v === 'object'", "isPlainObject"],
    ["export function asRecord(v: unknown) { return v as Record<string, unknown> }", "isPlainObject"],
    ["export function isPlainObject(v: unknown) { return typeof v === 'object' }", "isPlainObject"],
    ["export function isObjectLiteral(v: unknown) { return typeof v === 'object' }", "isObjectLiteral"],
  ])("rejects %s", async (code, helper) => {
    const messages = await restrictedMessages(code)
    expect(messages).toHaveLength(1)
    expect(messages[0]).toContain(helper)
  })

  it("accepts the shared helpers and ordinary names", async () => {
    const code = [
      'import { apiErrorMessage } from "../api/errors"',
      "export const errorMessage = apiErrorMessage(new Error('x'))",
      "export function describe(e: unknown) { return apiErrorMessage(e, 'Failed.') }",
      "for (let attempt = 0; ; attempt += 1) { if (attempt > 2) break }",
      "while (Math.random() > 2) { break }",
    ].join("\n")
    expect(await restrictedMessages(code)).toEqual([])
  })

  it.each([
    ["src/utils/formatBytes.ts", "export function formatBytes(b: number) { return `${b}` }"],
    ["src/utils/formatValue.ts", "export function formatDuration(s: number) { return `${s}` }"],
    ["src/types/guards.ts", "export function isPlainObject(v: unknown) { return typeof v === 'object' }"],
    ["src/utils/objectLiteral.ts", "export function isObjectLiteral(v: unknown) { return typeof v === 'object' }"],
    ["src/api/errors.ts", "export const text = (e: { detail?: string; message: string }) => e.detail || e.message"],
  ])("exempts the owning module %s", async (filePath, code) => {
    expect(await restrictedMessages(code, filePath)).toEqual([])
  })

  it("does not police test files", async () => {
    expect(await restrictedMessages("for (;;) { break }", "src/panels/__tests__/probe.test.ts")).toEqual([])
  })
})
