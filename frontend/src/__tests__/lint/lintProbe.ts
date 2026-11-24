/**
 * Lint a probe snippet through the repository's real ESLint config, as if it were the text
 * of `filePath`. The type-aware parser reads only files a tsconfig holds, so a probe of
 * browser source stands in for a real file's text; the file on disk is not read or changed.
 */
import { ESLint, type Linter } from "eslint"
import { expect } from "vitest"

/** A browser source file no ban exempts. */
export const BROWSER_SOURCE = "src/main.tsx"

let eslint: ESLint | undefined

export async function lintProbe(code: string, filePath = BROWSER_SOURCE): Promise<Linter.LintMessage[]> {
  eslint ??= new ESLint({ cwd: process.cwd() })
  const [result] = await eslint.lintText(code, { filePath })
  // A probe the parser could not read proves nothing either way.
  expect(result.messages.filter((message) => message.fatal).map((message) => message.message)).toEqual([])
  return result.messages
}
