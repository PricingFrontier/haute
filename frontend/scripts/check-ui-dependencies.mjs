import { readdirSync, readFileSync, statSync } from "node:fs"
import path from "node:path"
import { fileURLToPath, pathToFileURL } from "node:url"
import { gzipSync } from "node:zlib"
import { parse } from "@babel/parser"

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const frontendDir = path.resolve(__dirname, "..")
const defaultSrcDir = path.join(frontendDir, "src")
const defaultAssetsDir = path.resolve(frontendDir, "../src/haute/static/assets")
const DEFAULT_MAX_VENDOR_UI_GZIP_KIB = 20

const SOURCE_EXTENSIONS = new Set([".ts", ".tsx"])

// Keep the current UI vendor surface intentionally small: lucide icon modules
// live in vendor-ui, while broader component libraries need an explicit bundle
// plan before they are introduced.
const BROAD_UI_PACKAGE_IMPORTS = [
  "@mui",
  "@chakra-ui",
  "@mantine",
  "@blueprintjs",
  "antd",
  "semantic-ui-react",
  "react-bootstrap",
  "reactstrap",
  "primereact",
]

function formatKiB(bytes) {
  return `${(bytes / 1024).toFixed(1)} KiB`
}

function parsePositiveNumberEnv(envName, defaultValue) {
  const raw = process.env[envName]
  if (raw == null || raw === "") return defaultValue
  const parsed = Number(raw)
  if (!Number.isFinite(parsed) || parsed <= 0) {
    throw new Error(`${envName} must be a positive number of KiB, got ${JSON.stringify(raw)}.`)
  }
  return parsed
}

function isSourceFile(filePath) {
  return SOURCE_EXTENSIONS.has(path.extname(filePath))
}

function isRuntimeSourceFile(filePath) {
  const normalized = filePath.replaceAll(path.sep, "/")
  const basename = path.basename(filePath)
  return (
    isSourceFile(filePath) &&
    !normalized.includes("/__tests__/") &&
    !normalized.includes("/test-utils/") &&
    !basename.endsWith(".test.ts") &&
    !basename.endsWith(".test.tsx") &&
    basename !== "setupTests.ts"
  )
}

function collectSourceFiles(directory) {
  const files = []
  const stack = [directory]

  while (stack.length > 0) {
    const current = stack.pop()
    for (const entry of readdirSync(current, { withFileTypes: true })) {
      const entryPath = path.join(current, entry.name)
      if (entry.isDirectory()) {
        if (entry.name === "node_modules") continue
        stack.push(entryPath)
      } else if (entry.isFile() && isRuntimeSourceFile(entryPath)) {
        files.push({
          path: path.relative(frontendDir, entryPath).replaceAll(path.sep, "/"),
          source: readFileSync(entryPath, "utf8"),
        })
      }
    }
  }

  return files.sort((a, b) => a.path.localeCompare(b.path))
}

function collectJsAssets(directory) {
  return readdirSync(directory)
    .filter((name) => name.endsWith(".js"))
    .map((name) => {
      const filePath = path.join(directory, name)
      return {
        name,
        rawBytes: statSync(filePath).size,
        gzipBytes: gzipSync(readFileSync(filePath)).length,
      }
    })
    .sort((a, b) => b.gzipBytes - a.gzipBytes || a.name.localeCompare(b.name))
}

function parseAst(file) {
  let ast
  try {
    ast = parse(file.source, {
      sourceType: "module",
      plugins: ["typescript", "jsx"],
    })
  } catch (error) {
    throw new Error(`Failed to parse ${file.path}: ${error instanceof Error ? error.message : String(error)}`)
  }

  return ast
}

function isTypeOnlyImport(declaration) {
  return (
    declaration.importKind === "type" ||
    (
      declaration.specifiers.length > 0 &&
      declaration.specifiers.every((specifier) => specifier.importKind === "type")
    )
  )
}

function isTypeOnlyExport(declaration) {
  return declaration.exportKind === "type"
}

function walkAst(node, visit) {
  if (!node || typeof node !== "object") return
  if (Array.isArray(node)) {
    for (const child of node) walkAst(child, visit)
    return
  }

  if (typeof node.type === "string") visit(node)

  for (const [key, value] of Object.entries(node)) {
    if (
      key === "loc" ||
      key === "start" ||
      key === "end" ||
      key === "leadingComments" ||
      key === "trailingComments" ||
      key === "innerComments"
    ) {
      continue
    }
    walkAst(value, visit)
  }
}

