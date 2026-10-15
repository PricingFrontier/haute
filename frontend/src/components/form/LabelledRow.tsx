/**
 * A side panel's rows of a label beside its control (specs/frontend-shared): the label
 * in a fixed column, so the controls line up, or, with no label, the column left blank,
 * as for tick boxes under labelled rows; and a select of named choices in one.
 */
import type { ReactNode } from "react"
import { FIELD_CLASS, INPUT_STYLE } from "./fieldStyle"

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

/** A labelled select of `choices`, its label its name too. */
export function SelectRow<T extends string>({
  label,
  value,
  choices,
  onChange,
}: {
  label: string
  value: T
  choices: ReadonlyArray<{ value: T; label: string }>
  onChange: (value: T) => void
}) {
  return (
    <LabelledRow label={label}>
      <select aria-label={label} value={value} onChange={(event) => onChange(event.target.value as T)} className={FIELD_CLASS} style={INPUT_STYLE}>
        {choices.map((choice) => (
          <option key={choice.value} value={choice.value}>
            {choice.label}
          </option>
        ))}
      </select>
    </LabelledRow>
  )
}
