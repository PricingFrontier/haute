/**
 * Repro for claim: hashconfig-djb2-32bit-collision-staleness
 *
 * hashConfig in frontend/src/stores/useNodeResultsStore.ts uses a 32-bit djb2
 * digest of JSON.stringify(sortKeys(config)) as the SOLE staleness key for
 * solve/train/explore results and estimate staleness. The freshness decision is
 * a single equality of that 32-bit digest (e.g. ExplorePreview.tsx:92
 * `cachedResult.configHash === configHash`; useStaleConfigEstimate.ts:60
 * `cachedResult.configHash !== configHash`).
 *
 * This repro ports djb2 + hashConfig VERBATIM from the source (lines 168-187),
 * then demonstrates:
 *   (A) two MATERIALLY DIFFERENT configs whose hashConfig() digests COLLIDE, and
 *   (B) that the exact staleness predicate used in the UI treats a config edit
 *       from config A -> config B as "NOT stale" (no config-changed indication),
 *       i.e. an outdated result would be shown as current.
 *
 * Isolation: pure in-memory; no project files read/written; no haute imports.
 * Run: node review/02-findings/repro/frontend__hashconfig-djb2-32bit-collision-staleness.mjs
 */

// ─── VERBATIM port of source lines 168-187 ──────────────────────────
// frontend/src/stores/useNodeResultsStore.ts
function djb2(s) {
  let hash = 5381
  for (let i = 0; i < s.length; i++) {
    hash = ((hash << 5) + hash + s.charCodeAt(i)) | 0
  }
  return (hash >>> 0).toString(36)
}

function hashConfig(config) {
  const { _nodeId, _columns, _schemaWarnings, _availableColumns, ...rest } = config
  void _nodeId; void _columns; void _schemaWarnings; void _availableColumns
  const sortKeys = (o) => {
    if (o === null || typeof o !== "object") return o
    if (Array.isArray(o)) return o.map(sortKeys)
    const sorted = Object.keys(o).sort()
    return Object.fromEntries(sorted.map(k => [k, sortKeys(o[k])]))
  }
  return djb2(JSON.stringify(sortKeys(rest)))
}

// ─── The exact staleness predicate the UI uses ─────────────────────
// ExplorePreview.tsx:92  -> currentCachedResult is the cached result ONLY when equal
// useStaleConfigEstimate.ts:60 -> isStale = cachedResult.configHash !== configHash
function isStale(cachedConfigHash, currentConfigHash) {
  return cachedConfigHash !== currentConfigHash
}

// ─── (A) Find a real collision between materially different configs ──
// Realistic shape: an optimiser config with a numeric hyperparameter the actuary
// might tweak. We vary max_iter over a large integer range and look for two
// DISTINCT values that produce the SAME 32-bit digest. djb2 over 4 bytes has
// ~4.29e9 codomain, so a collision within a few-hundred-million enumeration is
// expected by the birthday bound (and even a direct first-collision search
// finds one quickly here).

// A realistic, materially-different config parameterised by a "tag" string that
// stands in for an edited value (e.g. an objective name / factor label / note an
// actuary would actually change). Two different tags = two different configs that
// compute different results. We vary the tag to exercise djb2's distribution.
function makeConfig(tag) {
  return {
    objective: "minimise_loss",
    tolerance: 1e-6,
    factor_columns: [["age"], ["region"]],
    label: tag, // the materially-different, user-editable value
  }
}

// Deterministic pseudo-random tag generator (no external deps) so the run is
// reproducible. xorshift32.
function makeRng(seed) {
  let x = seed >>> 0 || 1
  return () => {
    x ^= x << 13; x >>>= 0
    x ^= x >>> 17
    x ^= x << 5;  x >>>= 0
    return x
  }
}

