/**
 * Cache usage pane (ModalShell-based).
 *
 * What the shared snapshot store holds against the two budgets that decide
 * whether the next capture is kept, so a user can tell how close the cache is
 * to refusing one — and, when it refuses, which limit to raise.
 *
 * It reads `GET /api/cache/usage` once when it opens and again only when the
 * user asks. The server answers it by walking every identity, generation and
 * staging entry, which is what an admission pays, so this is deliberately an
 * explicit request rather than a poll: a surface that polled would need an
 * incremental count in the store first.
 *
 * The two budgets are independent — node outputs and input snapshots neither
 * consume nor evict one another — so they are shown side by side with no
 * combined total.
 *
 * Per `specs/frontend-shared/low-level.md` ("Cache usage pane").
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { Loader2, RefreshCw, TriangleAlert } from "lucide-react"

import ModalShell from "./ModalShell"
import { fetchCacheNodes, fetchCacheUsage } from "../api/client"
import { apiErrorMessage } from "../api/errors"
import type { CacheBudgetUsage, CacheNodeEntry, CacheNodesResponse, CacheUsageResponse } from "../api/types"
import { formatByteSize } from "../utils/formatBytes"
import { buildGraph } from "../utils/buildGraph"
import usePanelGraphContext, { toSimpleEdge, toSimpleNode } from "../hooks/usePanelGraphContext"
import useGraphStore from "../stores/useGraphStore"
import useSettingsStore from "../stores/useSettingsStore"

/** Where a budget stops being roomy. Both are about warning, not refusing. */
const NEAR_LIMIT_FRACTION = 0.75
const AT_LIMIT_FRACTION = 0.9

type BudgetDescriptor = {
  key: "node_outputs" | "input_snapshots"
  title: string
  detail: string
}

const BUDGETS: readonly BudgetDescriptor[] = [
  {
    key: "node_outputs",
    title: "Node outputs",
    detail: "Whole node results captured by previews, runs and explicit builds.",
  },
  {
    key: "input_snapshots",
    title: "Input snapshots",
    detail: "Data Input sources cached as snapshots.",
  },
]

function fractionUsed(used: number, limit: number): number {
  // The parser already rejects a non-positive limit, so this is belt and
  // braces against dividing by zero, not a case the response can produce.
  if (!(limit > 0)) return 0
  return Math.min(1, used / limit)
}

function barColor(fraction: number): string {
  if (fraction >= AT_LIMIT_FRACTION) return "var(--danger)"
  if (fraction >= NEAR_LIMIT_FRACTION) return "var(--warning-strong)"
  return "var(--accent)"
}

function UsageMeter({
  label,
  used,
  limit,
  variable,
  format,
}: {
  label: string
  used: number
  limit: number
  variable: string
  format: (value: number) => string
}) {
  const fraction = fractionUsed(used, limit)
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-[11px] font-medium" style={{ color: "var(--text-secondary)" }}>
          {label}
        </span>
        <span className="text-[11px] font-mono" style={{ color: "var(--text-primary)" }}>
          {format(used)} of {format(limit)}
        </span>
      </div>
      <div
        className="h-1.5 w-full rounded-full overflow-hidden"
        style={{ background: "var(--chrome-hover)" }}
        role="meter"
        aria-label={label}
        aria-valuenow={used}
        aria-valuemin={0}
        aria-valuemax={limit}
        aria-valuetext={`${format(used)} of ${format(limit)}`}
      >
        <div
          className="h-full rounded-full"
          style={{ width: `${fraction * 100}%`, background: barColor(fraction) }}
        />
      </div>
      {/* The variable is the remedy: it is what a user changes to raise this
          limit, and the server reports the name it actually read. */}
      <span className="text-[10px] font-mono" style={{ color: "var(--text-muted)" }}>
        {variable}
      </span>
    </div>
  )
}

function BudgetSection({
  descriptor,
  usage,
}: {
  descriptor: BudgetDescriptor
  usage: CacheBudgetUsage
}) {
  return (
    <section
      className="flex flex-col gap-2.5 rounded-lg p-3"
      style={{ background: "var(--bg-elevated)", border: "1px solid var(--border)" }}
      data-testid={`cache-budget-${descriptor.key}`}
    >
      <div className="flex flex-col gap-0.5">
        <h3 className="text-[12px] font-semibold" style={{ color: "var(--text-primary)" }}>
          {descriptor.title}
        </h3>
        <p className="text-[11px]" style={{ color: "var(--text-muted)" }}>
          {descriptor.detail}
        </p>
      </div>
      <UsageMeter
        label="Generations"
        used={usage.generations_used}
        limit={usage.generations_limit}
        variable={usage.generations_limit_variable}
        format={(value) => value.toLocaleString()}
      />
      <UsageMeter
        label="Size"
        used={usage.bytes_used}
        limit={usage.bytes_limit}
        variable={usage.bytes_limit_variable}
        format={formatByteSize}
      />
    </section>
  )
}

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

