/**
 * Relationships pane of the Explore preview: how strongly chosen features move
 * a target, and whether chosen key columns identify a row, over the whole data
 * point the node reads.
 *
 * The choices are this pane's own state, not node config: asking is an
 * investigation, and every answer is cached by the server for the data version
 * and the exact question, so asking again is cheap.
 */

import { ChevronDown, ChevronRight } from "lucide-react"
import { Fragment, useMemo, useState } from "react"

import {
  getExploreRelationships,
  type ExploreKeyCheck,
  type ExploreRelationship,
  type ExploreRelationshipsResponse,
} from "../../api/exploreRelationships"
import type { ExploreColumnStat } from "../../api/types"
import { NODE_GROUP_COLORS } from "../../theme/colors"
import { formatChartNumber } from "../../utils/chartHelpers"
import type { SimpleEdge, SimpleNode } from "../editors"
import useWholeDataAnswer from "../editors/shared/useWholeDataAnswer"
import type { ExploreDataView } from "./exploreDataView"

/** The server's bounds: features per question and columns per key. */
const FEATURE_LIMIT = 50
const KEY_COLUMN_LIMIT = 8

const MUTED_STYLE = { color: "var(--text-muted)" } as const
const PRIMARY_STYLE = { color: "var(--text-primary)" } as const
const SECONDARY_STYLE = { color: "var(--text-secondary)" } as const
const LABEL_CLASS = "text-[10px] font-bold uppercase tracking-[0.08em]"
const CELL_CLASS = "px-2 py-1.5"
const CARD_STYLE = { background: "var(--bg-elevated)", border: "1px solid var(--border)" } as const
const SELECT_STYLE = {
  background: "var(--bg-input)",
  border: "1px solid var(--border)",
  color: "var(--text-primary)",
} as const

function canBeTarget(column: ExploreColumnStat): boolean {
  return column.kind === "Numeric" || column.kind === "Boolean"
}

function canBeGrouped(column: ExploreColumnStat): boolean {
  return column.kind !== "Nested" && column.kind !== "Other"
}

function toggle(list: string[], name: string, limit: number): string[] {
  if (list.includes(name)) return list.filter((entry) => entry !== name)
  return list.length >= limit ? list : [...list, name]
}

function ColumnChecklist({
  label,
  columns,
  selected,
  limit,
  onToggle,
  disabledReason,
}: {
  label: string
  columns: ExploreColumnStat[]
  selected: string[]
  limit: number
  onToggle: (name: string) => void
  disabledReason?: string
}) {
  return (
    <fieldset className="space-y-1 min-w-0">
      <legend className={LABEL_CLASS} style={SECONDARY_STYLE}>
        {label}{" "}
        <span style={MUTED_STYLE}>
          ({selected.length}/{limit})
        </span>
      </legend>
      {disabledReason ? (
        <p className="text-[11px]" style={MUTED_STYLE}>
          {disabledReason}
        </p>
      ) : (
        <div className="max-h-40 overflow-y-auto rounded-md p-1.5 space-y-0.5" style={SELECT_STYLE}>
          {columns.map((column) => {
            const checked = selected.includes(column.name)
            return (
              <label key={column.name} className="flex items-center gap-1.5 text-[11px] font-mono" style={PRIMARY_STYLE}>
                <input
                  type="checkbox"
                  checked={checked}
                  disabled={!checked && selected.length >= limit}
                  onChange={() => onToggle(column.name)}
                />
                <span className="truncate" title={column.name}>
                  {column.name}
                </span>
              </label>
            )
          })}
        </div>
      )}
    </fieldset>
  )
}

function StrengthBar({ strength }: { strength: number }) {
  const percent = Math.round(strength * 1000) / 10
  return (
    <span className="inline-flex items-center gap-1.5">
      <span
        className="inline-block h-1.5 w-20 rounded-full overflow-hidden"
        style={{ background: "var(--bg-input)" }}
        aria-hidden="true"
      >
        <span
          className="block h-full"
          style={{ width: `${percent}%`, background: NODE_GROUP_COLORS.explore }}
        />
      </span>
      <span className="tabular-nums">{percent}%</span>
    </span>
  )
}

