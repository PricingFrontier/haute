import { Fragment, useState, useEffect, useLayoutEffect, useMemo, useRef } from "react"
import {
  GitFork, GitBranch, Clock, ChevronRight, ChevronDown, RefreshCw, History,
  Pencil, Plus, Minus, ArrowRightLeft, Copy, CornerDownRight, FileText, Eye, RotateCcw,
} from "lucide-react"
import PanelShell from "./PanelShell"
import BranchManager from "../components/BranchManager"
import GitNavigationConfirm from "../components/GitNavigationConfirm"
import RemotePushControl from "../components/RemotePushControl"
import Tooltip from "../components/Tooltip"
import useToastStore from "../stores/useToastStore"
import useGitStore from "../stores/useGitStore"
import useGraphStore from "../stores/useGraphStore"
import { createWorkingBranch, setWorkingBranch } from "../api/client"
import type { GitLedgerSave, GitFileChange } from "../api/types"
import { computeGitGraphLayout, computeRailRuns, railWidth } from "./gitgraph/layout"
import type { RailModel, RailRow, RailRowGeom, RowDescriptor } from "./gitgraph/layout"
import { GraphRailCell, GraphRailHeader, GraphRailOverlay } from "./gitgraph/GraphCell"
import { recordSwitch } from "../utils/vcHistory"
import { gitErrorMessage } from "../utils/gitError"
import { useGitHistory } from "./git/useGitHistory"

/** Minimal graph-branch shape the in-row spawn chips need. */
interface SpawnChipBranch {
  name: string
  is_archived: boolean
  colorIndex?: number
}

const HASH_TOOLTIP =
  "Commit hash — a unique ID for every save or milestone. Fragment of a much " +
  "longer hexadecimal string."

// Rail-cell node centres: aligned with the first text line of each row kind
// (milestone rows carry py-2 + 12px text; save rows 11px text, plus the 6px
// pt-1.5 that replaces the old container gap on rows after the first).
const MILESTONE_DOT_Y = 16
const SAVE_DOT_Y = 8
const SAVE_ROW_GAP = 6

interface GitPanelProps {
  onClose: () => void
  onSave?: () => Promise<boolean>
}

export default function GitPanel({ onClose, onSave }: GitPanelProps) {
  const status = useGitStore((s) => s.status)
  const loadStatus = useGitStore((s) => s.loadStatus)
  // Peek state lives in the store so the toolbar indicator can return to the
  // current branch without the panel being open (S38).
  const viewBranch = useGitStore((s) => s.peekBranch)
  // Bumped after a save so we re-fetch without a manual refresh; a save must not
  // move the selection. A commit bumps a separate nonce and DOES select (S38).
  const historyNonce = useGitStore((s) => s.historyNonce)
  const commitNonce = useGitStore((s) => s.commitNonce)
  // Open the read-only side-by-side comparison on a version (S11).

  const workingBranch = status?.working_branch ?? null
  const peeking = viewBranch !== null && viewBranch !== workingBranch
  // The branch whose history the panel shows: the peeked branch, else the
  // current working branch (null before the first status load).
  const branchKey = viewBranch ?? workingBranch

  useEffect(() => {
    loadStatus()
  }, [loadStatus])

  return (
    <GitPanelBranchScope
      key={`${branchKey ?? "none"}:${peeking ? "peek" : "working"}`}
      onClose={onClose}
      onSave={onSave}
      workingBranch={workingBranch}
      viewBranch={viewBranch}
      peeking={peeking}
      branchKey={branchKey}
      historyNonce={historyNonce}
      commitNonce={commitNonce}
    />
  )
}

type GitPanelBranchScopeProps = GitPanelProps & {
  workingBranch: string | null
  viewBranch: string | null
  peeking: boolean
  branchKey: string | null
  historyNonce: number
  commitNonce: number
}

