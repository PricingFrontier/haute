import { Radio } from "lucide-react"
import useWorkbenchStore from "../../stores/useWorkbenchStore"
import { apiInputLabelIssue, apiInputLabelIssueMessage } from "../../utils/apiInputPorts"
import { withAlpha } from "../../utils/color"
import { readV2, type ApiInputTableV2 } from "./apiInputSchema"

const SECTION_LABEL = "text-[11px] font-bold uppercase tracking-[0.08em]"

/**
 * The Workbench Input's panel (specs/workbench): the tables the project's workbench
 * supplies, read-only, with whatever stops a table's label being a port. It reads no
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
  const tables = readV2(config).tables
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
          <WorkbenchTable
            key={`${index}:${table.label}`}
            table={table}
            issue={tableLabelIssue(tables, index, reservedFrameLabels)}
          />
        ))}
      </section>
    </div>
  )
}

/** What stops the label of `tables[index]` being a port, or null. */
// eslint-disable-next-line react-refresh/only-export-components
export function tableLabelIssue(
  tables: readonly ApiInputTableV2[],
  index: number,
  reservedFrameLabels: ReadonlySet<string>,
): string | null {
  return apiInputLabelIssueMessage(
    apiInputLabelIssue(
      tables[index].label,
      tables.filter((_, other) => other !== index).map((other) => other.label),
      reservedFrameLabels,
    ),
  )
}

/** The note both workbench panels show while the project's workbench is not enabled. */
export const WORKBENCH_DISABLED_NOTE =
  "The workbench is not enabled in haute.toml, so these tables are the last copy and nothing updates them."

/**
 * The title of a workbench node's tables and its notes: the workbench is not enabled, or
 * a submodel is open.
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
      <h3 className={SECTION_LABEL} style={{ color: "var(--text-muted)" }}>
        {enabled ? "Tables from the workbench" : "Tables"}
      </h3>
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

function WorkbenchTable({ table, issue }: { table: ApiInputTableV2; issue: string | null }) {
  const columns = table.columns.filter((column) => column.selected)
  return (
    <div
      data-testid={`workbench-table-${table.label}`}
      className="rounded-md border px-2 py-1.5 text-xs"
      style={{ borderColor: issue ? "var(--warning)" : "var(--border)", background: "var(--bg-elevated)" }}
    >
      <div className="flex items-baseline justify-between gap-2">
        <span className="font-mono font-semibold truncate">{table.label}</span>
        <span className="shrink-0 text-[11px]" style={{ color: "var(--text-muted)" }}>
          {table.path === "$[:]" ? "one per quote" : "many per quote"}
        </span>
      </div>
      {issue && (
        <p role="alert" className="mt-0.5 text-[11px]" style={{ color: "var(--warning)" }}>
          {issue}
        </p>
      )}
      <ul className="mt-1 space-y-0.5">
        {columns.map((column) => (
          <li key={column.name} className="flex items-baseline justify-between gap-2">
            <span className="font-mono truncate">{column.name}</span>
            <span className="shrink-0 font-mono text-[11px]" style={{ color: "var(--text-muted)" }}>
              {column.type}
            </span>
          </li>
        ))}
      </ul>
    </div>
  )
}