function RelationshipRows({ relationship }: { relationship: ExploreRelationship }) {
  const [open, setOpen] = useState(false)
  return (
    <Fragment>
      <tr data-testid="explore-relationship-row" style={{ borderBottom: "1px solid var(--border)" }}>
        <td className={CELL_CLASS}>
          <button
            type="button"
            onClick={() => setOpen((value) => !value)}
            aria-expanded={open}
            aria-label={`${open ? "Hide" : "Show"} levels of ${relationship.feature}`}
            className="inline-flex items-center gap-1 font-mono"
            style={PRIMARY_STYLE}
          >
            {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
            {relationship.feature}
          </button>
        </td>
        <td className={CELL_CLASS} style={SECONDARY_STYLE}>
          {relationship.kind}
        </td>
        <td className={CELL_CLASS} style={PRIMARY_STYLE}>
          <StrengthBar strength={relationship.strength} />
        </td>
        <td className={CELL_CLASS} style={SECONDARY_STYLE}>
          {relationship.levels.length}
          {relationship.levels_truncated ? " (top levels)" : ""}
        </td>
      </tr>
      {open && (
        <tr>
          <td colSpan={4} className="px-6 pb-2">
            <table className="w-full text-[11px]" aria-label={`Levels of ${relationship.feature}`}>
              <thead>
                <tr>
                  {["Level", "Rows", "Weight", "Target mean"].map((header) => (
                    <th key={header} className={`${LABEL_CLASS} text-left px-2 py-1`} style={SECONDARY_STYLE}>
                      {header}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {relationship.levels.map((level, index) => (
                  <tr key={`${level.kind}-${level.label}-${index}`}>
                    <td
                      className="px-2 py-1 font-mono"
                      style={level.kind === "value" || level.kind === "bin" ? PRIMARY_STYLE : MUTED_STYLE}
                    >
                      {level.label}
                    </td>
                    <td className="px-2 py-1 tabular-nums" style={PRIMARY_STYLE}>
                      {level.rows.toLocaleString()}
                    </td>
                    <td className="px-2 py-1 tabular-nums" style={PRIMARY_STYLE}>
                      {formatChartNumber(level.weight)}
                    </td>
                    <td className="px-2 py-1 tabular-nums" style={PRIMARY_STYLE}>
                      {level.target_mean === null ? "-" : formatChartNumber(level.target_mean)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </td>
        </tr>
      )}
    </Fragment>
  )
}

function keyCheckText(check: ExploreKeyCheck): string {
  const key = check.columns.join(" + ")
  const counts = `${check.distinct_keys.toLocaleString()} distinct of ${check.rows.toLocaleString()} rows`
  if (check.unique) return `${key} is unique: ${counts}.`
  const problems = [
    check.duplicate_rows > 0 ? `${check.duplicate_rows.toLocaleString()} duplicate rows` : null,
    check.null_key_rows > 0 ? `${check.null_key_rows.toLocaleString()} rows with a missing key value` : null,
  ].filter((problem): problem is string => problem !== null)
  return `${key} is not unique: ${problems.join(", ")} (${counts}).`
}

export default function ExploreRelationshipsPane({
  node,
  allNodes,
  edges,
  submodels,
  preamble,
  report,
}: {
  node: SimpleNode
  allNodes: SimpleNode[]
  edges: SimpleEdge[]
  submodels?: Record<string, unknown>
  preamble?: string
  report: ExploreDataView | null
}) {
  const columns = useMemo(() => report?.columns ?? [], [report])
  const [target, setTarget] = useState("")
  const [weight, setWeight] = useState("")
  const [features, setFeatures] = useState<string[]>([])
  const [keyColumns, setKeyColumns] = useState<string[]>([])

  const targetColumns = columns.filter(canBeTarget)
  const weightColumns = columns.filter((column) => column.kind === "Numeric" && column.name !== target)
  const featureColumns = columns.filter(
    (column) => canBeGrouped(column) && column.name !== target && column.name !== weight,
  )
  // A column that stopped being eligible (the target moved onto it) drops out
  // of the question rather than being sent for the server to refuse.
  const askedFeatures = target ? features.filter((name) => featureColumns.some((column) => column.name === name)) : []
  const askedFor =
    askedFeatures.length > 0 || keyColumns.length > 0
      ? JSON.stringify({
          target: target || null,
          weight: weight || null,
          features: askedFeatures,
          key_columns: keyColumns,
        })
      : null

  const { cache, answer, cacheRequired, loading, error } = useWholeDataAnswer<ExploreRelationshipsResponse>({
    node,
    allNodes,
    edges,
    submodels,
    preamble,
    askedFor,
    ask: ({ asked, graph, nodeId, source, signal }) => {
      const question = JSON.parse(asked) as {
        target: string | null
        weight: string | null
        features: string[]
        key_columns: string[]
      }
      return getExploreRelationships({ graph, node_id: nodeId, source, ...question, signal })
    },
    failureMessage: "the relationships could not be read",
  })

  if (!report) {
    return (
      <div className="flex-1 flex items-center justify-center p-4 text-xs" style={MUTED_STYLE}>
        Cache and profile this data to analyse relationships.
      </div>
    )
  }

  return (
    <div className="flex-1 min-h-0 overflow-auto p-3 space-y-3" data-testid="explore-relationships-pane">
      <div className="rounded-lg p-3 grid gap-3 md:grid-cols-2" style={CARD_STYLE}>
        <div className="space-y-2">
          <label className="block space-y-1">
            <span className={LABEL_CLASS} style={SECONDARY_STYLE}>
              Target
            </span>
            <select
              aria-label="Target"
              value={target}
              onChange={(event) => {
                setTarget(event.target.value)
                if (event.target.value === weight) setWeight("")
              }}
              className="w-full rounded-md px-2 py-1 text-[11px] font-mono"
              style={SELECT_STYLE}
            >
              <option value="">Choose a numeric target…</option>
              {targetColumns.map((column) => (
                <option key={column.name} value={column.name}>
                  {column.name}
                </option>
              ))}
            </select>
          </label>
          <label className="block space-y-1">
            <span className={LABEL_CLASS} style={SECONDARY_STYLE}>
              Weight (optional)
            </span>
            <select
              aria-label="Weight"
              value={weight}
              onChange={(event) => setWeight(event.target.value)}
              className="w-full rounded-md px-2 py-1 text-[11px] font-mono"
              style={SELECT_STYLE}
              disabled={!target}
            >
              <option value="">No weight</option>
              {weightColumns.map((column) => (
                <option key={column.name} value={column.name}>
                  {column.name}
                </option>
              ))}
            </select>
          </label>
        </div>
        <ColumnChecklist
          label="Features"
          columns={featureColumns}
          selected={askedFeatures}
          limit={FEATURE_LIMIT}
          onToggle={(name) => setFeatures((current) => toggle(current, name, FEATURE_LIMIT))}
          disabledReason={target ? undefined : "Choose a target to relate features to."}
        />
        <ColumnChecklist
          label="Key columns"
          columns={columns.filter(canBeGrouped)}
          selected={keyColumns}
          limit={KEY_COLUMN_LIMIT}
          onToggle={(name) => setKeyColumns((current) => toggle(current, name, KEY_COLUMN_LIMIT))}
        />
      </div>

      {askedFor === null ? (
        <p className="text-[11px]" style={MUTED_STYLE}>
          Choose features to relate to a target, or key columns to check for uniqueness.
        </p>
      ) : cache.availability !== "current" ? (
        <p className="text-[11px]" style={MUTED_STYLE}>
          Relationships read the cached data; cache it to analyse the whole dataset.
        </p>
      ) : error ? (
        <p role="alert" className="text-[11px]" style={{ color: "var(--danger-text)" }}>
          {error}
        </p>
      ) : cacheRequired ? (
        <div className="flex items-center gap-2 text-[11px]" style={MUTED_STYLE}>
          <span>The server has no cached data for these columns; cache it again to analyse them.</span>
          <button
            type="button"
            onClick={() => void cache.run()}
            className="rounded-md px-2 py-1 text-[11px] font-medium"
            style={{ ...SELECT_STYLE, color: "var(--text-primary)" }}
          >
            Cache data
          </button>
        </div>
      ) : !answer ? (
        <p role="status" className="text-[11px]" style={MUTED_STYLE}>
          {loading ? "Analysing the whole dataset…" : "Waiting to analyse…"}
        </p>
      ) : (
        <div className="space-y-3">
          {answer.key_check && (
            <p
              data-testid="explore-key-check"
              className="text-[11px] rounded-md px-2.5 py-2"
              style={{
                ...CARD_STYLE,
                color: answer.key_check.unique ? "var(--text-primary)" : "var(--warning-strong)",
              }}
            >
              {keyCheckText(answer.key_check)}
            </p>
          )}
          {answer.relationships.length > 0 && (
            <div className="rounded-lg p-3 space-y-2" style={CARD_STYLE}>
              <p className="text-[11px]" style={MUTED_STYLE}>
                Ranked by how much of {answer.target}'s variance each feature's levels explain, over{" "}
                {answer.used_rows.toLocaleString()} of {answer.total_rows.toLocaleString()} rows
                {answer.weight ? `, weighted by ${answer.weight}` : ""}.
              </p>
              <table className="w-full text-[11px]" aria-label="Feature relationships">
                <thead>
                  <tr>
                    {["Feature", "Kind", "Strength", "Levels"].map((header) => (
                      <th key={header} className={`${LABEL_CLASS} text-left px-2 py-1.5`} style={SECONDARY_STYLE}>
                        {header}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {answer.relationships.map((relationship) => (
                    <RelationshipRows key={relationship.feature} relationship={relationship} />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
