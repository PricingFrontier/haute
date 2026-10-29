/** An Import's and an Export's panes, and their Format (specs/workbench). */
import type { ReactNode } from "react"
import type { FormSpec } from "../../api/types"
import { CommittedTextField, FIELD_CLASS, INPUT_STYLE } from "../../components/form"
import ToggleButtonGroup from "../../components/ToggleButtonGroup"
import PreviewPanelTabs from "../../panels/PreviewPanelTabs"
import useWorkbenchFormStore from "../../stores/useWorkbenchFormStore"
import useWorkbenchViewStore from "../../stores/useWorkbenchViewStore"
import { updateWidget, type ButtonWidget, type ImportFormat } from "../../utils/workbenchForm"
import { ShowWhenEditor } from "../ShowWhenEditor"
import { FORMAT_MARKS, WIDGET_KINDS } from "../widgetKinds"
import { WIDGET_PANES } from "./FieldsSettings"
import { Section } from "./Section"

const FORMATS: ReadonlyArray<{ key: ImportFormat; label: string }> = [
  { key: "csv", label: "CSV" },
  { key: "excel", label: "Excel" },
  { key: "sql", label: "SQL" },
]

/** A button's Format, CSV, Excel or SQL, each with its mark, in the kind's colour; choosing the one it has records nothing. */
export function FormatToggle({ widget, format, onChange }: { widget: ButtonWidget; format: ImportFormat; onChange: (format: ImportFormat) => void }) {
  return (
    <Section title="Format">
      <ToggleButtonGroup
        ariaLabel="Format"
        value={format}
        accentColor={WIDGET_KINDS[widget.type].color}
        options={FORMATS.map(({ key, label }) => {
          const Mark = FORMAT_MARKS[key]
          return { key, label, icon: <Mark size={13} aria-hidden="true" /> }
        })}
        onChange={(next) => {
          if (next !== format) onChange(next)
        }}
      />
    </Section>
  )
}

/**
 * An Import's or an Export's two panes, as a Table's are, in the kind's colour: Config, the
 * panel's own (`children`), and Display, its Label and its Show when.
 */
export function ButtonPanes({ form, widget, children }: { form: FormSpec; widget: ButtonWidget; children: ReactNode }) {
  const change = useWorkbenchFormStore((s) => s.change)
  const kind = WIDGET_KINDS[widget.type]
  const pane = useWorkbenchViewStore((s) => s.widgetPanes[widget.id] ?? "config")
  const setWidgetPane = useWorkbenchViewStore((s) => s.setWidgetPane)
  return (
    <>
      <PreviewPanelTabs
        tabs={WIDGET_PANES}
        activeTab={pane}
        onChange={(next) => setWidgetPane(widget.id, next)}
        ariaLabel={`${kind.label} panes`}
        accentColor={kind.color}
        equalWidth
        idPrefix="component"
      />
      <div
        id={`component-${pane}-pane`}
        role="tabpanel"
        aria-labelledby={`component-${pane}-tab`}
        className="flex min-h-0 flex-1 flex-col gap-4 overflow-auto px-3 py-3 text-xs"
      >
        {pane === "config" ? (
          children
        ) : (
          <>
            <Section title="Label">
              <CommittedTextField
                aria-label="Label"
                value={widget.label}
                placeholder="None"
                onCommit={(label) => change((spec) => updateWidget(spec, widget.id, { label }))}
                className={`${FIELD_CLASS} w-full`}
                style={INPUT_STYLE}
              />
            </Section>
            <Section title="Show when">
              <ShowWhenEditor
                form={form}
                showWhen={widget.show_when}
                priced={false}
                onChange={(next) => change((spec) => updateWidget(spec, widget.id, { show_when: next }))}
              />
            </Section>
          </>
        )}
      </div>
    </>
  )
}
