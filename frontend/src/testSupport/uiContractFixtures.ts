import { readFileSync } from "node:fs"
import { dirname, resolve } from "node:path"
import { fileURLToPath } from "node:url"

const THIS_DIR = dirname(fileURLToPath(import.meta.url))
const REPO_ROOT = resolve(THIS_DIR, "..", "..", "..")
const UI_CONTRACT_FIXTURES_DIR = resolve(REPO_ROOT, "tests", "fixtures", "ui_contracts")

export function loadUiContractFixture<T>(name: string): T {
  return JSON.parse(
    readFileSync(resolve(UI_CONTRACT_FIXTURES_DIR, `${name}.json`), "utf8"),
  ) as T
}
