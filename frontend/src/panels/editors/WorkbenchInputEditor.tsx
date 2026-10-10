import { Radio } from "lucide-react"
import type { WorkbenchTable } from "../../api/types"
import useWorkbenchStore from "../../stores/useWorkbenchStore"
import { apiInputLabelIssue, apiInputLabelIssueMessage } from "../../utils/apiInputPorts"
import { withAlpha } from "../../utils/color"
import { readWorkbenchTables } from "../../utils/workbenchTables"

const SECTION_LABEL = "text-[11px] font-bold uppercase tracking-[0.08em]"

/**
 * The Workbench Input's panel (specs/workbench): the tables the project's workbench
 * supplies, read-only, with whatever stops a table's name being a port. It reads no
 * file: previews run on the workbench's sample quote, a table it gives no rows being
 * one row of nulls. The editor keeps the copies current (`useWorkbenchTables`);
 * nothing here edits them.
 */
export default function WorkbenchInputEditor({
  config,
  accentColor,
  reservedFrameLabels,
  insideSubmodel = false,
}: {
  config: Record<string, unknown>
  accentColor: string
  reservedFrameLabels: ReadonlySet<string>
  /** A submodel is open: the editor updates the tables only at the pipeline's top level. */
  insideSubmodel?: boolean
}) {
  const enabled = useWorkbenchStore((state) => state.enabled)
  const tables = readWorkbenchTables(config)
  const names = tables.map((table) => table.name)
  const sample = config.sample
  const hasSample =
    sample !== null && typeof sample === "object" && !Array.isArray(sample) && Object.keys(sample).length > 0

  return (
    <div className="px-4 py-3 space-y-3" data-testid="workbench-input-editor">
      <div
        className="flex items-center gap-2 px-2.5 py-2 rounded-lg text-xs font-medium"
        style={{
          background: withAlpha(accentColor, 0.1),
          border: `1px solid ${withAlpha(accentColor, 0.3)}`,
          color: accentColor,
        }}
      >
        <Radio size={14} />
        <span>This node receives live API requests at deploy time</span>
      </div>

      <p data-testid="workbench-input-preview-note" className="text-[11px]" style={{ color: "var(--text-muted)" }}>
        {hasSample
          ? "Previews run on the workbench's sample values; a table with none is one row of nulls."
          : "Previews run on one row of nulls per table until the workbench supplies values."}
      </p>

      <section data-testid="workbench-input-tables" className="space-y-1.5">
        <WorkbenchTablesHeader
          enabled={enabled}
          insideSubmodel={insideSubmodel}
          empty={tables.length === 0}
        />
        {tables.map((table, index) => (
          <WorkbenchTableCard
            key={`${index}:${table.name}`}
            table={table}
            issue={tableLabelIssue(names, index, reservedFrameLabels)}
          />
        ))}
      </section>
    </div>
  )
}

/** What stops `names[index]`, a table's name, being a port, or null. */
// eslint-disable-next-line react-refresh/only-export-components
export function tableLabelIssue(
  names: readonly string[],
  index: number,
  reservedFrameLabels: ReadonlySet<string>,
): string | null {
  return apiInputLabelIssueMessage(
    apiInputLabelIssue(
      names[index],
      names.filter((_, other) => other !== index),
      reservedFrameLabels,
    ),
  )
}

/** The note both workbench panels show while the project's workbench is not enabled. */
export const WORKBENCH_DISABLED_NOTE =
  "The workbench is not enabled in haute.toml, so these tables are the last copy and nothing updates them."

/**
 * The title of a workbench node's tables, the way to the workbench's view while the
 * workbench is enabled, and its notes: the workbench is not enabled, or a submodel is open.
 */
export function WorkbenchTablesHeader({
  enabled,
  insideSubmodel,
  empty,
}: {
  enabled: boolean
  insideSubmodel: boolean
  empty: boolean
}) {
  return (
    <>
      <div className="flex items-baseline justify-between gap-2">
        <h3 className={SECTION_LABEL} style={{ color: "var(--text-muted)" }}>
          {enabled ? "Tables from the workbench" : "Tables"}
        </h3>
        {enabled && (
          <button
            type="button"
            data-testid="edit-in-workbench"
            onClick={() => useWorkbenchStore.getState().showView("workbench")}
            className="quiet-action focus-ring shrink-0 rounded px-1 py-0.5 text-[11px] font-medium"
          >
            Edit in Workbench
          </button>
        )}
      </div>
      {!enabled && (
        <p role="note" className="text-[11px]" style={{ color: "var(--text-muted)" }}>
          {WORKBENCH_DISABLED_NOTE}
        </p>
      )}
      {insideSubmodel && (
        <p role="note" className="text-[11px]" style={{ color: "var(--text-muted)" }}>
          The editor updates these tables only at the pipeline's top level.
        </p>
      )}
      {empty && (
        <p className="text-[11px]" style={{ color: "var(--text-muted)" }}>
          No tables yet.
        </p>
      )}
    </>
  )
}

/** The note a table without columns shows: it has no frame, so no port. */
export const NO_COLUMNS_NOTE = "No columns yet, so no port."

/** One of a workbench node's tables, read-only: its name, its rows per quote and its columns. */
export function WorkbenchTableCard({ table, issue }: { table: WorkbenchTable; issue: string | null }) {
  return (
    <div
      data-testid={`workbench-table-${table.name}`}
      className="rounded-md border px-2 py-1.5 text-xs"
      style={{ borderColor: issue ? "var(--warning)" : "var(--border)", background: "var(--bg-elevated)" }}
    >
      <div className="flex items-baseline justify-between gap-2">
        <span className="font-mono font-semibold truncate">{table.name}</span>
        <span className="shrink-0 text-[11px]" style={{ color: "var(--text-muted)" }}>
          {table.rows === "one" ? "one per quote" : "many per quote"}
        </span>
      </div>
      {issue && (
        <p role="alert" className="mt-0.5 text-[11px]" style={{ color: "var(--warning)" }}>
          {issue}
        </p>
      )}
      {table.columns.length === 0 ? (
        <p className="mt-0.5 text-[11px]" style={{ color: "var(--text-muted)" }}>
          {NO_COLUMNS_NOTE}
        </p>
      ) : (
        <ul className="mt-1 space-y-0.5">
          {table.columns.map((column) => (
            <li key={column.name} className="flex items-baseline justify-between gap-2">
              <span className="font-mono truncate">{column.name}</span>
              <span className="shrink-0 font-mono text-[11px]" style={{ color: "var(--text-muted)" }}>
                {column.type}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
