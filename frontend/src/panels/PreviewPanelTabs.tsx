import type { CSSProperties } from "react"

export type PreviewPanelTab<T extends string> = {
  key: T
  label: string
}

type PreviewPanelTabsProps<T extends string> = {
  tabs: readonly PreviewPanelTab<T>[]
  activeTab: T
  onChange: (tab: T) => void
  ariaLabel: string
  accentColor?: string
  equalWidth?: boolean
  idPrefix?: string
}

export default function PreviewPanelTabs<T extends string>({
  tabs,
  activeTab,
  onChange,
  ariaLabel,
  accentColor = "var(--accent)",
  equalWidth = false,
  idPrefix,
}: PreviewPanelTabsProps<T>) {
  const containerStyle: CSSProperties = {
    borderBottom: "1px solid var(--border)",
    ...(equalWidth ? { gridTemplateColumns: `repeat(${tabs.length}, minmax(0, 1fr))` } : {}),
  }

  return (
    <div
      role="tablist"
      aria-label={ariaLabel}
      className={`${equalWidth ? "grid" : "flex overflow-x-auto"} shrink-0`}
      style={containerStyle}
    >
      {tabs.map((tab) => {
        const isActive = activeTab === tab.key
        return (
          <button
            key={tab.key}
            id={idPrefix ? `${idPrefix}-${tab.key}-tab` : undefined}
            type="button"
            role="tab"
            aria-selected={isActive}
            aria-controls={idPrefix ? `${idPrefix}-${tab.key}-pane` : undefined}
            onClick={() => onChange(tab.key)}
            className={`min-w-0 truncate px-2 py-1.5 text-[11px] font-semibold uppercase tracking-[0.08em] transition-colors ${
              equalWidth ? "" : "shrink-0"
            } ${isActive ? "" : "hover:bg-[var(--bg-hover)]"}`}
            style={{
              color: isActive ? accentColor : "var(--text-muted)",
              borderBottom: `2px solid ${isActive ? accentColor : "transparent"}`,
              background: isActive ? "var(--accent-soft)" : "transparent",
            }}
          >
            {tab.label}
          </button>
        )
      })}
    </div>
  )
}
