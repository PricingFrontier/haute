/**
 * index.css as Tailwind compiles it: the app's real stylesheet entry, with the kit's
 * stylesheets it imports and the utilities for `candidates`. For tests that pin what the
 * build emits rather than what the source says.
 */
import { readFileSync } from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"

const SRC = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..")

export async function compileIndexCss(candidates: string[] = []): Promise<string> {
  let compile: typeof import("tailwindcss").compile
  try {
    ;({ compile } = await import("tailwindcss"))
  } catch {
    throw new Error("Cannot import tailwindcss - is frontend/node_modules installed? (npm ci --prefix frontend)")
  }
  const twEntry = path.resolve(SRC, "..", "node_modules", "tailwindcss", "index.css")
  const compiled = await compile(readFileSync(path.join(SRC, "index.css"), "utf8"), {
    base: SRC,
    async loadStylesheet(id: string, base: string) {
      // `@import "tailwindcss"` resolves to the package entry; every other import,
      // the package's internal ones included, resolves relative to the importing file.
      const file = id === "tailwindcss" ? twEntry : path.resolve(base, id)
      return { path: file, base: path.dirname(file), content: readFileSync(file, "utf8") }
    },
  })
  return compiled.build(candidates)
}
