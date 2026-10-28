/** An Export's panel (specs/workbench). */
import { X } from "lucide-react"
import type { CsvExport, ExportWidget, FormSpec } from "../../api/types"
import { CommittedTextField, ConfigCheckbox, FIELD_CLASS, INPUT_STYLE, LabelledRow, SelectRow } from "../../components/form"
import useWorkbenchFormStore from "../../stores/useWorkbenchFormStore"
import { setExportFormat, setTableName, toggleExportTable, updateExportTarget } from "../../utils/workbenchForm"
import { ButtonPanes, FormatToggle } from "./ButtonPanes"
import { Section } from "./Section"

const EXPORT_DELIMITERS: ReadonlyArray<{ value: CsvExport["delimiter"]; label: string }> = [
  { value: "comma", label: "Comma" },
  { value: "semicolon", label: "Semicolon" },
  { value: "tab", label: "Tab" },
]

const HEADERS: ReadonlyArray<{ value: CsvExport["headers"]; label: string }> = [
  { value: "names", label: "Names" },
  { value: "labels", label: "Labels" },
]

const VALUES: ReadonlyArray<{ value: CsvExport["values"]; label: string }> = [
  { value: "raw", label: "Raw" },
  { value: "formatted", label: "Formatted" },
]

/**
 * An Export's panel in its panes (`ButtonPanes`), Config holding its Format, a file's
 * name, delimiter, headers and values or a database's connection, and the Tables it
 * exports, each, to a database, beside the name of the table it is written to.
 */
export function ExportSettings({ form, widget }: { form: FormSpec; widget: ExportWidget }) {
  const change = useWorkbenchFormStore((s) => s.change)
  const { target } = widget
  const set = (patch: Parameters<typeof updateExportTarget>[2]) => change((spec) => updateExportTarget(spec, widget.id, patch))
  const strays = widget.tables.filter((id) => !form.schema.tables.some((table) => table.id === id))
  return (
    <ButtonPanes form={form} widget={widget}>
      <FormatToggle widget={widget} format={target.format} onChange={(format) => change((spec) => setExportFormat(spec, widget.id, format))} />
      {target.format === "sql" ? (
        <Section title="Connection">
          <CommittedTextField
            aria-label="Connection"
            value={target.connection}
            placeholder="None"
            onCommit={(connection) => set({ connection })}
            className={`${FIELD_CLASS} w-full font-mono`}
            style={INPUT_STYLE}
          />
        </Section>
      ) : (
        <Section title="File">
          <LabelledRow label="Name">
            <CommittedTextField
              aria-label="File name"
              value={target.file_name}
              onCommit={(file_name) => set({ file_name })}
              className={`${FIELD_CLASS} min-w-0 flex-1 font-mono`}
              style={INPUT_STYLE}
            />
          </LabelledRow>
          {target.format === "csv" && <SelectRow label="Delimiter" value={target.delimiter} choices={EXPORT_DELIMITERS} onChange={(delimiter) => set({ delimiter })} />}
          <SelectRow label="Headers" value={target.headers} choices={HEADERS} onChange={(headers) => set({ headers })} />
          <SelectRow label="Values" value={target.values} choices={VALUES} onChange={(values) => set({ values })} />
        </Section>
      )}
      <Section title="Tables" aside={`${widget.tables.length} exported`}>
        {form.schema.tables.map((table) => {
          const exported = widget.tables.includes(table.id)
          return (
            <div key={table.id} className="flex items-center gap-2">
              <div className="min-w-0 flex-1 font-mono">
                <ConfigCheckbox label={table.name} checked={exported} onChange={() => change((spec) => toggleExportTable(spec, widget.id, table.id))} />
              </div>
              <span className="text-[11px] font-semibold" style={{ color: table.role === "input" ? "var(--accent)" : "var(--success)" }}>
                {table.role === "input" ? "Input" : "Output"}
              </span>
              {target.format === "sql" && exported && (
                <CommittedTextField
                  aria-label={`${table.name} is written to`}
                  value={target.table_names.find((named) => named.table === table.id)?.name ?? ""}
                  placeholder={table.name}
                  onCommit={(name) => change((spec) => setTableName(spec, widget.id, table.id, name))}
                  className={`${FIELD_CLASS} w-32 font-mono`}
                  style={INPUT_STYLE}
                />
              )}
            </div>
          )
        })}
        {strays.map((id) => (
          <div key={id} className="flex items-center gap-2">
            <span className="min-w-0 flex-1 truncate" style={{ color: "var(--warning)" }}>
              Missing table
            </span>
            <button type="button" onClick={() => change((spec) => toggleExportTable(spec, widget.id, id))} aria-label="Stop exporting the missing table" className="icon-danger-btn focus-ring shrink-0 rounded p-0.5">
              <X size={12} aria-hidden="true" />
            </button>
          </div>
        ))}
      </Section>
    </ButtonPanes>
  )
}
