import { gzipSync } from "node:zlib"
import { readdirSync, readFileSync, statSync } from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const assetsDir = path.resolve(__dirname, "../../src/haute/static/assets")

const maxTotalJsGzipKiB = Number(process.env.HAUTE_BUNDLE_MAX_TOTAL_GZIP_KIB ?? 1100)
const maxSingleJsGzipKiB = Number(process.env.HAUTE_BUNDLE_MAX_SINGLE_GZIP_KIB ?? 650)

function fail(message) {
  console.error(message)
  process.exitCode = 1
}

function formatKiB(bytes) {
  return `${(bytes / 1024).toFixed(1)} KiB`
}

let entries
try {
  entries = readdirSync(assetsDir)
} catch {
  fail(`Bundle assets not found at ${assetsDir}. Run 'npm run build' first.`)
  process.exit()
}

const jsAssets = entries
  .filter((name) => name.endsWith(".js"))
  .map((name) => {
    const filePath = path.join(assetsDir, name)
    const content = readFileSync(filePath)
    return {
      name,
      rawBytes: statSync(filePath).size,
      gzipBytes: gzipSync(content).length,
    }
  })
  .sort((a, b) => b.gzipBytes - a.gzipBytes)

if (jsAssets.length === 0) {
  fail(`No JavaScript assets found in ${assetsDir}.`)
  process.exit()
}

const totalGzipBytes = jsAssets.reduce((sum, asset) => sum + asset.gzipBytes, 0)
const largest = jsAssets[0]

console.log("JavaScript bundle gzip sizes:")
for (const asset of jsAssets) {
  console.log(`  ${asset.name}: ${formatKiB(asset.gzipBytes)} gzip (${formatKiB(asset.rawBytes)} raw)`)
}
console.log(`  total: ${formatKiB(totalGzipBytes)} gzip`)

if (totalGzipBytes > maxTotalJsGzipKiB * 1024) {
  fail(
    `Total JS gzip size ${formatKiB(totalGzipBytes)} exceeds budget ${maxTotalJsGzipKiB} KiB.`,
  )
}

if (largest.gzipBytes > maxSingleJsGzipKiB * 1024) {
  fail(
    `Largest JS chunk ${largest.name} is ${formatKiB(largest.gzipBytes)}, ` +
      `above budget ${maxSingleJsGzipKiB} KiB.`,
  )
}
