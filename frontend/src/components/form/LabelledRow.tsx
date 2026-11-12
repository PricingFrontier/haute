/**
 * A side panel's rows of a label beside its control (specs/frontend-shared): the label
 * in a fixed column, so the controls line up, or, with no label, the column left blank,
 * as for tick boxes under labelled rows; and a select of named choices in one.
 */
import type { ReactNode } from "react"
import { Select } from "../../haute-ui"

const ROW = "flex items-center gap-2"
const LABEL_COLUMN = "w-14 shrink-0"

export function LabelledRow({ label, children }: { label?: string; children: ReactNode }) {
  if (label === undefined) {
    return (
      <div className={ROW}>
        <span className={LABEL_COLUMN} aria-hidden="true" />
        {children}
      </div>
    )
  }
  return (
    <label className={ROW}>
      <span className={LABEL_COLUMN} style={{ color: "var(--text-muted)" }}>
        {label}
      </span>
      {children}
    </label>
  )
}

/** A labelled select of `choices`, its label its name too unless given a `name` of its own. */
export function SelectRow<T extends string>({
  label,
  name = label,
  value,
  choices,
  onChange,
}: {
  label: string
  name?: string
  value: T
  choices: ReadonlyArray<{ value: T; label: string }>
  onChange: (value: T) => void
}) {
  return (
    <LabelledRow label={label}>
      <Select aria-label={name} value={value} onChange={(event) => onChange(event.target.value as T)}>
        {choices.map((choice) => (
          <option key={choice.value} value={choice.value}>
            {choice.label}
          </option>
        ))}
      </Select>
    </LabelledRow>
  )
}
