import { Plus, Trash2 } from "lucide-react"
import type { OnUpdateConfig } from "./_shared"
import { configField } from "../../utils/configField"

type ConstantValue = { name: string; value: string }

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
    onUpdate("values", [...values, { name: `constant_${values.length + 1}`, value: "0" }])
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
          <div key={i} className="flex items-center gap-1.5">
            <input
              type="text"
              value={v.name}
              onChange={(e) => updateRow(i, "name", e.target.value)}
              placeholder="name"
              className="flex-1 min-w-0 px-2 py-1.5 text-xs font-mono rounded-lg"
              style={{
                background: "var(--bg-input)",
                color: "var(--text-primary)",
                border: "1px solid var(--border)",
              }}
            />
            <input
              type="text"
              value={v.value}
              onChange={(e) => updateRow(i, "value", e.target.value)}
              placeholder="value"
              className="w-24 px-2 py-1.5 text-xs font-mono rounded-lg text-right"
              style={{
                background: "var(--bg-input)",
                color: "var(--text-primary)",
                border: "1px solid var(--border)",
              }}
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
