/** A Table's, a Collection's or a Chart's panel, in its panes (specs/workbench). */
import type { FormSpec } from "../../api/types"
import { CommittedTextField, ConfigCheckbox, FIELD_CLASS, INPUT_STYLE, LabelledRow, SelectRow, ValidatedTextField, wholeNumber } from "../../components/form"
import useCollapsedSet from "../../hooks/useCollapsedSet"
import PreviewPanelTabs, { type PreviewPanelTab } from "../../panels/PreviewPanelTabs"
import useWorkbenchFormStore from "../../stores/useWorkbenchFormStore"
import useWorkbenchViewStore, { type WidgetPane } from "../../stores/useWorkbenchViewStore"
import { chartGrain, COLLECTION_COLUMNS_MAX, fieldColumn, rowsShown, setTableRows, TABLE_ROWS_MAX, tableGrain, updateWidget, type CollectionOptions, type FieldsWidget } from "../../utils/workbenchForm"
import ChartSettings from "../ChartSettings"
import { ShowWhenEditor } from "../ShowWhenEditor"
import { WIDGET_KINDS } from "../widgetKinds"
import { FieldOrder } from "./FieldOrder"
import { Section } from "./Section"
import { TableFields } from "./TableFields"
import { DENSITIES } from "./lookChoices"
import { TableLook } from "./TableLook"

const LABEL_PLACES: ReadonlyArray<{ value: CollectionOptions["labels"]; label: string }> = [
  { value: "above", label: "Above" },
  { value: "beside", label: "Beside" },
]

/** A Table's and a Collection's panes: the data it shows and their order, and how it shows them. */
const WIDGET_PANES = [
  { key: "config", label: "Config" },
  { key: "display", label: "Display" },
] as const satisfies readonly PreviewPanelTab<WidgetPane>[]

