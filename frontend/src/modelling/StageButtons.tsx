/** A row of stage buttons along the top of a pane, as Paths shows its stages. */
export type StageButton<T extends string> = { key: T; label: string; disabled?: boolean; title?: string }

export function StageButtons<T extends string>({ label, options, value, onChange }: {
  label: string
  options: readonly StageButton<T>[]
  value: T
  onChange: (value: T) => void
}) {
  return (
    <div className="inline-flex gap-1 rounded-lg p-1" style={{ background: "var(--bg-panel)" }} role="tablist" aria-label={label}>
      {options.map((option) => <button key={option.key} type="button" role="tab" aria-selected={value === option.key}
        disabled={option.disabled} title={option.title}
        className="focus-ring rounded-md px-3 py-1.5 text-[12px] font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-40"
        onClick={() => onChange(option.key)}
        style={{ color: value === option.key ? "var(--text-primary)" : "var(--text-muted)", background: value === option.key ? "var(--bg-hover)" : "transparent" }}>
        {option.label}
      </button>)}
    </div>
  )
}
