import { EditorLabel } from "../../components/form"
import ToggleButtonGroup from "../../components/ToggleButtonGroup"
import type { IoCapabilityGroup } from "../../api/types"
import { DIRECTION_LABEL, PROVIDER_KEY, type IoDirection } from "./_ioProvider"

/**
 * The provider block both Data editors open with: a capability-load error, an
 * unknown stored provider, and the provider picker.
 */
export default function IoProviderPicker({
  direction,
  config,
  groups,
  group,
  capabilitiesLoaded,
  error,
  accentColor,
  onSelect,
}: {
  direction: IoDirection
  config: Record<string, unknown>
  groups: IoCapabilityGroup[]
  group: IoCapabilityGroup | undefined
  capabilitiesLoaded: boolean
  error: string | null | undefined
  accentColor: string
  onSelect: (group: IoCapabilityGroup) => void
}) {
  const stored = config[PROVIDER_KEY[direction]]
  return (
    <>
      {error && (
        <p style={{ color: "var(--danger-text)" }}>
          Could not load IO capabilities: {error}
        </p>
      )}

      {stored !== undefined && !group && capabilitiesLoaded && (
        <section
          aria-label="Configuration errors"
          className="rounded-lg p-2 text-[11px]"
          style={{
            background: "var(--danger-soft)",
            border: "1px solid var(--danger-border)",
            color: "var(--danger-text)",
          }}
        >
          Unknown {DIRECTION_LABEL[direction]} provider {JSON.stringify(stored)}.
        </section>
      )}

      <div>
        <EditorLabel as="div">Provider</EditorLabel>
        <div className="mt-1">
          <ToggleButtonGroup
            value={group?.name ?? ""}
            onChange={(name) => {
              const next = groups.find((candidate) => candidate.name === name)
              if (next) onSelect(next)
            }}
            options={groups.map((candidate) => ({
              key: candidate.name,
              label: candidate.label,
            }))}
            accentColor={accentColor}
            ariaLabel="Provider"
          />
        </div>
      </div>
    </>
  )
}
