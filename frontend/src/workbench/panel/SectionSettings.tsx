/** A section's own panel (specs/workbench). */
import { Trash2 } from "lucide-react"
import type { FormSpec, Page, Section as SheetSection } from "../../api/types"
import { CommittedTextField, FIELD_CLASS, INPUT_STYLE, LabelledRow, QuietAction, ValidatedTextField, wholeNumber } from "../../components/form"
import useWorkbenchFormStore from "../../stores/useWorkbenchFormStore"
import useWorkbenchViewStore from "../../stores/useWorkbenchViewStore"
import { leastSectionHeight } from "../../utils/sheetGeometry"
import { addSection, createSection, fitSection, renameSection, setSectionHeight, setSectionShowWhen } from "../../utils/workbenchForm"
import { deleteSection } from "../sheetInteractions"
import { ShowWhenEditor } from "../ShowWhenEditor"
import { Section } from "./Section"

/**
 * A section's panel: its title (a blank one kept out), when it shows, its height in whole
 * pixels, never under what its components reach, with Fit, and adding a section under it
 * or deleting it, asking first when components are in it, while it is not the sheet's only
 * section.
 */
export function SectionSettings({ form, page, section, index }: { form: FormSpec; page: Page; section: SheetSection; index: number }) {
  const change = useWorkbenchFormStore((s) => s.change)
  const selectSection = useWorkbenchViewStore((s) => s.selectSection)
  const least = leastSectionHeight(section.widgets)
  const height = wholeNumber({ min: least, unit: "pixels" })
  const addBelow = () => {
    const added = createSection(page)
    change((spec) => addSection(spec, page.id, added, index + 1))
    selectSection(added.id)
  }
  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-auto px-3 py-3 text-xs">
      <Section title="Title">
        <CommittedTextField
          aria-label="Section title"
          value={section.title}
          onCommit={(title) => {
            const trimmed = title.trim()
            if (trimmed) change((spec) => renameSection(spec, section.id, trimmed))
          }}
          className={`${FIELD_CLASS} w-full`}
          style={INPUT_STYLE}
        />
      </Section>
      <Section title="Show when">
        <ShowWhenEditor
          form={form}
          showWhen={section.show_when}
          onChange={(next) => change((spec) => setSectionShowWhen(spec, section.id, next))}
        />
      </Section>
      <Section title="Layout">
        <div className="flex items-start gap-2">
          <LabelledRow label="Height">
            <ValidatedTextField
              dataTestId="section-height"
              value={String(section.height)}
              onCommit={(text) => change((spec) => setSectionHeight(spec, section.id, Number(text)))}
              validate={height}
              containerClassName="w-20"
              className={`${FIELD_CLASS} w-full tabular-nums`}
              style={INPUT_STYLE}
            />
          </LabelledRow>
          <button
            type="button"
            onClick={() => change((spec) => fitSection(spec, section.id))}
            title="Fit the section to its components"
            className="quiet-action focus-ring rounded px-1.5 py-1 text-[11px] font-medium"
          >
            Fit
          </button>
        </div>
      </Section>
      <div className="flex flex-col items-start gap-1">
        <QuietAction onClick={addBelow}>Add section below</QuietAction>
        <button
          type="button"
          disabled={page.sections.length === 1}
          title={page.sections.length === 1 ? "A sheet keeps at least one section" : undefined}
          onClick={() => void deleteSection(page, section)}
          className="focus-ring inline-flex items-center gap-1 rounded px-1 py-0.5 -ml-1 text-[11px] font-medium hover-bg disabled:opacity-50"
          style={{ color: "var(--danger)" }}
        >
          <Trash2 size={11} aria-hidden="true" />
          Delete section
        </button>
      </div>
    </div>
  )
}
