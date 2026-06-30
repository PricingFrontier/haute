import { describe, expect, it } from "vitest"
import { readFileSync } from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"

const HERE = path.dirname(fileURLToPath(import.meta.url))
const APP_TSX = readFileSync(path.resolve(HERE, "..", "App.tsx"), "utf8")

describe("App ReactFlow connection mode", () => {
  it("enables loose connections so output handles can be joined to output handles", () => {
    expect(APP_TSX).toMatch(/ConnectionMode/)
    expect(APP_TSX).toMatch(/<ReactFlow[\s\S]*connectionMode=\{ConnectionMode\.Loose\}/)
  })

  it("passes a graph-level connection validator to ReactFlow", () => {
    expect(APP_TSX).toMatch(/<ReactFlow[\s\S]*isValidConnection=\{isValidConnection\}/)
  })
})
