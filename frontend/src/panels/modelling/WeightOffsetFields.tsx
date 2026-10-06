import type { OnUpdateConfig } from "../editors"
import { ColumnSelector } from "./ColumnSelector"
import type { ModelColumn } from "./modelColumns"
import { OffsetFieldLabel } from "./OffsetFieldLabel"

/** The optional weight and offset column pickers every model family's target configuration shows. */
export default function WeightOffsetFields({
  weight,
  offset,
  weightColumns,
  offsetColumns,
  onUpdate,
}: {
  weight: string
  offset: string
  weightColumns: ModelColumn[]
  offsetColumns: ModelColumn[]
  onUpdate: OnUpdateConfig
}) {
  return (
    <>
      <div>
        <label className="text-[13px]" style={{ color: "var(--text-secondary)" }}>
          Weight column (optional)
        </label>
        <ColumnSelector
          label="Weight column"
          value={weight}
          columns={weightColumns}
          onChange={(next) => onUpdate("weight", next)}
          optional
        />
      </div>
      <div>
        <OffsetFieldLabel />
        <ColumnSelector
          label="Offset column"
          value={offset}
          columns={offsetColumns}
          onChange={(next) => onUpdate("offset", next || null)}
          optional
        />
      </div>
    </>
  )
}
