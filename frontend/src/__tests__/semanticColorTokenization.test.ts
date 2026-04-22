import { describe, it, expect } from "vitest"
import { readFileSync, readdirSync, statSync } from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"

const HERE = path.dirname(fileURLToPath(import.meta.url))
const SRC_ROOT = path.resolve(HERE, "..")

const HEX_COLOR_LITERAL = /#[0-9a-fA-F]{3,8}(?![0-9a-fA-F])/g
const RGB_COLOR_LITERAL = /rgba?\(\s*\d{1,3}\s*,\s*\d{1,3}\s*,\s*\d{1,3}(?:\s*,\s*(?:\d+(?:\.\d+)?|\.\d+))?\s*\)/g

const COLOR_TOKEN_MODULE = path.normalize("theme/colors.ts")

function collectSourceFiles(dir: string): string[] {
  const out: string[] = []
  for (const entry of readdirSync(dir)) {
    const full = path.join(dir, entry)
    const info = statSync(full)
    if (info.isDirectory()) {
      if (entry === "__tests__") continue
      out.push(...collectSourceFiles(full))
      continue
    }
    if (!/\.(ts|tsx)$/.test(entry)) continue
    out.push(full)
  }
  return out
}

function isAllowedPath(rel: string): boolean {
  const normalized = rel.split(path.sep).join(path.posix.sep)
  if (normalized.includes("/__tests__/")) return true
  if (normalized.endsWith(".test.ts") || normalized.endsWith(".test.tsx")) return true
  if (normalized.endsWith(".spec.ts") || normalized.endsWith(".spec.tsx")) return true
  return path.normalize(normalized) === COLOR_TOKEN_MODULE
}

function stripComments(text: string): string {
  return text
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/^\s*\/\/.*$/gm, "")
}

function isHexColorLiteral(text: string, index: number): boolean {
  if (text[index - 1] === "&") {
    return false
  }

  return true
}

function isRgbColorLiteral(raw: string): boolean {
  const [red, green, blue] = raw.match(/\d{1,3}/g)?.map(Number) ?? []
  return !(red === green && green === blue)
}

describe("semantic color tokenization", () => {
  it("keeps hard-coded color literals out of live frontend files", () => {
    const offenders: string[] = []
    for (const file of collectSourceFiles(SRC_ROOT)) {
      const rel = path.relative(SRC_ROOT, file)
      if (isAllowedPath(rel)) continue
      const text = stripComments(readFileSync(file, "utf8"))
      for (const match of text.matchAll(HEX_COLOR_LITERAL)) {
        if (isHexColorLiteral(text, match.index ?? 0)) {
          offenders.push(`${rel} -> ${match[0]}`)
        }
      }
      for (const match of text.matchAll(RGB_COLOR_LITERAL)) {
        if (isRgbColorLiteral(match[0])) {
          offenders.push(`${rel} -> ${match[0]}`)
        }
      }
    }

    expect(offenders, offenders.join("\n")).toEqual([])
  })
})
