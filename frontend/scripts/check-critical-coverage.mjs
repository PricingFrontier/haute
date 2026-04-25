import { existsSync, readdirSync, readFileSync, statSync } from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"

const METRICS = ["statements", "branches", "functions", "lines"]

export class CriticalCoverageError extends Error {
  constructor(messages) {
    super(Array.isArray(messages) ? messages.join("\n") : messages)
    this.name = "CriticalCoverageError"
  }
}

function normalizeRelative(filePath) {
  return filePath.replace(/\\/g, "/").replace(/^\.\//, "")
}

function hasGlobSyntax(pattern) {
  return /[*?{[]/.test(pattern)
}

function globToRegExp(pattern) {
  const normalized = normalizeRelative(pattern)
  let regex = "^"

  for (let i = 0; i < normalized.length; i += 1) {
    const char = normalized[i]
    const next = normalized[i + 1]

    if (char === "*") {
      if (next === "*") {
        const afterGlobstar = normalized[i + 2]
        if (afterGlobstar === "/") {
          regex += "(?:.*/)?"
          i += 2
        } else {
          regex += ".*"
          i += 1
        }
      } else {
        regex += "[^/]*"
      }
      continue
    }

    if (char === "?") {
      regex += "[^/]"
      continue
    }

    if (char === "{") {
      const end = normalized.indexOf("}", i + 1)
      if (end === -1) {
        regex += "\\{"
        continue
      }
      const alternatives = normalized
        .slice(i + 1, end)
        .split(",")
        .map((part) => part.replace(/[|\\{}()[\]^$+*?.]/g, "\\$&"))
      regex += `(?:${alternatives.join("|")})`
      i = end
      continue
    }

    regex += char.replace(/[|\\{}()[\]^$+*?.]/g, "\\$&")
  }

  return new RegExp(`${regex}$`)
}

function firstGlobSegmentIndex(pattern) {
  return normalizeRelative(pattern)
    .split("/")
    .findIndex((segment) => hasGlobSyntax(segment))
}

function patternSearchRoot(projectRoot, pattern) {
  const normalized = normalizeRelative(pattern)
  const segments = normalized.split("/")
  const firstGlob = firstGlobSegmentIndex(normalized)
  const baseSegments = firstGlob === -1 ? segments.slice(0, -1) : segments.slice(0, firstGlob)
  return path.resolve(projectRoot, ...baseSegments)
}

function walkFiles(root, current = root, files = []) {
  for (const entry of readdirSync(current, { withFileTypes: true })) {
    const entryPath = path.join(current, entry.name)
    if (entry.isDirectory()) {
      walkFiles(root, entryPath, files)
    } else if (entry.isFile()) {
      files.push(entryPath)
    }
  }
  return files
}

function findMatchingFiles(projectRoot, pattern) {
  const normalizedPattern = normalizeRelative(pattern)

  if (!hasGlobSyntax(normalizedPattern)) {
    const absolutePath = path.resolve(projectRoot, normalizedPattern)
    return existsSync(absolutePath) && statSync(absolutePath).isFile() ? [absolutePath] : []
  }

  const searchRoot = patternSearchRoot(projectRoot, normalizedPattern)
  if (!existsSync(searchRoot)) return []

  const matcher = globToRegExp(normalizedPattern)
  return walkFiles(projectRoot, searchRoot)
    .map((absolutePath) => ({
      absolutePath,
      relativePath: normalizeRelative(path.relative(projectRoot, absolutePath)),
    }))
    .filter(({ relativePath }) => matcher.test(relativePath))
    .map(({ absolutePath }) => absolutePath)
    .sort((left, right) =>
      normalizeRelative(path.relative(projectRoot, left)).localeCompare(
        normalizeRelative(path.relative(projectRoot, right)),
      ),
    )
}

function loadJson(filePath, description) {
  try {
    return JSON.parse(readFileSync(filePath, "utf8"))
  } catch (error) {
    if (error.code === "ENOENT") throw error
    throw new CriticalCoverageError(`Failed to parse ${description} at ${filePath}: ${error.message}`)
  }
}

function coverageKeyToRelative(projectRoot, coverageKey) {
  const nativeKey = path.normalize(coverageKey)
  const absolutePath = path.isAbsolute(nativeKey)
    ? nativeKey
    : path.resolve(projectRoot, nativeKey)
  return normalizeRelative(path.relative(projectRoot, absolutePath))
}

function buildCoverageIndex(projectRoot, coverageSummary) {
  const index = new Map()
  for (const [coverageKey, value] of Object.entries(coverageSummary)) {
    if (coverageKey === "total") continue
    index.set(coverageKeyToRelative(projectRoot, coverageKey), value)
  }
  return index
}

function validateThresholds(entry) {
  const missingMetrics = METRICS.filter(
    (metric) =>
      typeof entry.thresholds?.[metric] !== "number" ||
      Number.isNaN(entry.thresholds[metric]),
  )
  if (missingMetrics.length > 0) {
    throw new CriticalCoverageError(
      `Critical coverage pattern ${entry.pattern} must define numeric thresholds for ${missingMetrics.join(", ")}.`,
    )
  }
}

function formatPct(value) {
  return `${Number(value).toFixed(2)}%`
}

export async function checkCriticalCoverage(projectRoot, config) {
  const artifact = config?.artifact
  const entries = config?.entries

  if (typeof artifact !== "string" || artifact.trim() === "") {
    throw new CriticalCoverageError("criticalCoverage.artifact must be a non-empty path.")
  }
  if (!Array.isArray(entries) || entries.length === 0) {
    throw new CriticalCoverageError("criticalCoverage.entries must include at least one critical file or glob.")
  }

  const artifactPath = path.resolve(projectRoot, artifact)
  if (!existsSync(artifactPath)) {
    throw new CriticalCoverageError(
      `Coverage summary not found at ${normalizeRelative(artifact)}. Run \`npm run test:coverage\` first.`,
    )
  }

  const coverageSummary = loadJson(artifactPath, "coverage summary")
  const coverageIndex = buildCoverageIndex(projectRoot, coverageSummary)
  const failures = []
  const checkedFiles = []

  for (const entry of entries) {
    if (typeof entry?.pattern !== "string" || entry.pattern.trim() === "") {
      throw new CriticalCoverageError("Each criticalCoverage entry must define a non-empty pattern.")
    }
    validateThresholds(entry)

    const matchedFiles = findMatchingFiles(projectRoot, entry.pattern)
    if (matchedFiles.length === 0) {
      failures.push(
        hasGlobSyntax(entry.pattern)
          ? `Critical coverage pattern ${entry.pattern} matched no files.`
          : `Critical coverage file ${entry.pattern} does not exist.`,
      )
      continue
    }

    for (const absolutePath of matchedFiles) {
      const relativePath = normalizeRelative(path.relative(projectRoot, absolutePath))
      const fileCoverage = coverageIndex.get(relativePath)

      if (!fileCoverage) {
        failures.push(
          `${relativePath} exists on disk but is missing from ${path.basename(artifact)}. ` +
            "Ensure Vitest coverage includes this file and that the tests import it.",
        )
        continue
      }

      checkedFiles.push({ relativePath, pattern: entry.pattern })

      for (const metric of METRICS) {
        const actual = fileCoverage[metric]?.pct
        const required = entry.thresholds[metric]
        if (typeof actual !== "number") {
          failures.push(`${relativePath} is missing ${metric}.pct in ${path.basename(artifact)}.`)
          continue
        }
        if (actual < required) {
          failures.push(
            `${relativePath} ${metric} coverage is ${formatPct(actual)}, below required ${formatPct(required)}. ` +
              `Add focused tests or lower the configured threshold only with review.`,
          )
        }
      }
    }
  }

  if (failures.length > 0) {
    throw new CriticalCoverageError(failures)
  }

  return { checkedFiles }
}

function loadPackageConfig(projectRoot) {
  const packageJsonPath = path.join(projectRoot, "package.json")
  const packageJson = loadJson(packageJsonPath, "package.json")
  if (!packageJson.criticalCoverage) {
    throw new CriticalCoverageError("package.json must define criticalCoverage for this gate.")
  }
  return packageJson.criticalCoverage
}

async function main() {
  const projectRoot = process.cwd()
  try {
    const result = await checkCriticalCoverage(projectRoot, loadPackageConfig(projectRoot))
    console.log(`Critical coverage gate passed for ${result.checkedFiles.length} frontend file(s).`)
  } catch (error) {
    console.error(error.message)
    process.exitCode = 1
  }
}

const isCli = process.argv[1]
  ? fileURLToPath(import.meta.url) === path.resolve(process.argv[1])
  : false

if (isCli) {
  await main()
}