function collectRuntimeDependencyReferences(file) {
  const ast = parseAst(file)
  const references = []

  for (const node of ast.program.body) {
    if (node.type === "ImportDeclaration") {
      if (isTypeOnlyImport(node)) continue
      references.push({
        kind: "import",
        source: node.source.value,
        specifiers: node.specifiers,
      })
    } else if (
      (node.type === "ExportNamedDeclaration" || node.type === "ExportAllDeclaration") &&
      node.source &&
      !isTypeOnlyExport(node)
    ) {
      references.push({
        kind: "export",
        source: node.source.value,
        specifiers: node.specifiers ?? [],
      })
    }
  }

  walkAst(ast.program, (node) => {
    if (
      node.type === "CallExpression" &&
      node.callee.type === "Import" &&
      node.arguments[0]?.type === "StringLiteral"
    ) {
      references.push({
        kind: "dynamic import",
        source: node.arguments[0].value,
        specifiers: [],
      })
    }
    if (
      node.type === "ImportExpression" &&
      node.source.type === "StringLiteral"
    ) {
      references.push({
        kind: "dynamic import",
        source: node.source.value,
        specifiers: [],
      })
    }
  })

  return references
}

function isBroadUiPackage(source) {
  return BROAD_UI_PACKAGE_IMPORTS.some((pkg) => source === pkg || source.startsWith(`${pkg}/`))
}

export function auditUiDependencyImports(files) {
  const failures = []

  for (const file of files) {
    for (const reference of collectRuntimeDependencyReferences(file)) {
      const importedFrom = reference.source
      if (importedFrom === "lucide-react") {
        if (reference.kind !== "import") {
          failures.push(
            `${file.path} uses a runtime ${reference.kind} from lucide-react. ` +
              "Import named icons directly at the usage site.",
          )
          continue
        }
        if (reference.specifiers.length === 0) {
          failures.push(
            `${file.path} imports lucide-react for side effects. ` +
              "Use named icon imports so unused icons stay tree-shaken.",
          )
        }
        for (const specifier of reference.specifiers) {
          if (specifier.type === "ImportNamespaceSpecifier") {
            failures.push(
              `${file.path} imports lucide-react as a namespace. ` +
                "Use named icon imports so unused icons stay tree-shaken.",
            )
          }
          if (specifier.type === "ImportDefaultSpecifier") {
            failures.push(
              `${file.path} imports lucide-react as a default import. ` +
                "Use named icon imports so unused icons stay tree-shaken.",
            )
          }
        }
      } else if (importedFrom.startsWith("lucide-react/")) {
        failures.push(
          `${file.path} imports lucide-react deep path "${importedFrom}". ` +
            'Use named imports from "lucide-react" so icons stay in the audited vendor-ui chunk.',
        )
      } else if (isBroadUiPackage(importedFrom)) {
        failures.push(
          `${file.path} imports broad UI package "${importedFrom}". ` +
            "Add an explicit bundle plan before introducing another UI vendor.",
        )
      }
    }
  }

  return { failures }
}

export function evaluateVendorUiBudget({ jsAssets, maxVendorUiGzipKiB }) {
  const vendorUiAsset = jsAssets.find((asset) =>
    asset.name === "vendor-ui.js" || asset.name.startsWith("vendor-ui-"),
  )
  if (!vendorUiAsset) {
    throw new Error('No "vendor-ui" JavaScript chunk found in built assets.')
  }

  const failures = []
  if (vendorUiAsset.gzipBytes > maxVendorUiGzipKiB * 1024) {
    failures.push(
      `vendor-ui gzip size ${formatKiB(vendorUiAsset.gzipBytes)} exceeds budget ${maxVendorUiGzipKiB} KiB.`,
    )
  }

  return { vendorUiAsset, failures }
}

function run() {
  let failures = []
  try {
    const maxVendorUiGzipKiB = parsePositiveNumberEnv(
      "HAUTE_BUNDLE_MAX_VENDOR_UI_GZIP_KIB",
      DEFAULT_MAX_VENDOR_UI_GZIP_KIB,
    )
    const importAudit = auditUiDependencyImports(collectSourceFiles(defaultSrcDir))
    const bundleAudit = evaluateVendorUiBudget({
      jsAssets: collectJsAssets(defaultAssetsDir),
      maxVendorUiGzipKiB,
    })
    failures = [...importAudit.failures, ...bundleAudit.failures]

    console.log("UI dependency audit:")
    console.log(`  vendor-ui: ${formatKiB(bundleAudit.vendorUiAsset.gzipBytes)} gzip (budget ${maxVendorUiGzipKiB} KiB)`)
  } catch (error) {
    failures = [error instanceof Error ? error.message : String(error)]
  }

  for (const failure of failures) {
    console.error(failure)
  }
  if (failures.length > 0) {
    process.exitCode = 1
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  run()
}
