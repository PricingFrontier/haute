import IoFormatEditor from "./_IoFormatEditor"
import type { OnUpdateConfig } from "./_shared"

/**
 * Editor for the `dataOutput` node — a registry-driven wrapper over the
 * native polars write/sink surface. Format options, modes, argument names
 * and missing-engine flags all come from GET /api/formats; nothing is
 * hard-coded here (io-nodes review IO12).
 */
export default function DataOutputEditor({
  config,
  onUpdate,
  accentColor,
}: {
  config: Record<string, unknown>
  onUpdate: OnUpdateConfig
  accentColor: string
}) {
  return <IoFormatEditor side="output" config={config} onUpdate={onUpdate} accentColor={accentColor} />
}
