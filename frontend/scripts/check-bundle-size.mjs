import { gzipSync } from "node:zlib"
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs"
import path from "node:path"
import { fileURLToPath, pathToFileURL } from "node:url"

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const staticDir = path.resolve(__dirname, "../../src/haute/static")
const assetsDir = path.join(staticDir, "assets")
const indexHtmlPath = path.join(staticDir, "index.html")

const DEFAULT_MAX_TOTAL_JS_GZIP_KIB = 1100
const DEFAULT_MAX_SINGLE_JS_GZIP_KIB = 650
// Initial JS is ~240 KiB gzip after the version-control feature merged in. All
// on-demand VC surfaces (comparison view, git panel, the VC modals) are
// React.lazy code-split, so the remaining weight is genuine non-splittable
// initial-path growth (VC api/types/guards/store + multi-frame core). The
// data-io feature added ~0.7 KiB of the same class (fetchIoFormats client
// function + its runtime guard; the editors themselves are lazy chunks).
// The Explore NaN split added ~0.5 KiB of the same class again (nan_count in
// types/guards, the NaN %/NaN columns, and the shared Tooltip + HelpCircle
// icon newly reaching the initial chunk via the Distinct help button).
// The assistant feature added ~0.3 KiB of the same deliberate-eager class: the
// always-visible Toolbar toggle (Bot icon + button + useUIStore selectors)
// and client.ts's postRawStream helper. The panel, store, api module, and
// markdown renderer are all lazy (guarded by App.assistantLazy.test.ts).
// 245 KiB keeps a small margin while still catching accidental eager
// editor/vendor imports.
const DEFAULT_MAX_INITIAL_JS_GZIP_KIB = 245

// Chunks that should only be fetched after a user opens a code/editor-heavy
// surface. If one appears as a startup modulepreload, the app has likely
// reintroduced an eager import path even if the initial gzip budget still fits.
export const LAZY_ONLY_MODULEPRELOAD_CHUNK_PREFIXES = [
  "CodeMirrorEditor",
  "vendor-codemirror",
  "vendor-layout",
  "DataSourceEditor",
  "TransformEditor",
  "ModelScoreEditor",
  "BandingEditor",
  "RatingStepEditor",
  "OutputEditor",
  "ExternalFileEditor",
  "ApiInputEditor",
  "LiveSwitchEditor",
  "SinkEditor",
  "ScenarioExpanderEditor",
  "OptimiserApplyEditor",
  "ConstantEditor",
  "SubmodelEditor",
  "ColumnsTab",
  "GroupedColumnsTab",
  "ModellingConfig",
  "OptimiserConfig",
  "_shared",
  "useMlflowBrowser",
  "useStaleConfigEstimate",
  "CacheFetchButton",
  "ColumnTable",
  "ToggleButtonGroup",
  "EditorLabel",
  "banding",
]

function fail(message) {
  console.error(message)
  process.exitCode = 1
}

function errorMessage(error) {
  return error instanceof Error ? error.message : String(error)
}

function errorCode(error) {
  return error && typeof error === "object" && "code" in error ? error.code : undefined
}

export function formatBundleAssetReadError(
  error,
  {
    staticDir: staticDirPath = staticDir,
    assetsDir: assetsDirPath = assetsDir,
    indexHtmlPath: indexHtmlFilePath = indexHtmlPath,
  } = {},
) {
  const message = errorMessage(error)
  if (errorCode(error) === "ENOENT") {
    if (!existsSync(staticDirPath)) {
      return `Bundle output directory not found at ${staticDirPath}. Run 'npm run build' first.`
    }
    if (!existsSync(assetsDirPath)) {
      return `Bundle assets directory not found at ${assetsDirPath}. Run 'npm run build' first.`
    }
    if (!existsSync(indexHtmlFilePath)) {
      return `Bundle index HTML not found at ${indexHtmlFilePath}. Run 'npm run build' first.`
    }
    return `Bundle file referenced by the bundle check was not found under ${staticDirPath}: ${message}`
  }

  return `Failed to read bundle assets under ${staticDirPath}: ${message}`
}