function findCollision() {
  // Birthday bound for a 32-bit codomain: ~2^16 samples => ~50% collision,
  // and collision is near-certain by a few hundred thousand samples. We store
  // digest -> tag. A well-distributed hash collides far below the Map size cap.
  const seen = new Map()
  const rng = makeRng(0xC0FFEE)
  const LIMIT = 5_000_000
  for (let i = 0; i < LIMIT; i++) {
    // Build a varied tag string; structurally diverse to exercise avalanche.
    const r = rng()
    const tag = `opt-${r.toString(36)}-${(r ^ 0x9E3779B9).toString(36)}-v${i.toString(36)}`
    const h = hashConfig(makeConfig(tag))
    const prev = seen.get(h)
    if (prev !== undefined && prev !== tag) {
      return { a: prev, b: tag, hash: h }
    }
    seen.set(h, tag)
  }
  return null
}

console.log("Searching for a djb2/hashConfig collision between materially different configs...")
const t0 = Date.now()
const collision = findCollision()
const elapsed = ((Date.now() - t0) / 1000).toFixed(1)

if (!collision) {
  console.error(`NO COLLISION FOUND within search budget (this would weaken the claim). elapsed=${elapsed}s`)
  process.exitCode = 2
} else {
  const { a, b, hash } = collision
  const cfgA = makeConfig(a)
  const cfgB = makeConfig(b)
  const hashA = hashConfig(cfgA)
  const hashB = hashConfig(cfgB)
  const canonA = JSON.stringify(cfgA)
  const canonB = JSON.stringify(cfgB)

  console.log("")
  console.log(`COLLISION FOUND in ${elapsed}s`)
  console.log(`  config A label = ${a}`)
  console.log(`  config B label = ${b}`)
  console.log(`  hashConfig(A) = ${hashA}`)
  console.log(`  hashConfig(B) = ${hashB}`)
  console.log(`  canonical A   = ${canonA}`)
  console.log(`  canonical B   = ${canonB}`)
  console.log(`  configs materially different? ${canonA !== canonB}`)
  console.log(`  digests equal?                ${hashA === hashB}`)

  // ─── Assertions on the SPECIFIC wrong behaviour ──────────────────
  // 1. The two configs are genuinely different (a real config edit).
  if (canonA === canonB) throw new Error("ASSERT FAILED: configs are not materially different")
  // 2. Their 32-bit digests collide.
  if (hashA !== hashB) throw new Error("ASSERT FAILED: digests do not actually collide")

  // 3. THE BUG: After editing the cached config A -> config B, the UI staleness
  //    predicate reports NOT stale (it should report stale because the config
  //    materially changed and the cached result was computed for config A).
  const cachedResultConfigHash = hashA          // result cached when config was A
  const currentConfigHash = hashB               // user has since edited config to B
  const reportedStale = isStale(cachedResultConfigHash, currentConfigHash)

  // What the UI SHOULD do: stale, because canonA !== canonB.
  const correctlyStale = canonA !== canonB

  console.log("")
  console.log(`  UI reports stale? ${reportedStale}   (SHOULD be ${correctlyStale})`)

  if (reportedStale !== false) {
    throw new Error("ASSERT FAILED: expected UI to (wrongly) report NOT stale on collision")
  }
  if (correctlyStale !== true) {
    throw new Error("ASSERT FAILED: expected the config edit to be genuinely stale")
  }

  // 4. ExplorePreview.tsx:92 currentCachedResult logic: an outdated result is
  //    served as current. Model the predicate directly.
  const cachedResult = { configHash: cachedResultConfigHash, result: "RESULT_FOR_CONFIG_A" }
  const currentCachedResult =
    cachedResult && cachedResult.configHash === currentConfigHash ? cachedResult : null
  console.log(`  ExplorePreview serves cached result for config A as current? ${currentCachedResult !== null}`)
  if (currentCachedResult === null) {
    throw new Error("ASSERT FAILED: expected ExplorePreview to serve the stale cached result")
  }
  console.log(`  -> served result = ${currentCachedResult.result} while live config = B`)

  console.log("")
  console.log("REPRODUCED: a materially different config edit reads as 'not stale';")
  console.log("an outdated result computed for config A is displayed as current for config B.")
}
