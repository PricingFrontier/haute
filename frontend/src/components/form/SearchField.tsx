import { Search, X } from "lucide-react"

interface SearchFieldProps {
  value: string
  onChange: (value: string) => void
  /** What it searches, as its accessible name and its placeholder. */
  label: string
  className?: string
}

/**
 * A search box: a magnifier, the text and, while it holds any, Clear. Escape in it
 * empties it too, its default prevented so nothing else hears it.
 */
export default function SearchField({ value, onChange, label, className = "" }: SearchFieldProps) {
  return (
    <div
      className={`flex min-w-0 items-center gap-1.5 rounded-md px-2 py-1 focus-within:brightness-110 ${className}`.trim()}
      style={{ background: "var(--bg-input)", border: "1px solid var(--border)" }}
    >
      <Search size={12} aria-hidden="true" className="shrink-0" style={{ color: "var(--text-muted)" }} />
      <input
        type="text"
        aria-label={label}
        placeholder={label}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={(event) => {
          if (event.key !== "Escape" || value === "") return
          event.preventDefault()
          onChange("")
        }}
        className="min-w-0 flex-1 bg-transparent text-xs outline-none"
        style={{ color: "var(--text-primary)" }}
      />
      {value !== "" && (
        <button
          type="button"
          onClick={() => onChange("")}
          aria-label={`Clear ${label.toLowerCase()}`}
          title="Clear"
          className="focus-ring hover-bg grid size-4 shrink-0 place-items-center rounded"
          style={{ color: "var(--text-muted)" }}
        >
          <X size={11} aria-hidden="true" />
        </button>
      )}
    </div>
  )
}
