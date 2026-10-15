/** The showing sheet's own panel (specs/workbench). */
import { Trash2 } from "lucide-react"
import type { FormSpec, Page } from "../../api/types"
import { CommittedTextField, FIELD_CLASS, INPUT_STYLE, LabelledRow, ValidatedTextField, wholeNumber } from "../../components/form"
import { confirmed } from "../../stores/useQuestionStore"
import useWorkbenchFormStore from "../../stores/useWorkbenchFormStore"
import useWorkbenchViewStore from "../../stores/useWorkbenchViewStore"
import { leastSheetWidth } from "../../utils/sheetGeometry"
import { pageWidgets, removePage, renamePage, setPageShowWhen, setPageWidth } from "../../utils/workbenchForm"
import { ShowWhenEditor } from "../ShowWhenEditor"
import { Section } from "./Section"

/**
 * The showing sheet's panel: its title (a blank one kept out), when it shows, its width in
 * whole pixels, never under what its components reach, and at its foot Delete sheet, asking
 * first what goes with it and what stays (`confirmed`), while it is not the only sheet.
 */
export function SheetSettings({ form, page }: { form: FormSpec; page: Page }) {
  const change = useWorkbenchFormStore((s) => s.change)
  const showPage = useWorkbenchViewStore((s) => s.showPage)
  const only = form.pages.length === 1
  const deleteSheet = async () => {
    const count = pageWidgets(page).length
    const going = count === 0 ? "Nothing is on it." : count === 1 ? "The component on it goes with it." : `The ${count} components on it go with it.`
    if (!(await confirmed({ title: `Delete "${page.title}"?`, text: `${going} The schema and the sample stay.`, confirm: "Delete", danger: true }))) return
    const remaining = form.pages.find((sheet) => sheet.id !== page.id)
    if (remaining === undefined) return
    change((spec) => removePage(spec, page.id))
    showPage(remaining.id)
  }
  const least = leastSheetWidth(pageWidgets(page))
  const width = wholeNumber({ min: least, unit: "pixels" })
  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-auto px-3 py-3 text-xs">
      <Section title="Title">
        <CommittedTextField
          aria-label="Sheet title"
          value={page.title}
          onCommit={(title) => {
            const trimmed = title.trim()
            if (trimmed) change((spec) => renamePage(spec, page.id, trimmed))
          }}
          className={`${FIELD_CLASS} w-full`}
          style={INPUT_STYLE}
        />
      </Section>
      <Section title="Show when">
        <ShowWhenEditor
          form={form}
          showWhen={page.show_when}
          onChange={(next) => change((spec) => setPageShowWhen(spec, page.id, next))}
        />
      </Section>
      <Section title="Layout">
        <LabelledRow label="Width">
          <ValidatedTextField
            dataTestId="sheet-width"
            value={String(page.width)}
            onCommit={(text) => change((spec) => setPageWidth(spec, page.id, Number(text)))}
            validate={width}
            containerClassName="w-20"
            className={`${FIELD_CLASS} w-full tabular-nums`}
            style={INPUT_STYLE}
          />
        </LabelledRow>
      </Section>
      <div className="mt-auto pt-3" style={{ borderTop: "1px solid var(--border)" }}>
        <button
          type="button"
          disabled={only}
          title={only ? "The last sheet cannot be deleted" : undefined}
          onClick={() => void deleteSheet()}
          className="focus-ring inline-flex items-center gap-1 rounded px-1 py-0.5 -ml-1 text-[11px] font-medium hover-bg disabled:opacity-50"
          style={{ color: "var(--danger)" }}
        >
          <Trash2 size={11} aria-hidden="true" />
          Delete sheet
        </button>
      </div>
    </div>
  )
}
