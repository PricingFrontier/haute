import { gzipSync } from "node:zlib"
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs"
import path from "node:path"
import { fileURLToPath, pathToFileURL } from "node:url"

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const staticDir = path.resolve(__dirname, "../../src/haute/static")
const assetsDir = path.join(staticDir, "assets")
const indexHtmlPath = path.join(staticDir, "index.html")

// PivotCharts deliberately add a 195.6 KiB gzip ECharts core chunk beneath the
// lazy Charts pane. Pipeline load recovery adds ~10.5 KiB across the exact
// editor-document validator/adapter, recovery state, live-sync fences, and the
// minimal repair contract. The merged bundle is 1,309.4 KiB; 1,320 KiB keeps
// ~10 KiB headroom while the separate chart-vendor cap below prevents that
// dependency consuming it.
const DEFAULT_MAX_TOTAL_JS_GZIP_KIB = 1320
const DEFAULT_MAX_SINGLE_JS_GZIP_KIB = 650
const DEFAULT_MAX_CHART_VENDOR_JS_GZIP_KIB = 205
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
// Git readiness hardening added ~0.3 KiB for the always-visible six-state
// indicator, retryable error state, and in-flight status de-duplication. The
// branch-list request coordinator remains lazy.
// Edge Join compatible-target feedback adds ~0.7 KiB of deliberate eager code:
// the always-mounted canvas must validate and announce candidates synchronously
// during a connection gesture. The merged initial bundle is 246.2 KiB; 247 KiB
// retained sub-KiB headroom while still catching accidental eager editor/vendor
// imports. Editable submodel boundaries add ~1.5 KiB of deliberate eager code:
// the always-mounted canvas projects available and mapped inputs, reconciles
// undo/redo state, and commits boundary connection/deletion gestures
// synchronously. The merged initial bundle is 247.7 KiB; 249 KiB retains about
// 1 KiB of headroom without weakening the lazy-editor/vendor checks below.
// Reusable submodel instances add another ~3.3 KiB of deliberate eager core:
// occurrence-aware navigation, runtime targeting, read-only copies, and shared
// boundary edits must all be available on the mounted canvas. The merged initial
// bundle is 251.3 KiB; 253 KiB retains about 1.7 KiB of headroom.
// Hosted durable storage adds ~1.9 KiB of deliberate eager code: the toolbar
// must state at first paint whether the session's work is being stored, how far
// behind publication is, and whether this project is a fork — a chip that
// appeared late would leave the user believing unsaved work was safe. Its
// dialogs (bind, upstream sync, identity) are all lazy. The merged initial
// bundle is 253.1 KiB; 255 KiB restores about 1.9 KiB of headroom.
// Explore Pivot/PivotChart contracts, retained-result state, and pane dispatch
// bring the merged startup bundle to 256.4 KiB without importing the chart
// runtime. 258 KiB preserves a narrow ~1.6 KiB regression tripwire.
// Excel-parity chart formatting adds ~1.6 KiB of deliberate eager state
// contracts: per-node preview/editor pane alignment and configured-card
// tracking in useUIStore, plus the pivot auto-claim registry in
// useNodeResultsStore that keeps chart sources refreshing atomically. The
// chart gallery, formatting editors, and data adapters all stay in lazy
// chunks. The merged initial bundle is 258.0 KiB; 260 KiB restores about
// 2 KiB of headroom.
// Pipeline load recovery adds ~10.5 KiB of deliberate eager core: every load
// and resync must validate and adapt the editor document, publish diagnostics
// and capabilities atomically, and preserve a truthful degraded canvas. The
// repair dialog itself remains small and the chart/editor vendors remain lazy.
// The merged initial bundle is 268.5 KiB; 271 KiB retains ~2.5 KiB headroom.
const DEFAULT_MAX_INITIAL_JS_GZIP_KIB = 271

// Chunks that should only be fetched after a user opens a code/editor-heavy
// surface. If one appears as a startup modulepreload, the app has likely
// reintroduced an eager import path even if the initial gzip budget still fits.
export const LAZY_ONLY_MODULEPRELOAD_CHUNK_PREFIXES = [
  "CodeMirrorEditor",
  "UtilityPanel",
  "vendor-codemirror",
  "vendor-layout",
  "vendor-charts",
  "chartRuntime",
  "ExploreChartsPane",
  "TransformEditor",
  "ModelScoreEditor",
  "BandingEditor",
  "RatingStepEditor",
  "OutputEditor",
  "ExternalFileEditor",
  "ApiInputEditor",
  "LiveSwitchEditor",
  "ScenarioExpanderEditor",
  "OptimiserApplyEditor",
  "ConstantEditor",
  "SubmodelEditor",
  "ColumnsTab",
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
  "trainGuards",
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

/**
 * @param {{
 *   html: string,
 *   jsAssets: Array<{ name: string, rawBytes: number, gzipBytes: number }>,
 *   budgetsKiB: {
 *     maxInitialJsGzipKiB: number,
 *     maxTotalJsGzipKiB: number,
 *     maxSingleJsGzipKiB: number,
 *     maxChartVendorJsGzipKiB?: number,
 *   },
 * }} input
 */
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
  const chartVendorAsset = jsAssets.find(
    (asset) => asset.name === "vendor-charts.js" || asset.name.startsWith("vendor-charts-"),
  )
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

  if (
    chartVendorAsset &&
    budgetsKiB.maxChartVendorJsGzipKiB != null &&
    chartVendorAsset.gzipBytes > budgetsKiB.maxChartVendorJsGzipKiB * 1024
  ) {
    failures.push(
      `Chart vendor JS chunk ${chartVendorAsset.name} is ${formatKiB(chartVendorAsset.gzipBytes)}, ` +
        `above budget ${budgetsKiB.maxChartVendorJsGzipKiB} KiB.`,
    )
  }

  return {
    failures,
    initialAssets,
    initialGzipBytes,
    chartVendorAsset,
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
    maxChartVendorJsGzipKiB: parseBudgetKiB(
      "HAUTE_BUNDLE_MAX_CHART_VENDOR_GZIP_KIB",
      DEFAULT_MAX_CHART_VENDOR_JS_GZIP_KIB,
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
  if (result.chartVendorAsset) {
    console.log(
      `  chart vendor: ${formatKiB(result.chartVendorAsset.gzipBytes)} gzip ` +
        `(budget ${budgetsKiB.maxChartVendorJsGzipKiB} KiB)`,
    )
  }
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
