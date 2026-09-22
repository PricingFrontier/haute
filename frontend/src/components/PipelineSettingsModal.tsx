/**
 * Pipeline settings pane (ModalShell-based), per
 * `specs/frontend-shared/low-level.md` ("Pipeline settings"): the calculation
 * mode, the preview row limit and streaming chunk size, then the cached-data
 * inventory.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { Loader2, RefreshCw, TriangleAlert, X } from "lucide-react"

import ModalShell from "./ModalShell"
import { clearCacheIdentities, fetchCacheNodes } from "../api/client"
import { apiErrorMessage } from "../api/errors"
import type { CacheNodeEntry, CacheNodesResponse } from "../api/types"
import { formatByteSize } from "../utils/formatBytes"
import { buildGraph } from "../utils/buildGraph"
import usePanelGraphContext, { toSimpleEdge, toSimpleNode } from "../hooks/usePanelGraphContext"
import useGraphStore from "../stores/useGraphStore"
import useSettingsStore, { MAX_STREAMING_CHUNK_SIZE, MIN_STREAMING_CHUNK_SIZE } from "../stores/useSettingsStore"
import useUIStore, { type CalculationMode } from "../stores/useUIStore"

/** What each point state is called, and whether it wants the user's attention. */
const STATE_LABELS: Record<NonNullable<CacheNodeEntry["state"]>, string> = {
  current: "Cached",
  stale: "Out of date",
  partial: "Some columns",
  missing: "Not cached",
  building: "Caching",
  corrupt: "Unreadable",
}

function stateColor(state: CacheNodeEntry["state"]): string {
  if (state === "current") return "var(--success)"
  if (state === "stale" || state === "partial") return "var(--warning-strong)"
  if (state === "corrupt") return "var(--danger)"
  return "var(--text-muted)"
}

