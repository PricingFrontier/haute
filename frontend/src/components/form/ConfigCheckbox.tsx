import { useId } from "react"

interface ConfigCheckboxProps {
  checked: boolean;
  onChange: (checked: boolean) => void;
  label: string;
  /** The label names the box without showing: a box whose row already says what it is. */
  labelHidden?: boolean;
  disabled?: boolean;
  id?: string;
}

export default function ConfigCheckbox({
  checked,
  onChange,
  label,
  labelHidden = false,
  disabled = false,
  id: externalId,
}: ConfigCheckboxProps) {
  const generatedId = useId()
  const checkboxId = externalId ?? generatedId

  return (
    <div className="flex items-center gap-2">
      <input
        id={checkboxId}
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        disabled={disabled}
        className="rounded border focus:ring-2 focus:ring-offset-0 disabled:opacity-50 disabled:cursor-not-allowed"
        style={{
          accentColor: "var(--accent)",
          borderColor: "var(--border)",
        }}
      />
      <label
        htmlFor={checkboxId}
        className={labelHidden ? "sr-only" : "text-xs select-none"}
        style={{
          color: disabled ? "var(--text-muted)" : "var(--text-secondary)",
          cursor: disabled ? "not-allowed" : "pointer",
        }}
      >
        {label}
      </label>
    </div>
  )
}