export function FieldsSettings({ form, widget }: { form: FormSpec; widget: FieldsWidget }) {
  const change = useWorkbenchFormStore((s) => s.change)
  const showSection = useWorkbenchViewStore((s) => s.showSection)
  const { collapsed, toggle: toggleCollapsed } = useCollapsedSet()
  // A Chart plots either kind of table; a Table and a Collection each show one.
  const rows = widget.type === "chart" ? null : rowsShown(widget)
  const tables = rows === null ? form.schema.tables : form.schema.tables.filter((table) => table.rows === rows)
  // The rows the fields already chosen fill: columns of tables that fill other rows cannot
  // share the grid. A key-less many-row table, for one, lines up with nothing else.
  const first = widget.fields.map((field) => fieldColumn(form, field)).find((shown) => shown?.table.rows === rows)
  const grain = widget.type === "chart" ? chartGrain(form.schema, widget) : first ? tableGrain(first.table) : null

  const kind = WIDGET_KINDS[widget.type]
  // A Table's and a Collection's settings are in two panes, as Haute's node panel puts an
  // Explore node's in panes; a Chart's are one list.
  const paned = widget.type !== "chart"
  const pane = useWorkbenchViewStore((s) => s.widgetPanes[widget.id] ?? "config")
  const setWidgetPane = useWorkbenchViewStore((s) => s.setWidgetPane)
  const shows = (section: WidgetPane) => !paned || pane === section

  return (
    <>
      {paned && (
        <PreviewPanelTabs
          tabs={WIDGET_PANES}
          activeTab={pane}
          onChange={(next) => setWidgetPane(widget.id, next)}
          ariaLabel={`${kind.label} panes`}
          accentColor={kind.color}
          equalWidth
          idPrefix="component"
        />
      )}
      <div
        id={paned ? `component-${pane}-pane` : undefined}
        role={paned ? "tabpanel" : undefined}
        aria-labelledby={paned ? `component-${pane}-tab` : undefined}
        className="flex min-h-0 flex-1 flex-col gap-4 overflow-auto px-3 py-3 text-xs"
      >
        {shows("display") && (
          <Section title="Title">
            <CommittedTextField
              aria-label="Title"
              value={widget.title}
              placeholder="None"
              onCommit={(title) => change((spec) => updateWidget(spec, widget.id, { title }))}
              className={`${FIELD_CLASS} w-full`}
              style={INPUT_STYLE}
            />
          </Section>
        )}
        {shows("display") && (
          <Section title="Show when">
            <ShowWhenEditor
              form={form}
              showWhen={widget.show_when}
              onChange={(next) => change((spec) => updateWidget(spec, widget.id, { show_when: next }))}
            />
          </Section>
        )}
        {shows("display") && widget.type !== "chart" && widget.fields.length > 0 && (
          <Section title="Layout">
            <LabelledRow label={widget.type === "collection" ? "Columns" : "Rows"}>
              {widget.type === "collection" ? (
                <ValidatedTextField
                  dataTestId="widget-columns"
                  value={String(widget.columns)}
                  onCommit={(text) => change((spec) => updateWidget(spec, widget.id, { columns: Number(text) }))}
                  validate={wholeNumber({ min: 1, max: COLLECTION_COLUMNS_MAX })}
                  containerClassName="w-16"
                  className={`${FIELD_CLASS} w-full tabular-nums`}
                  style={INPUT_STYLE}
                />
              ) : (
                <ValidatedTextField
                  dataTestId="widget-rows"
                  value={String(widget.rows)}
                  onCommit={(text) => change((spec) => setTableRows(spec, widget.id, Number(text)))}
                  validate={wholeNumber({ min: 1, max: TABLE_ROWS_MAX })}
                  containerClassName="w-16"
                  className={`${FIELD_CLASS} w-full tabular-nums`}
                  style={INPUT_STYLE}
                />
              )}
            </LabelledRow>
            {widget.type === "tableInput" && (
              <LabelledRow>
                <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
                  <ConfigCheckbox
                    label="Fixed rows"
                    checked={widget.fixed_rows}
                    onChange={(fixed_rows) => change((spec) => updateWidget(spec, widget.id, { fixed_rows }))}
                  />
                  <ConfigCheckbox
                    label="Transposed"
                    checked={widget.transposed}
                    onChange={(transposed) => change((spec) => updateWidget(spec, widget.id, { transposed }))}
                  />
                </div>
              </LabelledRow>
            )}
          </Section>
        )}
        {shows("display") && widget.type === "tableInput" && widget.fields.length > 0 && <TableLook form={form} widget={widget} />}
        {shows("display") && widget.type === "collection" && widget.fields.length > 0 && (
          <Section title="Look">
            <SelectRow label="Labels" value={widget.labels} choices={LABEL_PLACES} onChange={(labels) => change((spec) => updateWidget(spec, widget.id, { labels }))} />
            <SelectRow label="Density" value={widget.density} choices={DENSITIES} onChange={(density) => change((spec) => updateWidget(spec, widget.id, { density }))} />
            <LabelledRow>
              <ConfigCheckbox label="Frame" checked={widget.frame} onChange={(frame) => change((spec) => updateWidget(spec, widget.id, { frame }))} />
            </LabelledRow>
          </Section>
        )}
        {widget.type === "chart" && widget.fields.length > 0 && (
          <Section title="Chart">
            <ChartSettings form={form} widget={widget} />
          </Section>
        )}
        {shows("config") && widget.fields.length > 0 && (
          <Section title="Order">
            <FieldOrder form={form} widget={widget} />
          </Section>
        )}
        {shows("config") && (
          <Section title="Fields" aside={`${widget.fields.length} shown`}>
            {tables.length === 0 ? (
              <p style={{ color: "var(--text-muted)" }}>
                The schema has no {rows === null ? "" : rows === "one" ? "one-row " : "many-row "}tables yet.{" "}
                <button type="button" onClick={() => showSection("schema")} className="quiet-action focus-ring rounded">
                  Open the schema
                </button>
              </p>
            ) : (
              tables.map((table) => (
                <TableFields
                  key={table.id}
                  widget={widget}
                  table={table}
                  usable={grain === null || tableGrain(table) === grain}
                  plots={widget.type === "chart"}
                  collapsed={collapsed.has(table.id)}
                  onToggle={() => toggleCollapsed(table.id)}
                />
              ))
            )}
          </Section>
        )}
      </div>
    </>
  )
}
