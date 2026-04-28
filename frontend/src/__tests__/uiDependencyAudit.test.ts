import { describe, expect, it } from "vitest"

import * as audit from "../../scripts/check-ui-dependencies.mjs"

describe("UI dependency audit", () => {
  it("allows named lucide-react icon imports", () => {
    const result = audit.auditUiDependencyImports([
      {
        path: "src/components/Toolbar.tsx",
        source: 'import { Loader2, GitFork } from "lucide-react"\n',
      },
    ])

    expect(result.failures).toEqual([])
  })

  it("fails namespace and default lucide-react imports", () => {
    const result = audit.auditUiDependencyImports([
      {
        path: "src/components/Bad.tsx",
        source: [
          'import * as Icons from "lucide-react"',
          'import Lucide from "lucide-react"',
        ].join("\n"),
      },
    ])

    expect(result.failures).toEqual([
      'src/components/Bad.tsx imports lucide-react as a namespace. Use named icon imports so unused icons stay tree-shaken.',
      'src/components/Bad.tsx imports lucide-react as a default import. Use named icon imports so unused icons stay tree-shaken.',
    ])
  })

  it("fails lucide-react side-effect, deep, re-export, and dynamic imports", () => {
    const result = audit.auditUiDependencyImports([
      {
        path: "src/components/Bad.tsx",
        source: [
          'import "lucide-react"',
          'import { Search } from "lucide-react/dist/esm/icons/search"',
          'export * from "lucide-react"',
          'await import("lucide-react")',
        ].join("\n"),
      },
    ])

    expect(result.failures).toEqual([
      "src/components/Bad.tsx imports lucide-react for side effects. Use named icon imports so unused icons stay tree-shaken.",
      'src/components/Bad.tsx imports lucide-react deep path "lucide-react/dist/esm/icons/search". Use named imports from "lucide-react" so icons stay in the audited vendor-ui chunk.',
      "src/components/Bad.tsx uses a runtime export from lucide-react. Import named icons directly at the usage site.",
      "src/components/Bad.tsx uses a runtime dynamic import from lucide-react. Import named icons directly at the usage site.",
    ])
  })

  it("fails broad UI package runtime imports", () => {
    const result = audit.auditUiDependencyImports([
      {
        path: "src/panels/BadPanel.tsx",
        source: 'import { Button } from "@mui/material"\n',
      },
    ])

    expect(result.failures).toEqual([
      'src/panels/BadPanel.tsx imports broad UI package "@mui/material". Add an explicit bundle plan before introducing another UI vendor.',
    ])
  })

  it("allows type-only imports from broad UI packages", () => {
    const result = audit.auditUiDependencyImports([
      {
        path: "src/types/ui.ts",
        source: 'import type { ButtonProps } from "@mui/material"\n',
      },
    ])

    expect(result.failures).toEqual([])
  })

  it("fails when vendor-ui gzip exceeds the configured cap", () => {
    const result = audit.evaluateVendorUiBudget({
      jsAssets: [
        { name: "index.js", gzipBytes: 100 * 1024 },
        { name: "vendor-ui-abc.js", gzipBytes: 21 * 1024 },
      ],
      maxVendorUiGzipKiB: 20,
    })

    expect(result.failures).toEqual([
      "vendor-ui gzip size 21.0 KiB exceeds budget 20 KiB.",
    ])
  })

  it("fails loudly when a built bundle has no vendor-ui chunk", () => {
    expect(() =>
      audit.evaluateVendorUiBudget({
        jsAssets: [{ name: "index.js", gzipBytes: 100 * 1024 }],
        maxVendorUiGzipKiB: 20,
      }),
    ).toThrow('No "vendor-ui" JavaScript chunk found in built assets.')
  })
})
