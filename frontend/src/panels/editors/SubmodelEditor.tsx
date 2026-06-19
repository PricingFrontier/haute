import { Package } from "lucide-react"
import { configField } from "../../utils/configField"
import { withAlpha } from "../../utils/color"
import { EditorLabel } from "../../components/form"

export default function SubmodelEditor({
  config,
  accentColor,
}: {
  config: Record<string, unknown>
  accentColor: string
}) {
  const file = configField(config, "file", "")
  const childNodeIds = configField<string[]>(config, "childNodeIds", [])
  const inputPorts = configField<string[]>(config, "inputPorts", [])
  const outputPorts = configField<string[]>(config, "outputPorts", [])

  return (
    <div className="px-4 py-3 space-y-3">
      <div className="flex items-center gap-2 px-2.5 py-2 rounded-lg" style={{ background: withAlpha(accentColor, 0.08), border: `1px solid ${withAlpha(accentColor, 0.2)}` }}>
        <Package size={14} style={{ color: accentColor }} />
        <span className="text-xs font-medium" style={{ color: accentColor }}>Wrapper</span>
        <span className="ml-auto text-[11px] font-mono" style={{ color: 'var(--text-muted)' }}>{childNodeIds.length} nodes</span>
      </div>

      {file && (
        <div>
          <EditorLabel>File</EditorLabel>
          <div className="mt-1 text-xs font-mono px-2.5 py-1.5 rounded-lg" style={{ background: 'var(--bg-input)', border: '1px solid var(--border)', color: 'var(--text-secondary)' }}>
            {file}
          </div>
        </div>
      )}

      {inputPorts.length > 0 && (
        <div>
          <EditorLabel>Inputs</EditorLabel>
          <div className="mt-1 flex flex-wrap gap-1.5">
            {inputPorts.map((port) => (
              <span key={port} className="inline-flex items-center px-2 py-0.5 rounded text-[11px] font-mono" style={{ background: 'var(--accent-soft)', color: 'var(--accent)' }}>
                {port}
              </span>
            ))}
          </div>
        </div>
      )}

      {outputPorts.length > 0 && (
        <div>
          <EditorLabel>Outputs</EditorLabel>
          <div className="mt-1 flex flex-wrap gap-1.5">
            {outputPorts.map((port) => (
              <span key={port} className="inline-flex items-center px-2 py-0.5 rounded text-[11px] font-mono" style={{ background: withAlpha(accentColor, 0.1), color: accentColor }}>
                {port}
              </span>
            ))}
          </div>
        </div>
      )}

      <div className="text-[11px] pt-1" style={{ color: 'var(--text-muted)' }}>
        Double-click to view internal nodes
      </div>
    </div>
  )
}
