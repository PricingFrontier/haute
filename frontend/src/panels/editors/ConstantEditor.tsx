import { Plus, Trash2 } from "lucide-react"
import type { OnUpdateConfig } from "./_shared"
import { configField } from "../../utils/configField"
import { CommittedTextField, ValidatedTextField } from "../../components/form"
import { duplicateConstantNames, nextConstantName } from "./constantNames"

type ConstantValue = { name: string; value: string }

const FIELD_STYLE = {
  background: "var(--bg-input)",
  color: "var(--text-primary)",
  border: "1px solid var(--border)",
}

export default function ConstantEditor({
  config,
  onUpdate,
}: {
  config: Record<string, unknown>
  onUpdate: OnUpdateConfig
}) {
  const values = configField<ConstantValue[]>(config, "values", [])

  const updateRow = (index: number, field: "name" | "value", val: string) => {
    const next = values.map((v, i) => (i === index ? { ...v, [field]: val } : v))
    onUpdate("values", next)
  }

  const addRow = () => {
    onUpdate("values", [...values, { name: nextConstantName(values.map((v) => v.name)), value: "0" }])
  }

  // Execution refuses two values of one name, so a row whose name another row
  // uses is marked, and typing such a name is refused.
  const nameError = (index: number, candidate: string): string | null => {
    const names = values.map((v, i) => (i === index ? candidate : v.name))
    return duplicateConstantNames(names).has(candidate) ? `Another value is named ${candidate}` : null
  }

  const removeRow = (index: number) => {
    onUpdate("values", values.filter((_, i) => i !== index))
  }

  return (
    <div className="px-4 py-3 space-y-3">
      <label
        className="text-[11px] font-bold uppercase tracking-[0.08em]"
        style={{ color: "var(--text-muted)" }}
      >
        Values
      </label>

      <div className="space-y-1.5">
        {values.map((v, i) => (
          <div key={i} className="flex items-start gap-1.5">
            <ValidatedTextField
              value={v.name}
              validate={(candidate) => nameError(i, candidate)}
              onCommit={(val) => updateRow(i, "name", val)}
              placeholder="name"
              dataTestId={`constant-name-${i}`}
              containerClassName="flex-1 min-w-0"
              className="w-full px-2 py-1.5 text-xs font-mono rounded-lg"
              style={FIELD_STYLE}
            />
            <CommittedTextField
              type="text"
              value={v.value}
              onCommit={(val) => updateRow(i, "value", val)}
              placeholder="value"
              className="w-24 px-2 py-1.5 text-xs font-mono rounded-lg text-right"
              style={FIELD_STYLE}
            />
            <button
              onClick={() => removeRow(i)}
              className="icon-danger-btn p-1 rounded shrink-0"
              title="Remove"
            >
              <Trash2 size={12} />
            </button>
          </div>
        ))}
      </div>

      <button
        onClick={addRow}
        className="add-row-btn flex items-center gap-1.5 px-2.5 py-1.5 text-xs font-medium rounded-lg w-full justify-center"
        style={{
          color: "var(--text-secondary)",
          border: "1px solid var(--border)",
        }}
      >
        <Plus size={12} />
        Add value
      </button>
    </div>
  )
}
