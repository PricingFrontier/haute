/** Lints a snippet through the repository's real ESLint config, as the file at `filePath`. */
import { ESLint } from "eslint"

let eslint: ESLint | undefined

/** The messages of every `no-restricted-syntax` selector the config gives the file at `filePath`. */
export async function restrictedSelectorMessages(filePath: string): Promise<string[]> {
  eslint ??= new ESLint({ cwd: process.cwd() })
  const config = (await eslint.calculateConfigForFile(filePath)) as { rules?: Record<string, unknown[]> }
  const [, ...selectors] = config.rules?.["no-restricted-syntax"] ?? []
  return (selectors as { message: string }[]).map((entry) => entry.message)
}

/** The `no-restricted-syntax` messages the config raises for `code`. */
export async function restrictedMessages(code: string, filePath = "src/panels/probe.ts"): Promise<string[]> {
  eslint ??= new ESLint({ cwd: process.cwd() })
  const [result] = await eslint.lintText(code, { filePath })
  // A probe that does not parse would otherwise read as one nothing objects to.
  const unparsed = result.messages.find((message) => message.fatal)
  if (unparsed) throw new Error(`The probe does not parse: ${unparsed.message}`)
  return result.messages
    .filter((message) => message.ruleId === "no-restricted-syntax")
    .map((message) => message.message)
}
