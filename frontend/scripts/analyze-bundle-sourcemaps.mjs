import { existsSync, readdirSync, readFileSync } from "node:fs"
import path from "node:path"
import { fileURLToPath, pathToFileURL } from "node:url"

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const defaultAssetsDir = path.resolve(__dirname, "../../src/haute/static/assets")
const BASE64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"

function formatKiB(bytes) {
  return `${(bytes / 1024).toFixed(1)} KiB`
}

function parseTopN(raw) {
  if (raw == null || raw === "") return 15
  const parsed = Number(raw)
  if (!Number.isInteger(parsed) || parsed <= 0) {
    throw new Error(`HAUTE_BUNDLE_ANALYZE_TOP_N must be a positive integer, got ${JSON.stringify(raw)}.`)
  }
  return parsed
}

function validateSourceMap(chunkName, sourceMap) {
  if (!sourceMap || typeof sourceMap !== "object") {
    throw new Error(`${chunkName} source map must be an object.`)
  }
  if (sourceMap.version !== 3) {
    throw new Error(`${chunkName} source map must use version 3.`)
  }
  if (!Array.isArray(sourceMap.sources)) {
    throw new Error(`${chunkName} source map must include a sources array.`)
  }
  if (typeof sourceMap.mappings !== "string") {
    throw new Error(`${chunkName} source map must include a mappings string.`)
  }
}

function decodeVlqValues(segment) {
  const values = []
  let index = 0

  while (index < segment.length) {
    let result = 0
    let shift = 0
    let continuation = true

    while (continuation) {
      if (index >= segment.length) {
        throw new Error(`Invalid source map VLQ segment "${segment}".`)
      }
      const digit = BASE64.indexOf(segment[index])
      if (digit < 0) {
        throw new Error(`Invalid source map VLQ character "${segment[index]}".`)
      }
      index += 1
      continuation = (digit & 32) !== 0
      result += (digit & 31) << shift
      shift += 5
    }

    const negative = (result & 1) === 1
    const value = result >> 1
    values.push(negative ? -value : value)
  }

  return values
}

function decodeMappings(mappings) {
  let previousSource = 0
  let previousOriginalLine = 0
  let previousOriginalColumn = 0
  let previousName = 0

  return mappings.split(";").map((line) => {
    let previousGeneratedColumn = 0
    if (line === "") return []

    return line.split(",").filter(Boolean).map((encodedSegment) => {
      const fields = decodeVlqValues(encodedSegment)
      if (![1, 4, 5].includes(fields.length)) {
        throw new Error(`Invalid source map segment "${encodedSegment}".`)
      }

      previousGeneratedColumn += fields[0]
      const segment = { generatedColumn: previousGeneratedColumn }

      if (fields.length >= 4) {
        previousSource += fields[1]
        previousOriginalLine += fields[2]
        previousOriginalColumn += fields[3]
        segment.source = previousSource
        segment.originalLine = previousOriginalLine
        segment.originalColumn = previousOriginalColumn
      }

      if (fields.length === 5) {
        previousName += fields[4]
        segment.name = previousName
      }

      return segment
    })
  })
}

function generatedLines(generatedCode) {
  return generatedCode.split("\n")
}

function byteLengthBetweenColumns(line, startColumn, endColumn) {
  return Buffer.byteLength(line.slice(startColumn, endColumn))
}