export function formatKiB(bytes) {
  return `${(bytes / 1024).toFixed(1)} KiB`
}

function parseBudgetKiB(envName, defaultValue) {
  const raw = process.env[envName]
  if (raw == null || raw === "") return defaultValue
  const parsed = Number(raw)
  if (!Number.isFinite(parsed) || parsed <= 0) {
    throw new Error(`${envName} must be a positive number of KiB, got ${JSON.stringify(raw)}.`)
  }
  return parsed
}

function readAttributeMap(tag) {
  const attrs = new Map()
  for (const match of tag.matchAll(/([\w:-]+)\s*=\s*(["'])(.*?)\2/g)) {
    attrs.set(match[1].toLowerCase(), match[3])
  }
  return attrs
}

function assetNameFromReference(reference) {
  const withoutQuery = reference.split(/[?#]/, 1)[0]
  const assetMarker = "/assets/"
  const assetIndex = withoutQuery.lastIndexOf(assetMarker)
  if (assetIndex >= 0) return withoutQuery.slice(assetIndex + assetMarker.length)
  if (withoutQuery.startsWith("assets/")) return withoutQuery.slice("assets/".length)
  return path.posix.basename(withoutQuery)
}

function pushUnique(array, value) {
  if (!array.includes(value)) array.push(value)
}

export function parseInitialJsAssetNames(html) {
  const initial = []

  for (const match of html.matchAll(/<script\b[^>]*>/gi)) {
    const attrs = readAttributeMap(match[0])
    if (attrs.get("type") !== "module") continue
    const src = attrs.get("src")
    if (src && assetNameFromReference(src).endsWith(".js")) {
      pushUnique(initial, assetNameFromReference(src))
    }
  }

  for (const match of html.matchAll(/<link\b[^>]*>/gi)) {
    const attrs = readAttributeMap(match[0])
    if (attrs.get("rel") !== "modulepreload") continue
    const href = attrs.get("href")
    if (href && assetNameFromReference(href).endsWith(".js")) {
      pushUnique(initial, assetNameFromReference(href))
    }
  }

  return initial
}

export function parseModulepreloadJsAssetNames(html) {
  const preloads = []

  for (const match of html.matchAll(/<link\b[^>]*>/gi)) {
    const attrs = readAttributeMap(match[0])
    if (attrs.get("rel") !== "modulepreload") continue
    const href = attrs.get("href")
    if (href && assetNameFromReference(href).endsWith(".js")) {
      pushUnique(preloads, assetNameFromReference(href))
    }
  }

  return preloads
}

function isLazyOnlyModulepreloadChunk(assetName) {
  return LAZY_ONLY_MODULEPRELOAD_CHUNK_PREFIXES.some((prefix) =>
    assetName === `${prefix}.js` || assetName.startsWith(`${prefix}-`),
  )
}

export function collectJsAssets(directory) {
  return readdirSync(directory)
    .filter((name) => name.endsWith(".js"))
    .map((name) => {
      const filePath = path.join(directory, name)
      const content = readFileSync(filePath)
      return {
        name,
        rawBytes: statSync(filePath).size,
        gzipBytes: gzipSync(content).length,
      }
    })
    .sort((a, b) => b.gzipBytes - a.gzipBytes)
}

export function evaluateBundleBudgets({
  html,
  jsAssets,
  budgetsKiB,
}) {
  if (jsAssets.length === 0) {
    throw new Error("No JavaScript assets found.")
  }

  const initialAssetNames = parseInitialJsAssetNames(html)
  if (initialAssetNames.length === 0) {
    throw new Error("No initial JavaScript assets found in index.html.")
  }

  const assetByName = new Map(jsAssets.map((asset) => [asset.name, asset]))
  const initialAssets = initialAssetNames.map((name) => {
    const asset = assetByName.get(name)
    if (!asset) {
      throw new Error(`Initial JS asset "${name}" referenced by index.html was not found.`)
    }
    return asset
  })

  const totalGzipBytes = jsAssets.reduce((sum, asset) => sum + asset.gzipBytes, 0)
  const initialGzipBytes = initialAssets.reduce((sum, asset) => sum + asset.gzipBytes, 0)
  const largest = jsAssets[0]
  const failures = []

  for (const assetName of parseModulepreloadJsAssetNames(html)) {
    if (isLazyOnlyModulepreloadChunk(assetName)) {
      failures.push(
        `Lazy-only JS chunk "${assetName}" must not be modulepreloaded by index.html.`,
      )
    }
  }

  if (initialGzipBytes > budgetsKiB.maxInitialJsGzipKiB * 1024) {
    failures.push(
      `Initial JS gzip size ${formatKiB(initialGzipBytes)} exceeds budget ` +
        `${budgetsKiB.maxInitialJsGzipKiB} KiB.`,
    )
  }

  if (totalGzipBytes > budgetsKiB.maxTotalJsGzipKiB * 1024) {
    failures.push(
      `Total JS gzip size ${formatKiB(totalGzipBytes)} exceeds budget ` +
        `${budgetsKiB.maxTotalJsGzipKiB} KiB.`,
    )
  }

  if (largest.gzipBytes > budgetsKiB.maxSingleJsGzipKiB * 1024) {
    failures.push(
      `Largest JS chunk ${largest.name} is ${formatKiB(largest.gzipBytes)}, ` +
        `above budget ${budgetsKiB.maxSingleJsGzipKiB} KiB.`,
    )
  }

  return {
    failures,
    initialAssets,
    initialGzipBytes,
    jsAssets,
    largest,
    totalGzipBytes,
  }
}

function run() {
  const budgetsKiB = {
    maxInitialJsGzipKiB: parseBudgetKiB(
      "HAUTE_BUNDLE_MAX_INITIAL_GZIP_KIB",
      DEFAULT_MAX_INITIAL_JS_GZIP_KIB,
    ),
    maxTotalJsGzipKiB: parseBudgetKiB(
      "HAUTE_BUNDLE_MAX_TOTAL_GZIP_KIB",
      DEFAULT_MAX_TOTAL_JS_GZIP_KIB,
    ),
    maxSingleJsGzipKiB: parseBudgetKiB(
      "HAUTE_BUNDLE_MAX_SINGLE_GZIP_KIB",
      DEFAULT_MAX_SINGLE_JS_GZIP_KIB,
    ),
  }

  let jsAssets
  let html
  try {
    jsAssets = collectJsAssets(assetsDir)
    html = readFileSync(indexHtmlPath, "utf8")
  } catch (error) {
    fail(formatBundleAssetReadError(error))
    return
  }

  let result
  try {
    result = evaluateBundleBudgets({ html, jsAssets, budgetsKiB })
  } catch (error) {
    fail(error instanceof Error ? error.message : String(error))
    return
  }

  console.log("JavaScript bundle gzip sizes:")
  for (const asset of result.jsAssets) {
    console.log(`  ${asset.name}: ${formatKiB(asset.gzipBytes)} gzip (${formatKiB(asset.rawBytes)} raw)`)
  }
  console.log(`  total: ${formatKiB(result.totalGzipBytes)} gzip`)
  console.log("")
  console.log("Initial JavaScript gzip sizes:")
  for (const asset of result.initialAssets) {
    console.log(`  ${asset.name}: ${formatKiB(asset.gzipBytes)} gzip`)
  }
  console.log(
    `  initial total: ${formatKiB(result.initialGzipBytes)} gzip ` +
      `(budget ${budgetsKiB.maxInitialJsGzipKiB} KiB)`,
  )

  for (const message of result.failures) {
    fail(message)
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  run()
}
