/** A component's Component name, first in its panel (specs/workbench). */
import type { FormSpec } from "../../api/types"
import { FIELD_CLASS, INPUT_STYLE, ValidatedTextField } from "../../components/form"
import useWorkbenchFormStore from "../../stores/useWorkbenchFormStore"
import { componentNameProblem, renameWidget, type Widget } from "../../utils/workbenchForm"
import { Section } from "./Section"

/**
 * The builder's name for a component, which names its file and which no underwriter sees:
 * committed on blur or Enter as one undo step, a name that is not an identifier, one Windows
 * keeps or another component's refused at the field, the component keeping its own.
 */
export function ComponentNameField({ form, widget }: { form: FormSpec; widget: Widget }) {
  const change = useWorkbenchFormStore((s) => s.change)
  return (
    <Section title="Component name">
      <ValidatedTextField
        dataTestId="component-name"
        ariaLabel="Component name"
        value={widget.name}
        validate={(name) => componentNameProblem(form, widget.id, name)}
        onCommit={(name) => change((spec) => renameWidget(spec, widget.id, name))}
        containerClassName="w-full"
        className={`${FIELD_CLASS} w-full`}
        style={INPUT_STYLE}
      />
    </Section>
  )
}
