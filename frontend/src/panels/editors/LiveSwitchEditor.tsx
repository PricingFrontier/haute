import { AlertTriangle, ToggleLeft } from "lucide-react"
import { unresolvedFrameTitle } from "./_shared"
import type { InputSource, OnUpdateConfig } from "./_shared"
import { configField } from "../../utils/configField"
import { withAlpha } from "../../utils/color"
import useSettingsStore from "../../stores/useSettingsStore"

export default function LiveSwitchEditor({
  config,
  onUpdate,
  inputSources,
  accentColor,
}: {
  config: Record<string, unknown>
  onUpdate: OnUpdateConfig
  inputSources: InputSource[]
  accentColor: string
}) {
  const sources = useSettingsStore((s) => s.sources)
  const activeSource = useSettingsStore((s) => s.activeSource)
  const inputScenarioMap = configField<Record<string, string>>(config, "input_scenario_map", {})

  /** Update the mapping for a single input. */
  const setMapping = (inputName: string, source: string) => {
    onUpdate("input_scenario_map", { ...inputScenarioMap, [inputName]: source })
  }

  return (
    <div className="px-4 py-3 space-y-3">
      <div className="flex items-center gap-2 px-2.5 py-2 rounded-lg text-xs font-medium"
        style={{ background: withAlpha(accentColor, 0.1), border: `1px solid ${withAlpha(accentColor, 0.3)}`, color: accentColor }}
      >
        <ToggleLeft size={14} />
        <span>Routes inputs based on the active source</span>
      </div>

      <div>
        <label className="text-[11px] font-bold uppercase tracking-[0.08em] mb-1 block" style={{ color: 'var(--text-muted)' }}>
          Active Source
        </label>
        <div className="px-2.5 py-1.5 text-xs rounded-lg font-mono"
          style={{ background: 'var(--bg-input)', border: '1px solid var(--border)', color: 'var(--text-primary)' }}
        >
          {activeSource === "live" ? "● live" : activeSource}
        </div>
        <div className="mt-1 text-[10px]" style={{ color: 'var(--text-muted)' }}>
          Change the active source in the toolbar dropdown
        </div>
      </div>

      <div>
        <label className="text-[11px] font-bold uppercase tracking-[0.08em] mb-1.5 block" style={{ color: 'var(--text-muted)' }}>
          Input → Source Mapping ({inputSources.length})
        </label>
        <div className="space-y-1.5">
          {inputSources.map((src) => {
            const mappedSource = inputScenarioMap[src.name] || ""
            const isActive = mappedSource === activeSource
            return (
              <div
                key={src.edgeId}
                data-testid={`live-switch-input-${src.edgeId}`}
                className="flex items-center gap-2 px-2 py-1.5 rounded-md text-xs"
                style={{
                  background: isActive ? withAlpha(accentColor, 0.1) : 'var(--bg-panel)',
                  border: isActive ? `1px solid ${withAlpha(accentColor, 0.3)}` : '1px solid var(--border)',
                }}
              >
                <span
                  className="w-1.5 h-1.5 rounded-full shrink-0"
                  style={{ background: isActive ? accentColor : 'var(--text-muted)' }}
                />
                <span
                  className="font-mono min-w-0 flex-1 overflow-hidden text-ellipsis whitespace-pre"
                  data-unresolved={src.frameUnresolved ? "true" : undefined}
                  aria-label={src.frameUnresolved ? "Unresolved frame" : undefined}
                  title={src.frameUnresolved
                    ? unresolvedFrameTitle(src.sourceLabel)
                    : `from ${src.name}`}
                  style={{ color: src.frameUnresolved ? 'var(--warning)' : 'var(--text-primary)', whiteSpace: "pre" }}
                >
                  {src.frameUnresolved && (
                    <AlertTriangle
                      size={11}
                      aria-hidden="true"
                      className="inline mr-1"
                    />
                  )}
                  {src.name}
                </span>
                <select
                  value={mappedSource}
                  onChange={(e) => setMapping(src.name, e.target.value)}
                  className="px-1.5 py-0.5 text-[11px] font-mono rounded focus:outline-none"
                  style={{
                    background: 'var(--bg-input)',
                    border: '1px solid var(--border)',
                    color: 'var(--text-primary)',
                  }}
                >
                  <option value="">—</option>
                  {sources.map((s) => (
                    <option key={s} value={s}>{s}</option>
                  ))}
                </select>
                {isActive && (
                  <span className="text-[10px] font-medium shrink-0" style={{ color: accentColor }}>
                    active
                  </span>
                )}
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}
