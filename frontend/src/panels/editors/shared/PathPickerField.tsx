import { useState } from "react"
import { Check } from "lucide-react"
import { CommittedTextField } from "../../../components/form"
import { FileBrowser } from "../_shared"

export default function PathPickerField({
  label,
  sublabel,
  value,
  onSelect,
  extensions,
  manualEntry = false,
  testIdPrefix = "path-picker",
}: {
  label: string
  sublabel?: string
  value: string
  onSelect: (path: string) => void
  extensions?: string
  /** Show a committed text field above the browser for hand-typed paths. */
  manualEntry?: boolean
  testIdPrefix?: string
}) {
  const [expanded, setExpanded] = useState(false)
  const showBrowser = !value || expanded

  return (
    <div data-testid={testIdPrefix}>
      <label className="text-[11px] font-bold uppercase tracking-[0.08em] mb-1.5 block" style={{ color: "var(--text-muted)" }}>
        {label}
        {sublabel && <span className="ml-1.5 normal-case tracking-normal font-normal">{sublabel}</span>}
      </label>
      {value && (
        <div
          className="px-2.5 py-2 rounded-lg flex items-center gap-2"
          style={{ background: "var(--banner-success-bg)", border: "1px solid var(--banner-success-border)" }}
        >
          <Check size={14} style={{ color: "var(--banner-success-text)" }} className="shrink-0" />
          <span className="text-xs font-mono truncate flex-1" style={{ color: "var(--banner-success-data)" }}>
            {value}
          </span>
          <button
            type="button"
            data-testid="file-change-btn"
            onClick={() => setExpanded(!expanded)}
            className="shrink-0 text-[11px] font-semibold px-2 py-0.5 rounded transition-colors"
            style={{ color: "var(--banner-success-action)" }}
          >
            {expanded ? "close" : "change"}
          </button>
        </div>
      )}
      {showBrowser && (
        <div className="mt-2 space-y-2">
          {manualEntry && (
            <CommittedTextField
              aria-label={label}
              value={value}
              onCommit={(next) => {
                onSelect(next)
                setExpanded(false)
              }}
              className="focus-ring w-full px-2.5 py-1.5 text-xs font-mono rounded-lg"
              style={{ background: "var(--bg-input)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
            />
          )}
          <FileBrowser
            currentPath={value || undefined}
            extensions={extensions}
            showSelectionSummary={false}
            onSelect={(path) => {
              onSelect(path)
              setExpanded(false)
            }}
          />
        </div>
      )}
    </div>
  )
}
