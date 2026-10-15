/** A Heading's panel (specs/workbench). */
import { TextAlignCenter, TextAlignEnd, TextAlignStart, type LucideIcon } from "lucide-react"
import type { FormSpec, HeadingWidget } from "../../api/types"
import { CommittedTextField, FIELD_CLASS, INPUT_STYLE, LabelledRow } from "../../components/form"
import ToggleButtonGroup from "../../components/ToggleButtonGroup"
import useWorkbenchFormStore from "../../stores/useWorkbenchFormStore"
import { updateWidget } from "../../utils/workbenchForm"
import { ShowWhenEditor } from "../ShowWhenEditor"
import { WIDGET_KINDS } from "../widgetKinds"
import { Section } from "./Section"

const HEADING_SIZES: ReadonlyArray<{ value: HeadingWidget["size"]; label: string }> = [
  { value: "large", label: "Large" },
  { value: "medium", label: "Medium" },
  { value: "small", label: "Small" },
]

const HEADING_ALIGNS: ReadonlyArray<{ value: HeadingWidget["align"]; label: string; icon: LucideIcon }> = [
  { value: "left", label: "Left", icon: TextAlignStart },
  { value: "center", label: "Centre", icon: TextAlignCenter },
  { value: "right", label: "Right", icon: TextAlignEnd },
]

/** A Heading's panel: its text and description, when it shows, and its size and alignment. */
export function HeadingSettings({ form, widget }: { form: FormSpec; widget: HeadingWidget }) {
  const change = useWorkbenchFormStore((s) => s.change)
  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-auto px-3 py-3 text-xs">
      <Section title="Text">
        <CommittedTextField
          aria-label="Heading text"
          value={widget.text}
          placeholder="Name a section of the sheet"
          onCommit={(text) => change((spec) => updateWidget(spec, widget.id, { text }))}
          className={`${FIELD_CLASS} w-full`}
          style={INPUT_STYLE}
        />
      </Section>
      <Section title="Description">
        <CommittedTextField
          aria-label="Heading description"
          value={widget.description}
          placeholder="None"
          onCommit={(description) => change((spec) => updateWidget(spec, widget.id, { description }))}
          className={`${FIELD_CLASS} w-full`}
          style={INPUT_STYLE}
        />
      </Section>
      <Section title="Show when">
        <ShowWhenEditor
          form={form}
          showWhen={widget.show_when}
          onChange={(next) => change((spec) => updateWidget(spec, widget.id, { show_when: next }))}
        />
      </Section>
      <Section title="Look">
        <LabelledRow label="Size">
          <select
            aria-label="Size"
            value={widget.size}
            onChange={(event) => change((spec) => updateWidget(spec, widget.id, { size: event.target.value as HeadingWidget["size"] }))}
            className={FIELD_CLASS}
            style={INPUT_STYLE}
          >
            {HEADING_SIZES.map(({ value, label }) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </LabelledRow>
        <div className="flex items-center gap-2">
          <span className="w-14 shrink-0" style={{ color: "var(--text-muted)" }}>
            Align
          </span>
          <div className="min-w-0 flex-1">
            <ToggleButtonGroup
              ariaLabel="Align"
              value={widget.align}
              accentColor={WIDGET_KINDS.heading.color}
              options={HEADING_ALIGNS.map(({ value, label, icon: Icon }) => ({ key: value, label, icon: <Icon size={13} aria-hidden="true" /> }))}
              onChange={(align) => {
                if (align !== widget.align) change((spec) => updateWidget(spec, widget.id, { align }))
              }}
            />
          </div>
        </div>
      </Section>
    </div>
  )
}
