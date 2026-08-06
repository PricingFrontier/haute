/**
 * Meta-test: the Node webstorage pin must stay in vitest.config.ts.
 *
 * CI runs a Node where the experimental web-storage globals are off by
 * default, so deleting `--no-experimental-webstorage` from test.execArgv
 * keeps CI green while the protection silently vanishes for every dev on
 * Node >= 25 (where the runtime canary in setupStorageCanary.ts is the
 * only other line of defence — and it never runs on CI's Node). This test
 * makes the pin's PRESENCE a CI-gated fact, mirroring the repo pattern of
 * asserting on config/workflow content (tests/test_performance_docs.py).
 * Asserts on the config source text: importing the config module here
 * would pull `vitest/config` (and esbuild) into the jsdom environment,
 * which esbuild refuses.
 */

import { readFileSync } from "node:fs"
import { join } from "node:path"

import { describe, expect, it } from "vitest"

// Vitest workers run with cwd at the project root (frontend/); import.meta.url
// is an http: URL under the jsdom transform, so resolve from cwd instead.
const configText = readFileSync(join(process.cwd(), "vitest.config.ts"), "utf8")

describe("webstorage pin meta-test", () => {
  it("keeps --no-experimental-webstorage in test.execArgv", () => {
    expect(configText).toMatch(
      /execArgv:\s*\[[^\]]*"--no-experimental-webstorage"/,
    )
  })

  it("runs the storage canary first in setupFiles", () => {
    expect(configText).toMatch(
      /setupFiles:\s*\[\s*"\.\/src\/setupStorageCanary\.ts"/,
    )
  })
})
