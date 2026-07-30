import {
  useId,
  useRef,
  type CSSProperties,
  type KeyboardEvent,
} from "react"

export type PreviewPanelTab<T extends string> = {
  key: T
  label: string
  disabled?: boolean
  indicator?: { kind: "warning" | "active"; label: string }
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
  const tabRefs = useRef(new Map<T, HTMLButtonElement>())
  const generatedIndicatorPrefix = useId()
  const enabledTabs = tabs.filter((tab) => !tab.disabled)
  const rovingTabKey =
    enabledTabs.find((tab) => tab.key === activeTab)?.key ?? enabledTabs[0]?.key

  const handleKeyDown = (
    event: KeyboardEvent<HTMLButtonElement>,
    tab: PreviewPanelTab<T>,
  ) => {
    if (
      tab.disabled
      || !["ArrowRight", "ArrowLeft", "Home", "End"].includes(event.key)
    ) {
      return
    }

    event.preventDefault()
    const currentIndex = enabledTabs.findIndex(
      (enabledTab) => enabledTab.key === tab.key,
    )
    const targetIndex =
      event.key === "Home"
        ? 0
        : event.key === "End"
          ? enabledTabs.length - 1
          : (
            currentIndex
            + (event.key === "ArrowRight" ? 1 : -1)
            + enabledTabs.length
          ) % enabledTabs.length
    const targetTab = enabledTabs[targetIndex]

    onChange(targetTab.key)
    tabRefs.current.get(targetTab.key)?.focus()
  }

  const containerStyle: CSSProperties = {
    borderBottom: "1px solid var(--border)",
    ...(equalWidth
      ? { gridTemplateColumns: `repeat(${tabs.length}, minmax(0, 1fr))` }
      : {}),
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
        const indicatorId = tab.indicator
          ? `${idPrefix ?? generatedIndicatorPrefix}-${tab.key}-indicator`
          : undefined
        return (
          <button
            key={tab.key}
            id={idPrefix ? `${idPrefix}-${tab.key}-tab` : undefined}
            type="button"
            role="tab"
            aria-label={tab.indicator ? tab.label : undefined}
            aria-selected={isActive}
            aria-controls={idPrefix ? `${idPrefix}-${tab.key}-pane` : undefined}
            aria-describedby={indicatorId}
            disabled={tab.disabled}
            tabIndex={
              tab.disabled ? -1 : tab.key === rovingTabKey ? 0 : -1
            }
            ref={(element) => {
              if (element) tabRefs.current.set(tab.key, element)
              else tabRefs.current.delete(tab.key)
            }}
            onClick={() => onChange(tab.key)}
            onKeyDown={(event) => handleKeyDown(event, tab)}
            className={`min-w-0 truncate px-2 py-1.5 text-[11px] font-semibold uppercase tracking-[0.08em] transition-colors ${
              equalWidth ? "" : "shrink-0"
            } ${isActive ? "" : "hover:bg-[var(--bg-hover)]"}`}
            style={{
              color: isActive ? accentColor : "var(--text-muted)",
              borderBottom: `2px solid ${
                isActive ? accentColor : "transparent"
              }`,
              background: isActive ? "var(--accent-soft)" : "transparent",
            }}
          >
            <span>{tab.label}</span>
            {tab.indicator && (
              <>
                <span
                  aria-hidden="true"
                  className="ml-1 inline-flex items-center gap-0.5 normal-case tracking-normal"
                  style={{
                    color:
                      tab.indicator.kind === "warning"
                        ? "var(--warning-strong)"
                        : "var(--success)",
                  }}
                >
                  <span>
                    {tab.indicator.kind === "warning" ? "!" : "●"}
                  </span>
                  <span>
                    {tab.indicator.kind === "warning"
                      ? "Needs attention"
                      : "Running"}
                  </span>
                </span>
                <span id={indicatorId} className="sr-only">
                  {tab.indicator.label}
                </span>
              </>
            )}
          </button>
        )
      })}
    </div>
  )
}