function formatRows(rows: number | null): string {
  return rows === null ? "—" : `${rows.toLocaleString()} rows`
}

function CacheRow({
  name,
  detail,
  state,
  readsDirectly = false,
  rows,
  sizeBytes,
  generations,
  testId,
}: {
  name: string
  detail?: string
  state?: CacheNodeEntry["state"]
  /** A node reading its file directly holds no cache, whatever its state. */
  readsDirectly?: boolean
  rows: number | null
  sizeBytes: number
  generations: number
  testId?: string
}) {
  return (
    <div
      className="grid grid-cols-[minmax(0,1fr)_auto_auto] items-baseline gap-x-3 px-2 py-1 rounded"
      data-testid={testId}
    >
      <div className="min-w-0">
        <span className="text-[11px] truncate block" style={{ color: "var(--text-primary)" }}>
          {name}
        </span>
        {detail && (
          <span className="text-[10px] font-mono" style={{ color: "var(--text-muted)" }}>
            {detail}
          </span>
        )}
      </div>
      {state !== undefined ? (
        <span
          className="text-[10px] whitespace-nowrap"
          style={{ color: readsDirectly ? "var(--text-muted)" : stateColor(state) }}
        >
          {/* "Cached" would be a lie for a direct read: there is no cache, the
              file is scanned where it lies. */}
          {readsDirectly ? "Read directly" : state === null ? "—" : STATE_LABELS[state]}
        </span>
      ) : (
        <span className="text-[10px] whitespace-nowrap" style={{ color: "var(--text-muted)" }}>
          {generations === 1 ? "1 generation" : `${generations} generations`}
        </span>
      )}
      <span
        className="text-[10px] font-mono text-right whitespace-nowrap tabular-nums"
        style={{ color: "var(--text-secondary)" }}
        title={formatRows(rows)}
      >
        {sizeBytes > 0 ? formatByteSize(sizeBytes) : "—"}
      </span>
    </div>
  )
}

function GroupHeading({ children }: { children: React.ReactNode }) {
  return (
    <h3
      className="text-[10px] font-semibold uppercase tracking-wide px-2 pt-1"
      style={{ color: "var(--text-muted)" }}
    >
      {children}
    </h3>
  )
}

