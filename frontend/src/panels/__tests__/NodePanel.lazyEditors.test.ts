import { describe, expect, it } from "vitest"
import { readFileSync } from "node:fs"
import path from "node:path"

describe("NodePanel lazy editor loading", () => {
  it("does not statically import node editor bodies through the editor barrel", () => {
    const source = readFileSync(path.resolve(__dirname, "..", "NodePanel.tsx"), "utf8")
    const runtimeEditorBarrelImports = (source.match(/^import[\s\S]*?from\s*["'][^"']+["']/gm) ?? [])
      .filter((declaration) =>
        !declaration.startsWith("import type ") &&
        declaration.includes('from "./editors"'),
      )
    const runtimeEditorBodyImports = (source.match(/^import[\s\S]*?from\s*["'][^"']+["']/gm) ?? [])
      .filter((declaration) =>
        !declaration.startsWith("import type ") &&
        declaration.includes('from "./editors/'),
      )

    expect(runtimeEditorBarrelImports).toEqual([])
    expect(runtimeEditorBodyImports).toEqual([])
    expect(source).not.toMatch(/import\s+ModellingConfig\s+from\s*["']\.\/ModellingConfig["']/)
    expect(source).not.toMatch(/import\s+OptimiserConfig\s+from\s*["']\.\/OptimiserConfig["']/)
  })

  it("keeps App-mounted utility panels off the editor barrel runtime path", () => {
    for (const panel of ["UtilityPanel.tsx", "ImportsPanel.tsx"]) {
      const source = readFileSync(path.resolve(__dirname, "..", panel), "utf8")

      expect(source).not.toMatch(/from\s*["']\.\/editors["']/)
      expect(source).toContain('from "./editors/CodeEditor"')
    }
  })

  it("keeps editor bodies behind dynamic import boundaries", () => {
    const source = readFileSync(path.resolve(__dirname, "..", "LazyNodeEditors.tsx"), "utf8")

    for (const importPath of [
      "./editors/DataInputEditor",
      "./editors/TransformEditor",
      "./editors/EdgeJoinEditor",
      "./editors/ModelScoreEditor",
      "./editors/BandingEditor",
      "./editors/RatingStepEditor",
      "./editors/OutputEditor",
      "./editors/ExternalFileEditor",
      "./editors/ApiInputEditor",
      "./editors/LiveSwitchEditor",
      "./editors/DataOutputEditor",
      "./editors/ScenarioExpanderEditor",
      "./editors/OptimiserApplyEditor",
      "./editors/ConstantEditor",
      "./editors/SubmodelEditor",
      "./editors/ColumnsTab",
      "./ModellingConfig",
      "./OptimiserConfig",
    ]) {
      expect(source).toContain(`import("${importPath}")`)
    }
  })
})