function displayNodeName(name: string): string {
  return name.replace(/^submodel_runtime\//, "")
}

/** A duration a person reads at a glance: "0.4 s", "9.9 s", "2m 14s". */
function formatDuration(seconds: number | null): string {
  if (seconds === null) return "-"
  if (seconds < 10) return `${seconds.toFixed(1)} s`
  if (seconds < 60) return `${Math.round(seconds)} s`
  const minutes = Math.floor(seconds / 60)
  return `${minutes}m ${String(Math.round(seconds - minutes * 60)).padStart(2, "0")}s`
}

/** When it was cached, short enough for a column: "19 Sep 23:10". */
function formatCachedAt(seconds: number | null): string {
  if (seconds === null) return "-"
  return new Date(seconds * 1000).toLocaleString([], {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  })
}

/** The one grid the header and every row share, so the columns line up. */
const ROW_GRID =
  "grid grid-cols-[minmax(0,1fr)_84px_68px_104px_52px_20px] items-baseline gap-x-2"

function TableHeader() {
  return (
    <div
      className={`${ROW_GRID} px-2.5 pb-1 text-[9px] font-semibold uppercase tracking-wide`}
      style={{ color: "var(--text-muted)" }}
    >
      <span>Node</span>
      <span>Status</span>
      <span className="text-right">Size</span>
      <span className="text-right">Cached</span>
      <span className="text-right">Time</span>
      <span aria-hidden="true" />
    </div>
  )
}

function SectionRow({ children }: { children: React.ReactNode }) {
  return (
    <div
      className="px-2.5 py-1 mt-1 text-[9px] font-semibold uppercase tracking-wide"
      style={{ color: "var(--text-muted)", background: "var(--chrome-hover)" }}
    >
      {children}
    </div>
  )
}

function CacheRow({
  name,
  detail,
  status,
  statusColor,
  sizeBytes,
  cachedAt,
  buildSeconds,
  rows,
  onClear,
  clearing,
  testId,
}: {
  name: string
  detail?: string
  status: string
  statusColor: string
  sizeBytes: number
  cachedAt: number | null
  buildSeconds: number | null
  rows: number | null
  /** Absent when the row carries nothing: there is then nothing to clear. */
  onClear?: () => void
  clearing?: boolean
  testId?: string
}) {
  return (
    <div
      className={`${ROW_GRID} px-2.5 py-1 rounded hover:bg-[var(--chrome-hover)] group`}
      data-testid={testId}
    >
      <div className="min-w-0">
        <span className="text-[11px] truncate block" style={{ color: "var(--text-primary)" }}>
          {name}
        </span>
        {detail && (
          <span
            className="text-[9px] font-mono truncate block"
            style={{ color: "var(--text-muted)" }}
          >
            {detail}
          </span>
        )}
      </div>
      <span className="text-[10px] truncate" style={{ color: statusColor }}>
        {status}
      </span>
      <span
        className="text-[10px] font-mono tabular-nums text-right"
        style={{ color: "var(--text-secondary)" }}
        title={rows === null ? undefined : `${rows.toLocaleString()} rows`}
      >
        {sizeBytes > 0 ? formatByteSize(sizeBytes) : "-"}
      </span>
      <span
        className="text-[10px] font-mono tabular-nums text-right whitespace-nowrap"
        style={{ color: "var(--text-muted)" }}
      >
        {formatCachedAt(cachedAt)}
      </span>
      <span
        className="text-[10px] font-mono tabular-nums text-right"
        style={{ color: "var(--text-muted)" }}
      >
        {formatDuration(buildSeconds)}
      </span>
      {onClear ? (
        <button
          data-testid={testId ? `${testId}-clear` : undefined}
          onClick={onClear}
          disabled={clearing}
          /* Always visible, not revealed on hover: a control nobody can see is
             a control nobody knows they have. It is dimmed until the row is
             hovered so a long table does not read as a column of red. It shows
             only on a row that carries bytes, so it always has something to do
             and always does exactly what the row reports. */
          className="justify-self-end rounded p-0.5 transition-opacity opacity-50 group-hover:opacity-100 focus-visible:opacity-100 disabled:opacity-30"
          style={{ color: "var(--danger)" }}
          aria-label={`Clear the cache for ${name}`}
          title={`Clear the cache for ${name} - ${formatByteSize(sizeBytes)}`}
        >
          {clearing ? (
            <Loader2 size={12} className="animate-spin" aria-hidden="true" />
          ) : (
            <X size={12} aria-hidden="true" />
          )}
        </button>
      ) : (
        <span aria-hidden="true" />
      )}
    </div>
  )
}

/** One labelled numeric setting: label and hint on the left, field on the right. */
function NumberSetting({
  id,
  label,
  hint,
  children,
}: {
  id: string
  label: string
  hint: string
  children: React.ReactNode
}) {
  return (
    <div className="flex items-center justify-between gap-4 px-2.5 py-1.5">
      <div className="flex flex-col gap-0.5 min-w-0">
        <label htmlFor={id} className="text-[11px] font-medium" style={{ color: "var(--text-primary)" }}>
          {label}
        </label>
        <span className="text-[10px]" style={{ color: "var(--text-muted)" }}>{hint}</span>
      </div>
      {children}
    </div>
  )
}

const NUMBER_INPUT_CLASS =
  "toolbar-number-input shrink-0 w-28 px-1.5 h-[26px] text-[12px] font-mono rounded text-right focus:outline-none"
const NUMBER_INPUT_STYLE = {
  background: "var(--chrome-hover)",
  border: "1px solid var(--chrome-border)",
  color: "var(--text-primary)",
}

function PreviewSettings() {
  const rowLimit = useSettingsStore((s) => s.rowLimit)
  const setRowLimit = useSettingsStore((s) => s.setRowLimit)
  const streamingChunkSize = useSettingsStore((s) => s.streamingChunkSize)
  const setStreamingChunkSize = useSettingsStore((s) => s.setStreamingChunkSize)

  return (
    <section
      className="flex flex-col rounded-lg py-1"
      style={{ background: "var(--bg-elevated)", border: "1px solid var(--border)" }}
      data-testid="pipeline-settings-preview"
    >
      <NumberSetting
        id="pipeline-settings-rows"
        label="Preview rows"
        hint="Rows shown in a node preview. 0 means no limit."
      >
        <input
          id="pipeline-settings-rows"
          type="number"
          min={0}
          step={100}
          value={rowLimit}
          onChange={(e) => setRowLimit(Math.max(0, parseInt(e.target.value) || 0))}
          className={NUMBER_INPUT_CLASS}
          style={NUMBER_INPUT_STYLE}
        />
      </NumberSetting>
      <NumberSetting
        id="pipeline-settings-chunk"
        label="Chunk rows"
        hint="Rows per streaming chunk. Lower this if a wide dataset runs out of memory."
      >
        <input
          id="pipeline-settings-chunk"
          type="number"
          min={MIN_STREAMING_CHUNK_SIZE}
          max={MAX_STREAMING_CHUNK_SIZE}
          step={10000}
          value={streamingChunkSize}
          onChange={(e) => {
            const raw = e.target.value.trim()
            if (raw === "") return
            const parsed = Number(raw)
            if (!Number.isFinite(parsed)) return
            setStreamingChunkSize(parsed)
          }}
          className={NUMBER_INPUT_CLASS}
          style={NUMBER_INPUT_STYLE}
        />
      </NumberSetting>
    </section>
  )
}

const CALCULATION_MODES: { mode: CalculationMode; label: string; hint: string }[] = [
  {
    mode: "automatic",
    label: "Automatic",
    hint: "Clicking a node calculates its preview.",
  },
  {
    mode: "manual",
    label: "Manual",
    hint: "Clicking a node shows its last result. Press Refresh (Ctrl+Enter) to calculate.",
  },
]

function CalculationSettings() {
  const calculationMode = useUIStore((s) => s.calculationMode)
  const setCalculationMode = useUIStore((s) => s.setCalculationMode)
  const groupRef = useRef<HTMLDivElement>(null)

  // Radio-group keyboard contract: one tab stop, arrows move and select.
  const onKeyDown = (e: React.KeyboardEvent) => {
    const step = e.key === "ArrowRight" || e.key === "ArrowDown" ? 1
      : e.key === "ArrowLeft" || e.key === "ArrowUp" ? -1
      : 0
    if (step === 0) return
    e.preventDefault()
    const index = CALCULATION_MODES.findIndex((m) => m.mode === calculationMode)
    const next = (index + step + CALCULATION_MODES.length) % CALCULATION_MODES.length
    setCalculationMode(CALCULATION_MODES[next].mode)
    groupRef.current?.querySelectorAll<HTMLButtonElement>('[role="radio"]')[next]?.focus()
  }

  return (
    <div
      ref={groupRef}
      role="radiogroup"
      aria-label="Calculation"
      onKeyDown={onKeyDown}
      className="grid grid-cols-2 gap-2"
      data-testid="pipeline-settings-calculation"
    >
      {CALCULATION_MODES.map(({ mode, label, hint }) => {
        const selected = calculationMode === mode
        return (
          <button
            key={mode}
            type="button"
            role="radio"
            aria-checked={selected}
            tabIndex={selected ? 0 : -1}
            data-testid={`calculation-mode-${mode}`}
            onClick={() => setCalculationMode(mode)}
            className="flex flex-col items-start gap-0.5 rounded-lg px-2.5 py-2 text-left transition-colors"
            style={{
              background: selected ? "var(--accent-soft)" : "var(--bg-elevated)",
              border: `1px solid ${selected ? "var(--accent)" : "var(--border)"}`,
            }}
          >
            <span
              className="text-[12px] font-medium"
              style={{ color: selected ? "var(--text-accent)" : "var(--text-primary)" }}
            >
              {label}
            </span>
            <span className="text-[10px]" style={{ color: "var(--text-muted)" }}>{hint}</span>
          </button>
        )
      })}
    </div>
  )
}

function SectionHeading({ children }: { children: React.ReactNode }) {
  return (
    <h3 className="text-[11px] font-semibold" style={{ color: "var(--text-secondary)" }}>
      {children}
    </h3>
  )
}

export default function PipelineSettingsModal({ onClose }: { onClose: () => void }) {
  const [nodes, setNodes] = useState<CacheNodesResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  // The row being cleared, so its own control shows the work and no other row
  // is disabled by it.
  const [clearing, setClearing] = useState<string | null>(null)
  const abortRef = useRef<AbortController | null>(null)

  const { allNodes } = usePanelGraphContext()
  const activeSource = useSettingsStore((s) => s.activeSource)

  // The node report is keyed by node id; the label is the canvas's business,
  // and a node the store holds but the canvas no longer has keeps its id.
  const labels = useMemo(
    () => new Map(allNodes.map((node) => [node.id, String(node.data.label || node.id)])),
    [allNodes],
  )
  const nodeName = (id: string) => displayNodeName(labels.get(id) ?? id)

  const request = useCallback(
    (controller: AbortController) => {
      // Read the graph from the store at the moment of the request rather than
      // subscribing to it. Subscribing would make any edit, websocket resync or
      // load re-issue the request while the pane is open.
      const { nodes: storeNodes, edges, submodels, preamble } = useGraphStore.getState()
      const graph = buildGraph(
        storeNodes.map(toSimpleNode),
        edges.map(toSimpleEdge),
        submodels,
        preamble,
      )
      return fetchCacheNodes({ graph, source: activeSource }, { signal: controller.signal })
    },
    [activeSource],
  )

  const read = useCallback(
    (controller: AbortController) => {
      request(controller)
        .then((nodesResponse) => {
          if (controller.signal.aborted) return
          setNodes(nodesResponse)
          setLoading(false)
        })
        .catch((cause: unknown) => {
          if (controller.signal.aborted) return
          // Keep the last good inventory beside the explicit error.
          setError(apiErrorMessage(cause, "Could not read cached data."))
          setLoading(false)
        })
    },
    [request],
  )

  const refresh = useCallback(() => {
    // Refresh is disabled while a read is in flight, so there is nothing to
    // supersede today; aborting anyway keeps this correct on its own terms
    // rather than on the button's.
    abortRef.current?.abort()
    const controller = new AbortController()
    abortRef.current = controller
    setLoading(true)
    setError(null)
    read(controller)
  }, [read])

  const clearRow = useCallback(
    (rowKey: string, digests: string[]) => {
      if (digests.length === 0) return
      setClearing(rowKey)
      setError(null)
      clearCacheIdentities(digests)
        .then(() => {
          // Re-read rather than patch the row: the server owns the inventory.
          const controller = new AbortController()
          abortRef.current?.abort()
          abortRef.current = controller
          read(controller)
        })
        .catch((cause: unknown) => {
          setError(apiErrorMessage(cause, "Could not clear that cache."))
        })
        .finally(() => setClearing(null))
    },
    [read],
  )

  useEffect(() => {
    // The opening read sets no state of its own: the pane already starts
    // loading with no error, and only the response changes that.
    const controller = new AbortController()
    abortRef.current = controller
    read(controller)
    // Abort whichever read is in flight when the pane closes — the latest,
    // which after a Refresh is not the one this effect started.
    return () => abortRef.current?.abort()
  }, [read])

  return (
    <ModalShell ariaLabel="Pipeline settings" onClose={onClose} width="w-[620px]" testId="pipeline-settings">
      <div className="flex flex-col gap-3 p-4 max-h-[85vh]">
        <h2 className="text-[13px] font-semibold shrink-0" style={{ color: "var(--text-primary)" }}>
          Pipeline settings
        </h2>

        {/* Everything below the title scrolls: a large pipeline's node list is
            longer than any viewport. */}
        <div className="flex flex-col gap-3 overflow-y-auto -mr-1 pr-1">
        <div className="flex flex-col gap-1.5">
          <SectionHeading>Calculation</SectionHeading>
          <CalculationSettings />
        </div>

        <div className="flex flex-col gap-1.5">
          <SectionHeading>Preview</SectionHeading>
          <PreviewSettings />
        </div>

        <div className="flex items-center justify-between gap-3">
          <SectionHeading>Cached data</SectionHeading>
          <button
            data-testid="cache-usage-refresh"
            onClick={refresh}
            disabled={loading}
            className="toolbar-btn shrink-0 px-2 py-1 text-[11px] font-medium rounded-md flex items-center gap-1"
            title="Refresh cached data"
          >
            {loading
              ? <Loader2 size={12} className="animate-spin" aria-hidden="true" />
              : <RefreshCw size={12} aria-hidden="true" />}
            Refresh
          </button>
        </div>

        {error && (
          <p
            data-testid="cache-usage-error"
            role="alert"
            className="rounded-md px-2.5 py-2 text-[11px] flex items-start gap-1.5"
            style={{
              background: "var(--danger-soft)",
              color: "var(--danger-text)",
              border: "1px solid var(--danger-border)",
            }}
          >
            <TriangleAlert size={13} className="shrink-0 mt-px" aria-hidden="true" />
            {error}
          </p>
        )}

        {!nodes && !error && (
          <p className="text-[11px] py-6 text-center" style={{ color: "var(--text-muted)" }}>
            Loading cached data…
          </p>
        )}

        {nodes && (
          <section
            className="flex flex-col rounded-lg py-2 overflow-hidden"
            style={{ background: "var(--bg-elevated)", border: "1px solid var(--border)" }}
            data-testid="cache-node-list"
          >
            <TableHeader />
            {nodes.nodes.length === 0 && (
              <p className="text-[11px] px-2.5 py-1" style={{ color: "var(--text-muted)" }}>
                This pipeline has no nodes.
              </p>
            )}
            {nodes.nodes.map((node) => (
              <CacheRow
                key={node.node_id}
                testId={`cache-node-${node.node_id}`}
                name={nodeName(node.node_id)}
                detail={
                  node.unavailable_reason
                    ?? (node.reads_from
                      ? `reads ${nodeName(node.reads_from)}`
                      : node.shares_snapshot_with.length > 0
                        ? `${node.size_bytes > 0 ? "shared with" : "same source as"} ${node.shares_snapshot_with
                            .map(nodeName)
                            .join(", ")}`
                        : undefined)
                }
                /* "Cached" would be a lie for a direct read: there is no cache,
                   the file is scanned where it lies. */
                status={
                  node.reads_directly
                    ? "Read directly"
                    : node.state === null
                      ? "-"
                      : STATE_LABELS[node.state]
                }
                statusColor={node.reads_directly ? "var(--text-muted)" : stateColor(node.state)}
                rows={node.row_count}
                sizeBytes={node.size_bytes}
                cachedAt={node.newest_created_at}
                buildSeconds={node.build_seconds}
                onClear={
                  node.identity_digests.length > 0
                    ? () => clearRow(node.node_id, node.identity_digests)
                    : undefined
                }
                clearing={clearing === node.node_id}
              />
            ))}

            {nodes.other.length > 0 && (
              <>
                <SectionRow>Not in this pipeline</SectionRow>
                {nodes.other.map((owner, index) => (
                  <CacheRow
                    key={`${owner.bucket}:${owner.label}:${owner.source ?? ""}:${index}`}
                    testId={`cache-other-${owner.label}`}
                    name={owner.bucket === "node_output" ? displayNodeName(owner.label) : owner.label}
                    detail={owner.source ?? "input snapshot"}
                    status="Stored"
                    statusColor="var(--text-muted)"
                    rows={owner.row_count}
                    sizeBytes={owner.size_bytes}
                    cachedAt={owner.newest_created_at}
                    buildSeconds={owner.build_seconds}
                    onClear={
                      owner.identity_digests.length > 0
                        ? () => clearRow(`other:${index}`, owner.identity_digests)
                        : undefined
                    }
                    clearing={clearing === `other:${index}`}
                  />
                ))}
              </>
            )}

            {nodes.unattributed_bytes > 0 && (
              <p
                className="text-[10px] px-2.5 pt-2 leading-relaxed"
                style={{ color: "var(--text-muted)" }}
                data-testid="cache-node-footnotes"
              >
                {formatByteSize(nodes.unattributed_bytes)} could not be attributed to any node.
              </p>
            )}
          </section>
        )}
        </div>
      </div>
    </ModalShell>
  )
}