export default function CacheSettingsModal({ onClose }: { onClose: () => void }) {
  const [usage, setUsage] = useState<CacheUsageResponse | null>(null)
  const [nodes, setNodes] = useState<CacheNodesResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const abortRef = useRef<AbortController | null>(null)

  const { allNodes } = usePanelGraphContext()
  const activeSource = useSettingsStore((s) => s.activeSource)

  // The node report is keyed by node id; the label is the canvas's business,
  // and a node the store holds but the canvas no longer has keeps its id.
  const labels = useMemo(
    () => new Map(allNodes.map((node) => [node.id, String(node.data.label || node.id)])),
    [allNodes],
  )

  const request = useCallback(
    (controller: AbortController) => {
      // Read the graph from the store at the moment of the request rather than
      // subscribing to it. Subscribing would make any edit, websocket resync or
      // load re-issue both requests while the pane is open — each of which
      // costs the server what admitting a capture costs — which is exactly the
      // polling this surface is specified not to do.
      const { nodes: storeNodes, edges, submodels, preamble } = useGraphStore.getState()
      const graph = buildGraph(
        storeNodes.map(toSimpleNode),
        edges.map(toSimpleEdge),
        submodels,
        preamble,
      )
      return Promise.all([
        fetchCacheUsage({ signal: controller.signal }),
        fetchCacheNodes({ graph, source: activeSource }, { signal: controller.signal }),
      ])
    },
    [activeSource],
  )

  const read = useCallback(
    (controller: AbortController) => {
      request(controller)
        .then(([usageResponse, nodesResponse]) => {
          if (controller.signal.aborted) return
          setUsage(usageResponse)
          setNodes(nodesResponse)
          setLoading(false)
        })
        .catch((cause: unknown) => {
          if (controller.signal.aborted) return
          // Keep the last good numbers on screen: stale usage beside an explicit
          // error is more use than an empty pane.
          setError(apiErrorMessage(cause, "Could not read the cache usage."))
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
    <ModalShell ariaLabel="Cache usage" onClose={onClose} width="w-[460px]" testId="cache-settings">
      <div className="flex flex-col gap-3 p-4 max-h-[85vh]">
        <div className="flex items-start justify-between gap-3 shrink-0">
          <div className="flex flex-col gap-0.5">
            <h2 className="text-[13px] font-semibold" style={{ color: "var(--text-primary)" }}>
              Cache usage
            </h2>
            <p className="text-[11px]" style={{ color: "var(--text-muted)" }}>
              What the cache holds against the limits that decide whether the next capture is
              kept. The two budgets are separate: neither evicts the other.
            </p>
          </div>
          <button
            data-testid="cache-usage-refresh"
            onClick={refresh}
            disabled={loading}
            className="toolbar-btn shrink-0 px-2 py-1 text-[11px] font-medium rounded-md flex items-center gap-1"
            /* Counting the store costs what admitting a capture costs, so this
               is read when it is asked for and never on a timer. */
            title="Count the store again"
          >
            {loading
              ? <Loader2 size={12} className="animate-spin" aria-hidden="true" />
              : <RefreshCw size={12} aria-hidden="true" />}
            Refresh
          </button>
        </div>

        {/* Everything below the title scrolls: a large pipeline's node list is
            longer than any viewport, and the Refresh control must stay put. */}
        <div className="flex flex-col gap-3 overflow-y-auto -mr-1 pr-1">
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

        {usage
          ? BUDGETS.map((descriptor) => (
              <BudgetSection key={descriptor.key} descriptor={descriptor} usage={usage[descriptor.key]} />
            ))
          : !error && (
              <p className="text-[11px] py-6 text-center" style={{ color: "var(--text-muted)" }}>
                Counting the cache…
              </p>
            )}

        {nodes && (
          <section
            className="flex flex-col gap-0.5 rounded-lg py-2"
            style={{ background: "var(--bg-elevated)", border: "1px solid var(--border)" }}
            data-testid="cache-node-list"
          >
            <GroupHeading>This pipeline · {nodes.source}</GroupHeading>
            {nodes.nodes.length === 0 && (
              <p className="text-[11px] px-2 py-1" style={{ color: "var(--text-muted)" }}>
                This pipeline has no nodes.
              </p>
            )}
            {nodes.nodes.map((node) => (
              <CacheRow
                key={node.node_id}
                testId={`cache-node-${node.node_id}`}
                name={labels.get(node.node_id) ?? node.node_id}
                detail={
                  node.unavailable_reason
                    ?? (node.reads_from
                      ? `reads ${labels.get(node.reads_from) ?? node.reads_from}`
                      : node.shares_snapshot_with.length > 0
                        ? `${node.size_bytes > 0 ? "shared with" : "same source as"} ${node.shares_snapshot_with
                            .map((other) => labels.get(other) ?? other)
                            .join(", ")}`
                        : node.generations > 1
                          ? `${node.generations} generations`
                          : undefined)
                }
                state={node.state}
                readsDirectly={node.reads_directly}
                rows={node.row_count}
                sizeBytes={node.size_bytes}
                generations={node.generations}
              />
            ))}

            {nodes.other.length > 0 && (
              <>
                <GroupHeading>Not in this pipeline</GroupHeading>
                {nodes.other.map((owner, index) => (
                  <CacheRow
                    key={`${owner.bucket}:${owner.label}:${owner.source ?? ""}:${index}`}
                    testId={`cache-other-${owner.label}`}
                    name={owner.label}
                    detail={owner.source ?? "input snapshot"}
                    rows={owner.row_count}
                    sizeBytes={owner.size_bytes}
                    generations={owner.generations}
                  />
                ))}
              </>
            )}

            {(nodes.unattributed_bytes > 0 || nodes.unmarked_identities > 0) && (
              <p
                className="text-[10px] px-2 pt-1.5 leading-relaxed"
                style={{ color: "var(--text-muted)" }}
                data-testid="cache-node-footnotes"
              >
                {nodes.unattributed_bytes > 0 && (
                  <>
                    {formatByteSize(nodes.unattributed_bytes)} could not be attributed to any
                    node.{" "}
                  </>
                )}
                {nodes.unmarked_identities > 0 && (
                  <>
                    {nodes.unmarked_identities} cached{" "}
                    {nodes.unmarked_identities === 1 ? "entry carries" : "entries carry"} no
                    provider marker, so {nodes.unmarked_identities === 1 ? "it is" : "they are"}{" "}
                    counted against both budgets above — which is why the bars can exceed what is
                    listed here.
                  </>
                )}
              </p>
            )}
          </section>
        )}
        </div>
      </div>
    </ModalShell>
  )
}
