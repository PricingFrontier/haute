/** Lints a snippet through the repository's real ESLint config, as the file at `filePath`. */
import { ESLint } from "eslint"

let eslint: ESLint | undefined

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