export function analyzeSourceMapChunk({ chunkName, generatedCode, sourceMap }) {
  validateSourceMap(chunkName, sourceMap)

  const mappingsByLine = decodeMappings(sourceMap.mappings)
  const lines = generatedLines(generatedCode)
  const contributorsBySource = new Map()

  mappingsByLine.forEach((segments, lineIndex) => {
    const generatedLine = lines[lineIndex] ?? ""
    const generatedLineLength = generatedLine.length
    for (let index = 0; index < segments.length; index += 1) {
      const segment = segments[index]
      if (segment.source == null) continue
      if (segment.source < 0 || segment.source >= sourceMap.sources.length) {
        throw new Error(`${chunkName} source map references missing source index ${segment.source}.`)
      }

      const nextSegment = segments[index + 1]
      const endColumn = nextSegment?.generatedColumn ?? generatedLineLength
      const generatedBytes = Math.max(
        0,
        byteLengthBetweenColumns(generatedLine, segment.generatedColumn, endColumn),
      )
      if (generatedBytes === 0) continue

      const source = sourceMap.sources[segment.source]
      const existing = contributorsBySource.get(source) ?? {
        source,
        generatedBytes: 0,
        originalBytes: typeof sourceMap.sourcesContent?.[segment.source] === "string"
          ? Buffer.byteLength(sourceMap.sourcesContent[segment.source])
          : null,
      }
      existing.generatedBytes += generatedBytes
      contributorsBySource.set(source, existing)
    }
  })

  const contributors = [...contributorsBySource.values()]
    .sort((a, b) => b.generatedBytes - a.generatedBytes || a.source.localeCompare(b.source))

  return {
    chunkName,
    generatedBytes: Buffer.byteLength(generatedCode),
    totalMappedGeneratedBytes: contributors.reduce((sum, contributor) => sum + contributor.generatedBytes, 0),
    contributors,
  }
}

export function analyzeBundleDirectory(assetsDir = defaultAssetsDir) {
  let entries
  try {
    entries = readdirSync(assetsDir)
  } catch {
    throw new Error(`Bundle assets not found at ${assetsDir}. Run 'npm run analyze:bundle' after a sourcemap build.`)
  }

  const jsAssets = entries
    .filter((entry) => entry.endsWith(".js"))
    .sort()

  if (jsAssets.length === 0) {
    throw new Error(`No JavaScript assets found in ${assetsDir}.`)
  }

  return jsAssets.map((chunkName) => {
    const chunkPath = path.join(assetsDir, chunkName)
    const mapPath = `${chunkPath}.map`
    if (!existsSync(mapPath)) {
      throw new Error(`Missing source map for JavaScript asset ${chunkName}.`)
    }

    let sourceMap
    try {
      sourceMap = JSON.parse(readFileSync(mapPath, "utf8"))
    } catch (error) {
      throw new Error(`Invalid source map JSON for ${chunkName}: ${error instanceof Error ? error.message : String(error)}`)
    }

    return analyzeSourceMapChunk({
      chunkName,
      generatedCode: readFileSync(chunkPath, "utf8"),
      sourceMap,
    })
  }).sort((a, b) => b.generatedBytes - a.generatedBytes || a.chunkName.localeCompare(b.chunkName))
}

export function formatAnalysisReport(chunks, { topN = 15 } = {}) {
  if (!Array.isArray(chunks)) {
    throw new Error("formatAnalysisReport expected an array of chunk analyses.")
  }

  const lines = ["Bundle source-map contributors:"]
  for (const chunk of chunks) {
    lines.push(`${chunk.chunkName}: ${formatKiB(chunk.generatedBytes)} raw, ${formatKiB(chunk.totalMappedGeneratedBytes)} mapped`)
    for (const contributor of chunk.contributors.slice(0, topN)) {
      const percent = chunk.totalMappedGeneratedBytes > 0
        ? ((contributor.generatedBytes / chunk.totalMappedGeneratedBytes) * 100).toFixed(1)
        : "0.0"
      const original = contributor.originalBytes == null
        ? ""
        : `, ${formatKiB(contributor.originalBytes)} original`
      lines.push(`  ${contributor.source}: ${formatKiB(contributor.generatedBytes)} generated (${percent}%${original})`)
    }
  }
  return lines.join("\n")
}

function run() {
  let topN
  try {
    topN = parseTopN(process.env.HAUTE_BUNDLE_ANALYZE_TOP_N)
    const assetsDir = process.argv[2] ? path.resolve(process.argv[2]) : defaultAssetsDir
    console.log(formatAnalysisReport(analyzeBundleDirectory(assetsDir), { topN }))
  } catch (error) {
    console.error(error instanceof Error ? error.message : String(error))
    process.exitCode = 1
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  run()
}
