/**
 * Session-lived caches for the Version Control panel (GitPanel).
 *
 * The panel is conditionally mounted, so every open remounts it — without a
 * cache each open refetched four endpoints from scratch behind a "Loading
 * version history…" flash. These module-level maps survive remounts so a
 * branch viewed once this session paints instantly, stale-while-revalidate:
 * GitPanel ALWAYS refetches after hydrating, and byte-identical responses are
 * short-circuited (via the stored serializations) so an unchanged revalidate
 * applies no state at all. Staleness is therefore bounded by one round trip,
 * which is why the cache can be permissive about invalidation.
 *
 * Bounds: the per-branch history cache keeps the last BRANCH_HISTORY_CAP
 * branches; the milestone-saves cache keeps MILESTONE_SAVES_CAP entries.
 * A milestone's folded saves are content-addressed by its merge sha
 * (immutable), so that cache never revalidates. Pending saves are NOT
 * sha-addressed and are only ever cached as part of a branch's history
 * snapshot. Both maps evict least-recently-used. Nothing here is durable —
 * per-tab, per-session only.
 */
import type {
  GitGraphResponse,
  GitLedgerSave,
  GitManagedBranch,
  GitMilestoneEntry,
} from "../api/types"

export const BRANCH_HISTORY_CAP = 8
export const MILESTONE_SAVES_CAP = 64

/** One branch's last-seen history payloads plus their serializations (the
 *  serializations feed GitPanel's unchanged-payload short-circuit). */
export interface BranchHistoryEntry {
  milestones: GitMilestoneEntry[]
  milestonesJson: string
  pending: GitLedgerSave[]
  pendingJson: string
  forkBranches: GitManagedBranch[]
  forkBranchesJson: string
}

/** Cheap stable serialization for payload equality. Responses are parsed
 *  JSON from one backend serializer, so key order is stable and stringify
 *  round-trips byte-identical payloads to identical strings. */
export function serializePayload(value: unknown): string {
  return JSON.stringify(value)
}

// Map preserves insertion order → oldest entry is the first key. Reads
// re-insert (touch) so "oldest" means least-recently-used.
const branchHistory = new Map<string, BranchHistoryEntry>()
const milestoneSaves = new Map<string, GitLedgerSave[]>()

// The graph endpoint returns the WHOLE forest (it is not branch-scoped), so a
// single slot serves every branch's hydration.
let graphCache: { graph: GitGraphResponse; json: string } | null = null

function touch<K, V>(map: Map<K, V>, key: K): V | undefined {
  const value = map.get(key)
  if (value !== undefined) {
    map.delete(key)
    map.set(key, value)
  }
  return value
}

function put<K, V>(map: Map<K, V>, key: K, value: V, cap: number): void {
  map.delete(key)
  map.set(key, value)
  while (map.size > cap) {
    const oldest = map.keys().next().value
    if (oldest === undefined) break
    map.delete(oldest)
  }
}

export function readBranchHistory(branch: string): BranchHistoryEntry | undefined {
  return touch(branchHistory, branch)
}

export function writeBranchHistory(branch: string, entry: BranchHistoryEntry): void {
  put(branchHistory, branch, entry, BRANCH_HISTORY_CAP)
}

export function readGraphCache(): { graph: GitGraphResponse; json: string } | null {
  return graphCache
}

export function writeGraphCache(graph: GitGraphResponse, json: string): void {
  graphCache = { graph, json }
}

export function readMilestoneSaves(sha: string): GitLedgerSave[] | undefined {
  return touch(milestoneSaves, sha)
}

export function writeMilestoneSaves(sha: string, saves: GitLedgerSave[]): void {
  put(milestoneSaves, sha, saves, MILESTONE_SAVES_CAP)
}

/** Test hook: module-level state leaks across tests without this. */
export function clearGitPanelCaches(): void {
  branchHistory.clear()
  milestoneSaves.clear()
  graphCache = null
}