function GitPanelBranchScope({
  onClose,
  onSave,
  workingBranch,
  viewBranch,
  peeking,
  branchKey,
  historyNonce,
  commitNonce,
}: GitPanelBranchScopeProps) {
  const addToast = useToastStore((s) => s.addToast)
  const loadStatus = useGitStore((s) => s.loadStatus)
  const dirty = useGraphStore((s) => s.dirty)
  const setViewBranch = useGitStore((s) => s.setPeekBranch)
  const openComparison = useGitStore((s) => s.openComparison)
  const {
    milestones,
    pending,
    expanded,
    loading,
    selectedSha,
    graph,
    rowsBranch,
    refresh,
    toggleExpand,
    selectSha: setSelectedSha,
  } = useGitHistory({ branchKey, peeking, historyNonce, commitNonce, addToast })
  const [dirtyNavigation, setDirtyNavigation] = useState<(() => void) | null>(null)
  // Right-click "new branch from here" (S38): the anchor is the menu position +
  // fork point; the draft is the naming step once an option is picked.
  const [forkAnchor, setForkAnchor] = useState<
    { x: number; y: number; sha: string; canMove: boolean; peeking: boolean; label: string } | null
  >(null)
  const [forkDraft, setForkDraft] = useState<{ sha: string; move: boolean; name: string; x: number; y: number } | null>(null)
  const [forking, setForking] = useState(false)
  // Context menus on the rail (feedback round 2): a milestone dot offers the
  // commit actions, a lane line offers its branch's actions.
  const [dotMenu, setDotMenu] = useState<{ sha: string; x: number; y: number } | null>(null)
  const [laneMenu, setLaneMenu] = useState<{ branch: string; x: number; y: number } | null>(null)
  const [switching, setSwitching] = useState(false)

  // The row context menu. ALWAYS preventDefault (never fall through to the
  // browser menu) and always open an app menu. Fork-from-history is only
  // meaningful on the current branch's own history (the engine forks from the
  // current working branch), so the fork actions are gated on !peeking below;
  // while peeking the menu still opens, showing only the view/move items.
  const openForkMenu = (e: React.MouseEvent, sha: string, canMove: boolean, label: string) => {
    e.preventDefault()
    e.stopPropagation()
    setForkAnchor({ x: e.clientX, y: e.clientY, sha, canMove, peeking, label })
  }

  const startFork = (move: boolean) => {
    if (!forkAnchor) return
    const openDraft = () => {
      setForkDraft({ sha: forkAnchor.sha, move, name: "", x: forkAnchor.x, y: forkAnchor.y })
      setForkAnchor(null)
    }
    openDraft()
  }

  const submitFork = async () => {
    if (!forkDraft || !forkDraft.name.trim()) return
    setForking(true)
    try {
      const res = await createWorkingBranch(forkDraft.name.trim(), {
        at: forkDraft.sha,
        move: forkDraft.move,
      })
      addToast(
        "success",
        forkDraft.move
          ? `Created ${res.working_branch} and moved your work`
          : `Created ${res.working_branch}`,
      )
      setForkDraft(null)
      if (res.switched) {
        window.location.reload()
        return
      }
      await refresh()
    } catch (err) {
      const detail = gitErrorMessage(err, "unknown error")
      addToast("error", `Could not create branch: ${detail}`)
    } finally {
      setForking(false)
    }
  }

  const requestForkSubmit = () => {
    if (forkDraft?.move) guardNavigation(() => { void submitFork() })
    else void submitFork()
  }

  // Switch the working branch in place (no page reload) — the lane menu's
  // primary action. The pipeline lands via the websocket sync; the panel
  // returns to "current" view, refreshes, and the switch is recorded as an
  // undoable history entry.
  const performSwitch = async (branch: string) => {
    setSwitching(true)
    const from = workingBranch
    try {
      await setWorkingBranch(branch, false)
      addToast("success", `Switched to ${branch}`)
      if (from !== null) recordSwitch(from, branch)
      setViewBranch(null)
      await loadStatus()
      await refresh()
    } catch (err) {
      const detail = gitErrorMessage(err, "unknown error")
      addToast("error", `Could not switch branch: ${detail}`)
    } finally {
      setSwitching(false)
    }
  }

  const guardNavigation = (proceed: () => void) => {
    if (dirty) setDirtyNavigation(() => proceed)
    else proceed()
  }

  // Open the read-only side-by-side comparison on a version (S11).
  const viewVersion = (sha: string, label: string) => openComparison({ sha, label })

  // Begin a MOVE to a version (P6 §3.4): a real checkout, gated by the pre-move
  // save/discard/confirm prompt. Distinct from viewVersion's read-only compare.
  const moveVersion = (sha: string, label: string) =>
    useGitStore.getState().requestMove({ sha, label })

  // ---------------------------------------------------------------------------
  // Graph rail (D-B): the visual row list in render order + its rail model.
  // ---------------------------------------------------------------------------

  // Row descriptors mirror the render order below exactly: pending saves
  // first, then milestones newest-first, each followed by its expanded save
  // rows or a single placeholder row (loading / no saves recorded). Rows are
  // keyed by kind:sha — a placeholder shares its milestone's sha.
  const railRowData = useMemo(() => {
    const rows: RowDescriptor[] = []
    const indexByKey = new Map<string, number>()
    const push = (row: RowDescriptor) => {
      indexByKey.set(`${row.kind}:${row.sha}`, rows.length)
      rows.push(row)
    }
    for (const s of pending) push({ kind: "pending-save", sha: s.sha })
    for (const m of milestones) {
      const exp = expanded[m.sha]
      push({ kind: "milestone", sha: m.sha, expanded: exp !== undefined })
      if (exp === undefined) continue
      if (exp === "loading" || exp.length === 0) {
        push({ kind: "placeholder", sha: m.sha, milestoneSha: m.sha })
      } else {
        for (const s of exp) push({ kind: "save", sha: s.sha, milestoneSha: m.sha })
      }
    }
    return { rows, indexByKey }
  }, [pending, milestones, expanded])

  // Null whenever the rail must not draw: no graph payload (failed fetch),
  // empty history, rows still belonging to a previously viewed branch (the
  // peek's in-flight window — drawing would mislabel them), or a degraded
  // layout (unknown peek target, null working branch — layout returns
  // laneCount 0 for those). The list never depends on this.
  const rail = useMemo<RailModel | null>(() => {
    if (graph === null || milestones.length === 0) return null
    if (rowsBranch !== (viewBranch ?? workingBranch)) return null
    const model = computeGitGraphLayout(graph, { viewBranch, rows: railRowData.rows })
    return model.laneCount === 0 ? null : model
  }, [graph, viewBranch, workingBranch, rowsBranch, milestones.length, railRowData])

  const railW = rail === null ? 0 : railWidth(rail.laneCount, rail.slotCount)

  // ---------------------------------------------------------------------------
  // Overlay geometry: every straight vertical line of the milestones box is
  // drawn ONCE by an absolutely-positioned overlay (dash phase and stroke
  // continuity across rows and box borders — see gitgraph/layout.ts). The
  // per-row rail cells are measured after paint; the runs derive from the
  // rail model plus those measurements.
  // ---------------------------------------------------------------------------
  const milestonesBoxRef = useRef<HTMLDivElement | null>(null)
  const [rowGeom, setRowGeom] = useState<(RailRowGeom | null)[] | null>(null)
  const rowGeomKey = useRef("")
  useLayoutEffect(() => {
    const box = milestonesBoxRef.current
    if (box === null || rail === null) {
      rowGeomKey.current = ""
      setRowGeom(null)
      return
    }
    const measure = () => {
      const boxTop = box.getBoundingClientRect().top
      const cells = box.querySelectorAll<HTMLElement>("[data-rail-row]")
      // The milestones box holds the row tail of rail.rows (pending rows sit
      // in their own box and contribute no overlay runs).
      const offset = rail.rows.length - cells.length
      if (offset < 0) return
      const geom: (RailRowGeom | null)[] = new Array(rail.rows.length).fill(null)
      cells.forEach((el, j) => {
        const r = el.getBoundingClientRect()
        geom[offset + j] = {
          top: r.top - boxTop,
          height: r.height,
          dotY: Number(el.dataset.dotY ?? 0),
        }
      })
      const key = geom.map((g) => (g ? `${g.top.toFixed(1)}:${g.height.toFixed(1)}:${g.dotY}` : "-")).join("|")
      if (key !== rowGeomKey.current) {
        rowGeomKey.current = key
        setRowGeom(geom)
      }
    }
    measure()
    const ro = new ResizeObserver(measure)
    ro.observe(box)
    return () => ro.disconnect()
  }, [rail, railRowData])

  const railRuns = useMemo(() => {
    if (rail === null || rowGeom === null || rowGeom.length !== rail.rows.length) return null
    return computeRailRuns(rail, rowGeom)
  }, [rail, rowGeom])

  // In-row spawn chips, derived from graph ancestry. Anchor
  // rules mirror the rail's stubs: the source save's row when visible (live
  // branches only), else the credit milestone, else the fork point. Branches
  // with their own lane (the viewed one and its ancestors) never chip.
  const spawnChipsBySha = useMemo(() => {
    const map = new Map<string, SpawnChipBranch[]>()
    if (graph === null || rail === null) return map
    const laned = new Set(rail.lanes.map((l) => l.branch))
    const milestoneShas = new Set(milestones.map((m) => m.sha))
    const visibleSaves = new Set<string>(pending.map((s) => s.sha))
    for (const exp of Object.values(expanded)) {
      if (exp !== "loading") for (const s of exp) visibleSaves.add(s.sha)
    }
    const colorBy = new Map(rail.topChips.map((c) => [c.branch, c.colorIndex]))
    for (const b of graph.branches) {
      if (laned.has(b.name)) continue
      const src = b.fork_source_sha
      const anchor =
        src !== null && !b.is_archived && visibleSaves.has(src)
          ? src
          : b.fork_credit_sha !== null && milestoneShas.has(b.fork_credit_sha)
            ? b.fork_credit_sha
            : b.fork_point_sha !== null && milestoneShas.has(b.fork_point_sha)
              ? b.fork_point_sha
              : null
      if (anchor === null) continue
      const list = map.get(anchor) ?? []
      list.push({ name: b.name, is_archived: b.is_archived, colorIndex: colorBy.get(b.name) })
      map.set(anchor, list)
    }
    return map
  }, [graph, rail, milestones, pending, expanded])

  const chipsAt = (sha: string): SpawnChipBranch[] =>
    spawnChipsBySha.get(sha) ?? []

  // rail.rows is 1:1 with railRowData.rows (both derive from the same state
  // in the same render), so the key lookup always lands when rail is set.
  const railCell = (kind: RowDescriptor["kind"], sha: string, dotY: number) => {
    if (rail === null) return null
    const row: RailRow | undefined = rail.rows[railRowData.indexByKey.get(`${kind}:${sha}`) ?? -1]
    return (
      <GraphRailCell
        row={row}
        width={railW}
        dotY={dotY}
        laneCount={rail.laneCount}
        dimmed={rail.viewedIsArchived}
        onToggleExpand={toggleExpand}
        onDotContextMenu={(dotSha, x, y) => setDotMenu({ sha: dotSha, x, y })}
        onLaneContextMenu={(branch, x, y) => setLaneMenu({ branch, x, y })}
      />
    )
  }

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  return (
    <PanelShell
      testId="git-panel"
      title="Version Control"
      onClose={onClose}
      maxWidth={768}
      icon={<GitFork size={14} style={{ color: "var(--success)" }} />}
      // Branch + commit are not repeated here — the toolbar indicator beside this
      // panel already shows them (S38).
      actions={
        <Tooltip label="Refresh version history" side="bottom">
          <button
            data-testid="git-panel-refresh"
            onClick={refresh}
            disabled={loading}
            className="p-1 rounded shrink-0 transition-colors disabled:opacity-40 hover:bg-[var(--bg-hover)]"
            style={{ color: "var(--text-muted)" }}
          >
            <RefreshCw size={12} className={loading ? "animate-spin" : undefined} />
          </button>
        </Tooltip>
      }
    >
      <div className="flex-1 min-h-0 overflow-y-auto">
        {/* Deliberate push to a remote (S16/S33). Out-of-version saves drive its
            pre-push integrity prompt; ahead/behind re-fetch after a save/commit. */}
        <div className="pb-2" style={{ borderBottom: "1px solid var(--border)" }}>
          <RemotePushControl
            pendingSaveCount={pending.length}
            refreshNonce={historyNonce + commitNonce}
          />
        </div>

        {/* Branch manager (S19/S28: the Git panel hosts it) */}
        {dirtyNavigation && (
          <GitNavigationConfirm
            onCancel={() => setDirtyNavigation(null)}
            onDiscard={() => {
              const proceed = dirtyNavigation
              setDirtyNavigation(null)
              proceed()
            }}
            onSave={async () => {
              try {
                if (!await (onSave?.() ?? Promise.resolve(false))) return
                const proceed = dirtyNavigation
                setDirtyNavigation(null)
                proceed?.()
              } catch {
                // Saving failed; keep the choice visible.
              }
            }}
          />
        )}
        <BranchManager selectedBranch={viewBranch ?? workingBranch} onPeek={setViewBranch} onSave={onSave} />

        {/* Save history — a distinct, inset section set apart from the branch
            list above (BranchManager already draws the seam border); the inset
            keeps the banner + dividers narrow so the section reads as one
            coherent group (S38). */}
        <div className="px-2 pt-3 pb-2 flex flex-col gap-2">
          <div className="flex items-center gap-1.5 px-1">
            <History size={13} style={{ color: "var(--text-muted)" }} />
            <span className="text-[11px] font-medium uppercase tracking-wider" style={{ color: "var(--text-muted)" }}>
              Save history in branch
            </span>
          </div>

          {/* Peeking-at-another-branch banner */}
          {peeking && (
            <div
              data-testid="git-panel-peeking"
              className="px-2.5 py-1.5 rounded-md flex items-center gap-2 text-[11px]"
              style={{ background: "var(--accent-soft-faint)", border: "1px solid var(--accent-soft-strong)", color: "var(--text-secondary)" }}
            >
              <Eye size={11} style={{ color: "var(--accent)", flexShrink: 0 }} />
              <span className="flex-1 truncate">
                Viewing <span className="font-mono">{viewBranch}</span> (not current)
              </span>
              <button
                data-testid="git-panel-peek-clear"
                onClick={() => setViewBranch(null)}
                className="shrink-0 hover:underline"
                style={{ color: "var(--accent)" }}
              >
                Show current
              </button>
            </div>
          )}

          {/* Branches departing the visible spine: peekable chips + the
              "+N elsewhere" overflow, at the top of the rail's lanes (D-A). */}
          {rail !== null && (
            <GraphRailHeader
              topChips={rail.topChips}
              overflowCount={rail.overflowCount}
              onPeek={setViewBranch}
            />
          )}

          {/* Out-of-version saves — what the next commit would fold in.
              With a rail, the box's left padding and the inter-row gap move
              into the content side so the rail cells stack contiguously. */}
          {pending.length > 0 && (
            <div
              data-testid="git-panel-pending"
              className={rail !== null ? "py-2 rounded-md" : "px-2.5 py-2 rounded-md"}
              style={{ border: "1px solid var(--border)", background: "var(--accent-soft-faint)" }}
            >
              <span
                className={`text-[10px] font-medium uppercase tracking-wider block mb-1.5${rail !== null ? " px-2.5" : ""}`}
                style={{ color: "var(--text-muted)" }}
              >
                Out-of-version saves ({pending.length}) — to fold into next milestone
              </span>
              <div className={rail !== null ? "flex flex-col pr-2.5" : "flex flex-col gap-1.5 pl-2"}>
                {pending.map((s, i) => {
                  const row = (
                    <SaveRow
                      save={s}
                      testId="git-panel-pending-save"
                      forkLinks={chipsAt(s.sha)}
                      onPeek={setViewBranch}
                      selected={selectedSha === s.sha}
                      onSelect={setSelectedSha}
                      onView={viewVersion}
                      onMove={moveVersion}
                      onContextMenu={(e) => { setSelectedSha(s.sha); openForkMenu(e, s.sha, true, s.message) }}
                    />
                  )
                  return rail !== null ? (
                    <div
                      key={s.sha}
                      className="flex"
                      onContextMenu={(e) => { setSelectedSha(s.sha); openForkMenu(e, s.sha, true, s.message) }}
                    >
                      {railCell("pending-save", s.sha, i > 0 ? SAVE_DOT_Y + SAVE_ROW_GAP : SAVE_DOT_Y)}
                      <div className={`flex-1 min-w-0 pl-2${i > 0 ? " pt-1.5" : ""}`}>{row}</div>
                    </div>
                  ) : (
                    <Fragment key={s.sha}>{row}</Fragment>
                  )
                })}
              </div>
            </div>
          )}

          {/* Milestone spine */}
          {loading && milestones.length === 0 ? (
            <div data-testid="git-panel-loading" className="py-6 text-center">
              <span className="text-[11px]" style={{ color: "var(--text-muted)" }}>
                Loading version history…
              </span>
            </div>
          ) : milestones.length === 0 ? (
            <div data-testid="git-panel-empty" className="py-6 text-center">
              <Clock size={18} className="mx-auto mb-2" style={{ color: "var(--text-muted)" }} />
              <p className="text-[12px]" style={{ color: "var(--text-secondary)" }}>
                No milestones yet.
              </p>
              <p className="text-[11px] mt-1" style={{ color: "var(--text-muted)" }}>
                Use Commit in the toolbar to record one.
              </p>
            </div>
          ) : (
            <div
              ref={milestonesBoxRef}
              data-testid="git-panel-milestones"
              className="relative rounded-md overflow-hidden"
              style={{ border: "1px solid var(--border)" }}
            >
            {/* All straight vertical rail lines, one element per contiguous
                run — phase-coherent across every row and divider below. */}
            {rail !== null && railRuns !== null && (
              <GraphRailOverlay
                runs={railRuns}
                dimmed={rail.viewedIsArchived}
                onLaneContextMenu={(branch, x, y) => setLaneMenu({ branch, x, y })}
              />
            )}
            {milestones.map((m, idx) => {
              const exp = expanded[m.sha]
              const isOpen = exp !== undefined
              return (
                <div
                  key={m.sha}
                  style={{ borderBottom: idx < milestones.length - 1 ? "1px solid var(--border)" : undefined }}
                >
                  <button
                    data-testid="git-panel-milestone"
                    data-selected={selectedSha === m.sha || undefined}
                    // While this row's fork menu is open a full-screen backdrop
                    // sits above the row, so the CSS :hover shading no longer
                    // applies — keep the row shaded explicitly so it doesn't go
                    // flat mid-menu. The selected background (accent-soft) wins.
                    data-menu-open={forkAnchor?.sha === m.sha || undefined}
                    onClick={() => toggleExpand(m.sha)}
                    onContextMenu={(e) => openForkMenu(e, m.sha, idx === 0, m.version_label || m.message)}
                    // With a rail cell as first flex child the row's vertical
                    // padding moves onto the content so lanes stack contiguously.
                    className={`w-full flex items-start gap-1.5 ${rail !== null ? "pr-3" : "px-3 py-2"} text-left transition-colors hover:bg-[var(--bg-hover)]`}
                    style={
                      selectedSha === m.sha
                        ? { background: "var(--accent-soft)" }
                        : forkAnchor?.sha === m.sha
                          ? { background: "var(--bg-hover)" }
                          : undefined
                    }
                  >
                    {railCell("milestone", m.sha, MILESTONE_DOT_Y)}
                    <span className={`mt-0.5 shrink-0${rail !== null ? " pt-2" : ""}`} style={{ color: "var(--text-muted)" }}>
                      {isOpen ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
                    </span>
                    <div className={`flex-1 min-w-0${rail !== null ? " py-2" : ""}`}>
                      <div className="flex items-baseline gap-1.5">
                        {m.is_root ? (
                          <span
                            data-testid="git-panel-milestone-init"
                            className="text-[10px] px-1 py-0.5 rounded font-mono shrink-0"
                            style={{ background: "var(--bg-hover)", color: "var(--text-secondary)", border: "1px solid var(--border)" }}
                          >
                            init
                          </span>
                        ) : (
                          m.version_label && (
                            <span
                              data-testid="git-panel-milestone-label"
                              className="text-[10px] px-1 py-0.5 rounded font-mono shrink-0"
                              style={{ background: "var(--accent-soft)", color: "var(--accent)" }}
                            >
                              {m.version_label}
                            </span>
                          )
                        )}
                        <span className="text-[12px] truncate flex-1" style={{ color: "var(--text-primary)" }}>
                          {m.message}
                        </span>
                        <ForkLinks branches={chipsAt(m.sha)} onPeek={setViewBranch} />
                        <ViewVersionButton
                          sha={m.sha}
                          label={m.version_label || m.message}
                          onView={viewVersion}
                        />
                        <MoveToVersionButton
                          sha={m.sha}
                          label={m.version_label || m.message}
                          onMove={moveVersion}
                        />
                        <span className="text-[10px] font-mono shrink-0" style={{ color: "var(--text-secondary)" }}>
                          <Tooltip label={HASH_TOOLTIP} side="bottom">
                            <span>{m.short_sha}</span>
                          </Tooltip>
                          {" · "}{timeAgo(m.timestamp)}
                        </span>
                      </div>
                    </div>
                  </button>

                  {/* Expanded saves: with a rail, each save (or the single
                      loading/no-saves placeholder) is its own flex row with a
                      rail cell, replacing the nested pl-7 container (A-12) so
                      the lane lines stay contiguous through the expansion. */}
                  {isOpen && (rail !== null ? (
                    <div className="pr-3">
                      {exp === "loading" || exp.length === 0 ? (
                        <div className="flex">
                          {railCell("placeholder", m.sha, SAVE_DOT_Y)}
                          <div className="flex-1 min-w-0 pl-[22px] pb-2">
                            <span className="text-[11px]" style={{ color: "var(--text-muted)" }}>
                              {exp === "loading"
                                ? "Loading saves…"
                                : "No individual saves recorded for this milestone."}
                            </span>
                          </div>
                        </div>
                      ) : (
                        exp.map((s, i) => (
                          <div
                            key={s.sha}
                            className="flex"
                            onContextMenu={(e) => { setSelectedSha(s.sha); openForkMenu(e, s.sha, false, s.message) }}
                          >
                            {railCell("save", s.sha, i > 0 ? SAVE_DOT_Y + SAVE_ROW_GAP : SAVE_DOT_Y)}
                            <div className={`flex-1 min-w-0 pl-[22px]${i > 0 ? " pt-1.5" : ""}${i === exp.length - 1 ? " pb-2" : ""}`}>
                              <SaveRow
                                save={s}
                                testId="git-panel-save"
                                forkLinks={chipsAt(s.sha)}
                                onPeek={setViewBranch}
                                selected={selectedSha === s.sha}
                                onSelect={setSelectedSha}
                                onView={viewVersion}
                                onMove={moveVersion}
                                onContextMenu={(e) => { setSelectedSha(s.sha); openForkMenu(e, s.sha, false, s.message) }}
                              />
                            </div>
                          </div>
                        ))
                      )}
                    </div>
                  ) : (
                    <div className="pl-7 pr-3 pb-2">
                      {exp === "loading" ? (
                        <span className="text-[11px]" style={{ color: "var(--text-muted)" }}>
                          Loading saves…
                        </span>
                      ) : exp.length === 0 ? (
                        <span className="text-[11px]" style={{ color: "var(--text-muted)" }}>
                          No individual saves recorded for this milestone.
                        </span>
                      ) : (
                        <div className="flex flex-col gap-1.5">
                          {exp.map((s) => (
                            <SaveRow
                              key={s.sha}
                              save={s}
                              testId="git-panel-save"
                              forkLinks={chipsAt(s.sha)}
                              onPeek={setViewBranch}
                              selected={selectedSha === s.sha}
                              onSelect={setSelectedSha}
                              onView={viewVersion}
                              onMove={moveVersion}
                              onContextMenu={(e) => { setSelectedSha(s.sha); openForkMenu(e, s.sha, false, s.message) }}
                            />
                          ))}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )
            })}
            </div>
          )}
        </div>
      </div>

      {/* Right-click row menu (S38). The fork actions only apply on the current
          branch's own history, so they are gated on !peeking; the view/move
          items always render so the menu opens (never falls through to the
          browser menu) on every history row, peeking or not. */}
      {forkAnchor && (
        <>
          <div
            className="fixed inset-0 z-40"
            onClick={() => setForkAnchor(null)}
            onContextMenu={(e) => { e.preventDefault(); setForkAnchor(null) }}
          />
          <div
            data-testid="git-panel-fork-menu"
            className="fixed z-50 rounded-md py-1 shadow-lg text-[12px]"
            style={{ left: forkAnchor.x, top: forkAnchor.y, background: "var(--bg-elevated)", border: "1px solid var(--border)" }}
          >
            {!forkAnchor.peeking && (
              <>
                <button
                  data-testid="git-panel-fork-here"
                  onClick={() => startFork(false)}
                  className="flex items-center gap-1.5 px-3 py-1.5 w-full text-left hover:bg-[var(--bg-hover)]"
                  style={{ color: "var(--text-primary)" }}
                >
                  <GitBranch size={12} style={{ color: "var(--accent)" }} /> New branch from here
                </button>
                {forkAnchor.canMove && (
                  <button
                    data-testid="git-panel-fork-move"
                    onClick={() => startFork(true)}
                    className="flex items-center gap-1.5 px-3 py-1.5 w-full text-left hover:bg-[var(--bg-hover)]"
                    style={{ color: "var(--text-primary)" }}
                  >
                    <ArrowRightLeft size={12} style={{ color: "var(--accent)" }} /> New branch &amp; move work here
                  </button>
                )}
              </>
            )}
            <button
              data-testid="git-panel-menu-view"
              onClick={() => { viewVersion(forkAnchor.sha, forkAnchor.label); setForkAnchor(null) }}
              className="flex items-center gap-1.5 px-3 py-1.5 w-full text-left hover:bg-[var(--bg-hover)]"
              style={{ color: "var(--text-primary)" }}
            >
              <Eye size={12} style={{ color: "var(--accent)" }} /> View side-by-side
            </button>
            <button
              data-testid="git-panel-menu-move"
              onClick={() => { moveVersion(forkAnchor.sha, forkAnchor.label); setForkAnchor(null) }}
              className="flex items-center gap-1.5 px-3 py-1.5 w-full text-left hover:bg-[var(--bg-hover)]"
              style={{ color: "var(--text-primary)" }}
            >
              <RotateCcw size={12} style={{ color: "var(--accent)" }} /> Move to this version
            </button>
          </div>
        </>
      )}

      {/* Right-click menu on a rail milestone dot: the commit actions. */}
      {dotMenu && (() => {
        const m = milestones.find((e) => e.sha === dotMenu.sha)
        const label = m ? m.version_label || m.message : dotMenu.sha.slice(0, 7)
        return (
          <>
            <div className="fixed inset-0 z-40" onClick={() => setDotMenu(null)} onContextMenu={(e) => { e.preventDefault(); setDotMenu(null) }} />
            <div
              data-testid="git-graph-dot-menu"
              className="fixed z-50 rounded-md py-1 shadow-lg text-[12px]"
              style={{ left: dotMenu.x, top: dotMenu.y, background: "var(--bg-elevated)", border: "1px solid var(--border)" }}
            >
              <button
                data-testid="git-graph-dot-menu-view"
                onClick={() => { viewVersion(dotMenu.sha, label); setDotMenu(null) }}
                className="flex items-center gap-1.5 px-3 py-1.5 w-full text-left hover:bg-[var(--bg-hover)]"
                style={{ color: "var(--text-primary)" }}
              >
                <Eye size={12} style={{ color: "var(--accent)" }} /> View side-by-side
              </button>
              <button
                data-testid="git-graph-dot-menu-move"
                onClick={() => { moveVersion(dotMenu.sha, label); setDotMenu(null) }}
                className="flex items-center gap-1.5 px-3 py-1.5 w-full text-left hover:bg-[var(--bg-hover)]"
                style={{ color: "var(--text-primary)" }}
              >
                <RotateCcw size={12} style={{ color: "var(--accent)" }} /> Move to this version
              </button>
            </div>
          </>
        )
      })()}

      {/* Right-click menu on a rail lane line: the branch actions. */}
      {laneMenu && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setLaneMenu(null)} onContextMenu={(e) => { e.preventDefault(); setLaneMenu(null) }} />
          <div
            data-testid="git-graph-lane-menu"
            className="fixed z-50 rounded-md py-1 shadow-lg text-[12px]"
            style={{ left: laneMenu.x, top: laneMenu.y, background: "var(--bg-elevated)", border: "1px solid var(--border)" }}
          >
            <div className="px-3 py-1 font-mono text-[10px] max-w-[220px] truncate" style={{ color: "var(--text-muted)" }}>
              {laneMenu.branch}
            </div>
            <button
              data-testid="git-graph-lane-menu-switch"
              onClick={() => { const b = laneMenu.branch; setLaneMenu(null); guardNavigation(() => { void performSwitch(b) }) }}
              disabled={switching || laneMenu.branch === workingBranch}
              className="flex items-center gap-1.5 px-3 py-1.5 w-full text-left hover:bg-[var(--bg-hover)] disabled:opacity-40"
              style={{ color: "var(--text-primary)" }}
            >
              <ArrowRightLeft size={12} style={{ color: "var(--accent)" }} /> Switch to this branch
            </button>
            <button
              data-testid="git-graph-lane-menu-view"
              onClick={() => { setViewBranch(laneMenu.branch === workingBranch ? null : laneMenu.branch); setLaneMenu(null) }}
              disabled={laneMenu.branch === (viewBranch ?? workingBranch)}
              className="flex items-center gap-1.5 px-3 py-1.5 w-full text-left hover:bg-[var(--bg-hover)] disabled:opacity-40"
              style={{ color: "var(--text-primary)" }}
            >
              <Eye size={12} style={{ color: "var(--accent)" }} /> View this branch
            </button>
          </div>
        </>
      )}

      {/* Naming step for the fork */}
      {forkDraft && (
        <>
          <div
            className="fixed inset-0 z-40"
            onClick={() => !forking && setForkDraft(null)}
            onContextMenu={(e) => { e.preventDefault(); if (!forking) setForkDraft(null) }}
          />
          <div
            data-testid="git-panel-fork-dialog"
            className="fixed z-50 rounded-lg p-3 w-[260px] flex flex-col gap-2 shadow-lg"
            style={{ left: forkDraft.x, top: forkDraft.y, background: "var(--bg-panel)", border: "1px solid var(--border)" }}
          >
            <span className="text-[12px] font-medium" style={{ color: "var(--text-primary)" }}>
              {forkDraft.move ? "New branch — move your work here" : "New branch from this point"}
            </span>
            <input
              autoFocus
              data-testid="git-panel-fork-name"
              value={forkDraft.name}
              onChange={(e) => setForkDraft({ ...forkDraft, name: e.target.value })}
              onKeyDown={(e) => { if (e.key === "Enter") requestForkSubmit() }}
              placeholder="New branch name…"
              className="px-2 py-1 text-[12px] rounded-md focus:outline-none focus:ring-2"
              style={{ background: "var(--bg-input)", border: "1px solid var(--border)", color: "var(--text-primary)", caretColor: "var(--accent)" }}
            />
            <div className="flex justify-end gap-2">
              <button onClick={() => setForkDraft(null)} disabled={forking} className="px-2.5 py-1 text-[12px] rounded-md" style={{ color: "var(--text-secondary)" }}>
                Cancel
              </button>
              <button
                data-testid="git-panel-fork-create"
                onClick={requestForkSubmit}
                disabled={forking || forkDraft.name.trim() === ""}
                className="px-2.5 py-1 text-[12px] font-semibold rounded-md disabled:opacity-50"
                style={{ background: "var(--structure-action)", color: "var(--text-on-accent)" }}
              >
                {forkDraft.move ? "Create & Move" : "Create"}
              </button>
            </div>
          </div>
        </>
      )}
    </PanelShell>
  )
}

// ---------------------------------------------------------------------------
// One ledger save: message + sha/time on one line, then rename-aware file changes.
// ---------------------------------------------------------------------------

function SaveRow({
  save,
  testId,
  forkLinks,
  onPeek,
  selected,
  onSelect,
  onView,
  onMove,
  onContextMenu,
}: {
  save: GitLedgerSave
  testId: string
  forkLinks?: SpawnChipBranch[]
  onPeek?: (name: string) => void
  selected?: boolean
  onSelect?: (sha: string) => void
  onView?: (sha: string, label: string) => void
  onMove?: (sha: string, label: string) => void
  onContextMenu?: (e: React.MouseEvent) => void
}) {
  return (
    <div
      data-testid={testId}
      data-selected={selected || undefined}
      onClick={onSelect ? () => onSelect(save.sha) : undefined}
      onContextMenu={onContextMenu}
      className={`flex flex-col gap-0.5 rounded px-1 -mx-1 ${onSelect ? "cursor-pointer" : ""}`}
      style={selected ? { background: "var(--accent-soft)", outline: "1px solid var(--accent-soft-strong)" } : undefined}
    >
      <div className="flex items-baseline gap-1.5">
        <span className="text-[11px] truncate flex-1" style={{ color: "var(--text-primary)" }}>
          {save.message}
        </span>
        {/* Branch chip left of the hash (inline — doesn't push filenames down,
            and aligns with the milestone rows), S38. */}
        {forkLinks && forkLinks.length > 0 && onPeek && (
          <ForkLinks branches={forkLinks} onPeek={onPeek} />
        )}
        {onView && <ViewVersionButton sha={save.sha} label={save.message} onView={onView} />}
        {onMove && <MoveToVersionButton sha={save.sha} label={save.message} onMove={onMove} />}
        <span className="text-[10px] font-mono shrink-0" style={{ color: "var(--text-secondary)" }}>
          <Tooltip label={HASH_TOOLTIP} side="bottom">
            <span>{save.short_sha}</span>
          </Tooltip>
          {" · "}{timeAgo(save.timestamp)}
        </span>
      </div>
      {save.files.length > 0 && (
        <div className="flex flex-col gap-0.5 mt-0.5 pl-1">
          {save.files.map((f) => (
            <FileRow key={`${f.status}:${f.old_path ?? ""}:${f.path}`} file={f} />
          ))}
        </div>
      )}
    </div>
  )
}

// Status code → icon + human label (tooltip) + accent colour.
const STATUS_META: Record<
  string,
  { Icon: typeof Pencil; label: string; color: string }
> = {
  M: { Icon: Pencil, label: "Modified", color: "var(--text-secondary)" },
  A: { Icon: Plus, label: "Added", color: "var(--success)" },
  D: { Icon: Minus, label: "Deleted", color: "var(--danger)" },
  R: { Icon: ArrowRightLeft, label: "Renamed", color: "var(--accent)" },
  C: { Icon: Copy, label: "Copied", color: "var(--accent)" },
}

function FileRow({ file }: { file: GitFileChange }) {
  const meta = STATUS_META[file.status] ?? {
    Icon: FileText,
    label: file.status,
    color: "var(--text-muted)",
  }
  const Icon = meta.Icon
  const isRename = file.status === "R" && file.old_path
  return (
    <div
      data-testid="git-panel-file"
      className="text-[10px] font-mono flex items-start gap-1.5"
      style={{ color: "var(--text-secondary)" }}
    >
      <Tooltip label={meta.label} side="bottom" className="shrink-0 mt-0.5">
        <Icon size={11} style={{ color: meta.color }} aria-label={meta.label} />
      </Tooltip>
      {isRename ? (
        // Old above new so the two paths line up for comparison.
        <span className="flex flex-col min-w-0">
          <span className="truncate" style={{ color: "var(--text-muted)" }}>{file.old_path}</span>
          <span className="truncate inline-flex items-center gap-0.5" style={{ color: "var(--text-secondary)" }}>
            <CornerDownRight size={9} className="shrink-0" />
            {file.path}
          </span>
        </span>
      ) : (
        <span className="truncate">{file.path}</span>
      )}
    </div>
  )
}

// Back-links from a commit to the branch(es) spawned there (S38). Rendered as
// spans (not buttons) so they can live inside the milestone's <button> row;
// clicking PEEKS the branch (view, not switch). stopPropagation keeps a milestone
// row from toggling its expansion when a link is clicked. Chips wear their
// branch's lane colour (archived chips carry the parent's colour, muted).
function ForkLinks({
  branches,
  onPeek,
}: {
  branches: SpawnChipBranch[]
  onPeek: (name: string) => void
}) {
  if (!branches.length) return null
  return (
    <span className="inline-flex flex-wrap items-center gap-1 shrink-0">
      {branches.map((b) => {
        const color =
          b.colorIndex !== undefined ? `var(--git-lane-${b.colorIndex})` : "var(--accent)"
        return (
          <span
            key={b.name}
            role="button"
            tabIndex={0}
            data-testid="git-panel-fork-link"
            data-archived={b.is_archived || undefined}
            title={b.is_archived ? `View ${b.name} (archived)` : `View ${b.name}`}
            onClick={(e) => { e.stopPropagation(); onPeek(b.name) }}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") { e.stopPropagation(); onPeek(b.name) }
            }}
            // Archived targets are partially greyed — still clearly clickable.
            className="inline-flex items-center gap-0.5 px-1 py-0.5 rounded text-[10px] font-mono max-w-[120px] cursor-pointer hover:underline"
            style={{
              background: "var(--accent-soft-faint)",
              color,
              border: `1px solid ${b.colorIndex !== undefined ? color : "var(--accent-soft-strong)"}`,
              opacity: b.is_archived ? 0.55 : 1,
            }}
          >
            <GitBranch size={9} className="shrink-0" />
            <span className="truncate">{b.name.split("/").pop() ?? b.name}</span>
          </span>
        )
      })}
    </span>
  )
}

// Eye affordance that opens the read-only side-by-side comparison on a commit
// (S11). A role="button" span (not a <button>) so it can live inside the
// milestone row's <button>; stopPropagation keeps the row from toggling.
function ViewVersionButton({
  sha,
  label,
  onView,
}: {
  sha: string
  label: string
  onView: (sha: string, label: string) => void
}) {
  return (
    <span
      role="button"
      tabIndex={0}
      data-testid="git-panel-view"
      title="View this version side-by-side"
      onClick={(e) => { e.stopPropagation(); onView(sha, label) }}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") { e.stopPropagation(); onView(sha, label) }
      }}
      // self-center (not baseline): milestone and save rows have different
      // text sizes, so baseline-aligning the icon puts it at visibly
      // different heights between the two row kinds.
      className="shrink-0 self-center inline-flex items-center justify-center p-0.5 rounded cursor-pointer hover:bg-[var(--bg-hover)]"
      style={{ color: "var(--text-muted)" }}
    >
      <Eye size={12} />
    </span>
  )
}

// Move-to-version affordance (P6 §3.4): a real checkout that materialises this
// version on the canvas (gated by the pre-move prompt). Sibling to the read-only
// Eye; same role="button" span so it can sit inside the milestone row's <button>.
function MoveToVersionButton({
  sha,
  label,
  onMove,
}: {
  sha: string
  label: string
  onMove: (sha: string, label: string) => void
}) {
  return (
    <span
      role="button"
      tabIndex={0}
      data-testid="git-panel-move"
      title="Move to this version"
      onClick={(e) => { e.stopPropagation(); onMove(sha, label) }}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") { e.stopPropagation(); onMove(sha, label) }
      }}
      // self-center for the same cross-row alignment reason as the Eye.
      className="shrink-0 self-center inline-flex items-center justify-center p-0.5 rounded cursor-pointer hover:bg-[var(--bg-hover)]"
      style={{ color: "var(--text-muted)" }}
    >
      <RotateCcw size={12} />
    </span>
  )
}

function timeAgo(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime()
  const mins = Math.floor(diff / 60000)
  if (mins < 1) return "just now"
  if (mins < 60) return `${mins}m ago`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.floor(hours / 24)
  return `${days}d ago`
}
