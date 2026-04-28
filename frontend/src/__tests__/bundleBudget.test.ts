import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import path from "node:path"
import { describe, expect, it } from "vitest"

import * as checker from "../../scripts/check-bundle-size.mjs"

describe("bundle size checker initial JS budget", () => {
  it("parses module entry and modulepreload JavaScript assets from startup HTML", () => {
    expect(checker.parseInitialJsAssetNames(`
      <script type="module" crossorigin src="/assets/index-abc.js"></script>
      <link rel="modulepreload" crossorigin href="/assets/vendor-react-def.js">
      <link rel="modulepreload" crossorigin href="/assets/vendor-ui-ghi.js">
      <link rel="stylesheet" crossorigin href="/assets/index.css">
    `)).toEqual(["index-abc.js", "vendor-react-def.js", "vendor-ui-ghi.js"])
  })

  it("counts only startup entry and modulepreload JS against the initial budget", () => {
    const result = checker.evaluateBundleBudgets({
      html: `
        <script type="module" src="/assets/index.js"></script>
        <link rel="modulepreload" href="/assets/vendor-react.js">
      `,
      jsAssets: [
        { name: "index.js", rawBytes: 100, gzipBytes: 75 * 1024 },
        { name: "vendor-react.js", rawBytes: 100, gzipBytes: 120 * 1024 },
        { name: "CodeMirrorEditor.js", rawBytes: 100, gzipBytes: 180 * 1024 },
        { name: "DataSourceEditor.js", rawBytes: 100, gzipBytes: 40 * 1024 },
      ],
      budgetsKiB: {
        maxInitialJsGzipKiB: 230,
        maxTotalJsGzipKiB: 500,
        maxSingleJsGzipKiB: 200,
      },
    })

    expect(result.initialGzipBytes).toBe(195 * 1024)
    expect(result.initialAssets.map((asset) => asset.name)).toEqual(["index.js", "vendor-react.js"])
    expect(result.failures).toEqual([])
  })

  it("fails when initial entry and preload JavaScript exceed the configured budget", () => {
    const result = checker.evaluateBundleBudgets({
      html: `
        <script type="module" src="/assets/index.js"></script>
        <link rel="modulepreload" href="/assets/vendor-react.js">
      `,
      jsAssets: [
        { name: "index.js", rawBytes: 100, gzipBytes: 80 * 1024 },
        { name: "vendor-react.js", rawBytes: 100, gzipBytes: 125 * 1024 },
      ],
      budgetsKiB: {
        maxInitialJsGzipKiB: 200,
        maxTotalJsGzipKiB: 500,
        maxSingleJsGzipKiB: 200,
      },
    })

    expect(result.failures).toContain(
      "Initial JS gzip size 205.0 KiB exceeds budget 200 KiB.",
    )
  })

  it("fails when a lazy-only chunk is modulepreloaded from startup HTML", () => {
    const result = checker.evaluateBundleBudgets({
      html: `
        <script type="module" src="/assets/index.js"></script>
        <link rel="modulepreload" href="/assets/vendor-react.js">
        <link rel="modulepreload" href="/assets/CodeMirrorEditor-abc.js">
        <link rel="modulepreload" href="/assets/vendor-layout-def.js">
      `,
      jsAssets: [
        { name: "index.js", rawBytes: 100, gzipBytes: 50 * 1024 },
        { name: "vendor-react.js", rawBytes: 100, gzipBytes: 100 * 1024 },
        { name: "CodeMirrorEditor-abc.js", rawBytes: 100, gzipBytes: 10 * 1024 },
        { name: "vendor-layout-def.js", rawBytes: 100, gzipBytes: 20 * 1024 },
      ],
      budgetsKiB: {
        maxInitialJsGzipKiB: 240,
        maxTotalJsGzipKiB: 500,
        maxSingleJsGzipKiB: 200,
      },
    })

    expect(result.failures).toContain(
      'Lazy-only JS chunk "CodeMirrorEditor-abc.js" must not be modulepreloaded by index.html.',
    )
    expect(result.failures).toContain(
      'Lazy-only JS chunk "vendor-layout-def.js" must not be modulepreloaded by index.html.',
    )
  })

  it("allows a lazy-only chunk to exist when it is not modulepreloaded", () => {
    const result = checker.evaluateBundleBudgets({
      html: `
        <script type="module" src="/assets/index.js"></script>
        <link rel="modulepreload" href="/assets/vendor-react.js">
      `,
      jsAssets: [
        { name: "index.js", rawBytes: 100, gzipBytes: 50 * 1024 },
        { name: "vendor-react.js", rawBytes: 100, gzipBytes: 100 * 1024 },
        { name: "CodeMirrorEditor-abc.js", rawBytes: 100, gzipBytes: 10 * 1024 },
      ],
      budgetsKiB: {
        maxInitialJsGzipKiB: 240,
        maxTotalJsGzipKiB: 500,
        maxSingleJsGzipKiB: 200,
      },
    })

    expect(result.initialAssets.map((asset) => asset.name)).toEqual(["index.js", "vendor-react.js"])
    expect(result.failures).toEqual([])
  })

  it("fails loudly when startup HTML references a missing initial JavaScript asset", () => {
    expect(() =>
      checker.evaluateBundleBudgets({
        html: '<script type="module" src="/assets/index-missing.js"></script>',
        jsAssets: [{ name: "other.js", rawBytes: 100, gzipBytes: 10 * 1024 }],
        budgetsKiB: {
          maxInitialJsGzipKiB: 230,
          maxTotalJsGzipKiB: 500,
          maxSingleJsGzipKiB: 200,
        },
      }),
    ).toThrow('Initial JS asset "index-missing.js" referenced by index.html was not found.')
  })

  it("reports a build precondition when the bundle assets directory is missing", () => {
    const tempDir = mkdtempSync(path.join(tmpdir(), "haute-bundle-budget-"))
    try {
      const staticDir = path.join(tempDir, "static")
      const assetsDir = path.join(staticDir, "assets")
      const indexHtmlPath = path.join(staticDir, "index.html")
      mkdirSync(staticDir)
      writeFileSync(indexHtmlPath, '<script type="module" src="/assets/index.js"></script>')

      expect(
        checker.formatBundleAssetReadError(
          Object.assign(new Error("ENOENT: no such file or directory"), { code: "ENOENT" }),
          { staticDir, assetsDir, indexHtmlPath },
        ),
      ).toBe(`Bundle assets directory not found at ${assetsDir}. Run 'npm run build' first.`)
    } finally {
      rmSync(tempDir, { recursive: true, force: true })
    }
  })

  it("reports non-missing-file bundle read failures without misdiagnosing them as stale builds", () => {
    const message = checker.formatBundleAssetReadError(
      Object.assign(new Error("EACCES: permission denied, scandir 'assets'"), { code: "EACCES" }),
      {
        staticDir: "/tmp/haute/static",
        assetsDir: "/tmp/haute/static/assets",
        indexHtmlPath: "/tmp/haute/static/index.html",
      },
    )

    expect(message).toContain("Failed to read bundle assets under /tmp/haute/static")
    expect(message).toContain("permission denied")
    expect(message).not.toContain("npm run build")
  })
})
