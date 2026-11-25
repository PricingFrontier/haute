/**
 * Lint a probe snippet through the repository's real ESLint config, as if it were the text
 * of `filePath`. The type-aware parser reads only files a tsconfig holds, so a probe of
 * browser source stands in for a real file's text; the file on disk is not read or changed.
 */
import { ESLint, type Linter } from "eslint"
import { expect } from "vitest"

/** A browser source file no ban exempts. */
export const BROWSER_SOURCE = "src/main.tsx"

/** The rules that need the type-aware parser, which builds the whole program first. */
const TYPED_RULES = ["@typescript-eslint/no-floating-promises", "@typescript-eslint/no-misused-promises"]

const linters = new Map<boolean, ESLint>()

function linter(types: boolean): ESLint {
  let eslint = linters.get(types)
  if (eslint === undefined) {
    // A probe of the syntax bans needs no types: the config's own rules, parsed untyped.
    eslint = new ESLint({
      cwd: process.cwd(),
      ...(types ? {} : {
        overrideConfig: [{
          files: ["**/*.{ts,tsx}"],
          languageOptions: { parserOptions: { projectService: false } },
          rules: Object.fromEntries(TYPED_RULES.map((rule) => [rule, "off"])),
        }],
      }),
    })
    linters.set(types, eslint)
  }
  return eslint
}

export async function lintProbe(
  code: string,
  filePath = BROWSER_SOURCE,
  { types = false }: { types?: boolean } = {},
): Promise<Linter.LintMessage[]> {
  const [result] = await linter(types).lintText(code, { filePath })
  // A probe the parser could not read proves nothing either way.
  expect(result.messages.filter((message) => message.fatal).map((message) => message.message)).toEqual([])
  return result.messages
}
