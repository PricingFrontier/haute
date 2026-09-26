/**
 * The results workspace shell modelling and optimiser results share.
 *
 * It owns the Focus view (a ModalShell that keeps this same subtree mounted
 * while docked), the preview frame with a remembered docked height, an
 * optional progress bar, the results-style tab strip, a notices slot, a
 * provenance slot, the active tab's intro and the tabpanel that holds the
 * caller's content. The caller owns the tab, the height and the content.
 */

import { useState, type CSSProperties, type ReactNode } from "react"
import { Maximize2, Minimize2 } from "lucide-react"
import ModalShell from "../components/ModalShell"
import { MODEL_COLORS } from "../theme/colors"
import PreviewPanelFrame from "./PreviewPanelFrame"
import PreviewPanelTabs, { type PreviewPanelTab } from "./PreviewPanelTabs"
import "./modelling/validation.css"

export type ResultsWorkspaceAccent = {
  /** The accent colour: active tab, Focus view toggle, progress. */
  color: string
  /** Its soft tint: progress track, selected rows. */
  soft: string
}

export type ResultsWorkspaceIntro = { title: string; description: ReactNode }

const MODEL_ACCENT: ResultsWorkspaceAccent = {
  color: MODEL_COLORS.accent,
  soft: MODEL_COLORS.accentSoft,
}

type ResultsWorkspaceProps<T extends string> = {
  /** The Focus view dialog's accessible name. */
  ariaLabel: string
  /** Prefix of every tab and pane id: `<idPrefix>-<tab>-tab` / `-pane`. */
  idPrefix: string
  tabsAriaLabel: string
  tabs: readonly PreviewPanelTab<T>[]
  activeTab: T
  onTabChange: (tab: T) => void
  nodeLabel: string
  nodeType: string
  onRefresh?: () => void
  subtitle?: ReactNode
  collapsedMeta: ReactNode
  "data-testid": string
  height: number
  onHeightChange: (height: number) => void
  accent?: ResultsWorkspaceAccent
  /** Header actions shown before the Focus view toggle. */
  headerActions?: ReactNode
  /** Progress from 0 to 1 while work runs; null or omitted hides the bar. */
  progress?: number | null
  /** Strips about the result's state, below the tabs (e.g. a stale result). */
  notices?: ReactNode
  /** What the figures are; shown on every tab. */
  provenance?: ReactNode
  intro: ResultsWorkspaceIntro | null
  children: ReactNode
}

export default function ResultsWorkspace<T extends string>({
  ariaLabel,
  idPrefix,
  tabsAriaLabel,
  tabs,
  activeTab,
  onTabChange,
  nodeLabel,
  nodeType,
  onRefresh,
  subtitle,
  collapsedMeta,
  "data-testid": testId,
  height,
  onHeightChange,
  accent = MODEL_ACCENT,
  headerActions,
  progress,
  notices,
  provenance,
  intro,
  children,
}: ResultsWorkspaceProps<T>) {
  const [focused, setFocused] = useState(false)
  const paneStyle = {
    "--results-accent": accent.color,
    "--results-accent-soft": accent.soft,
  } as CSSProperties

  return (
    <ModalShell
      active={focused}
      ariaLabel={ariaLabel}
      onClose={() => setFocused(false)}
      width="w-[calc(100vw-24px)] h-[calc(100dvh-24px)]"
    >
      <PreviewPanelFrame
        nodeLabel={nodeLabel}
        nodeType={nodeType}
        onRefresh={onRefresh}
        subtitle={subtitle}
        collapsedMeta={collapsedMeta}
        data-testid={testId}
        initialHeight={height}
        onHeightChange={onHeightChange}
        focused={focused}
        actions={
          <>
            {headerActions}
            <button
              type="button"
              onClick={() => setFocused(!focused)}
              className="inline-flex items-center gap-1.5 rounded px-2 py-1 text-xs hover:bg-[var(--bg-hover)] focus-ring"
              style={{ color: accent.color }}
            >
              {focused ? (
                <Minimize2 size={14} aria-hidden="true" />
              ) : (
                <Maximize2 size={14} aria-hidden="true" />
              )}
              {focused ? "Exit focus view" : "Focus view"}
            </button>
          </>
        }
      >
        {progress != null && (
          <div className="h-1 w-full shrink-0" style={{ background: accent.soft }}>
            <div
              className="h-full transition-all duration-300"
              style={{
                width: `${Math.max(progress * 100, 2)}%`,
                background: accent.color,
              }}
            />
          </div>
        )}

        <div className="shrink-0 overflow-x-auto">
          <PreviewPanelTabs
            tabs={tabs}
            activeTab={activeTab}
            onChange={onTabChange}
            ariaLabel={tabsAriaLabel}
            accentColor={accent.color}
            idPrefix={idPrefix}
            appearance="results"
          />
        </div>

        {notices}

        {provenance && (
          <div
            className="flex shrink-0 flex-wrap items-center gap-x-3 gap-y-1 border-b px-4 py-2 text-xs"
            style={{ borderColor: "var(--border)", color: "var(--text-secondary)" }}
          >
            {provenance}
          </div>
        )}

        <div
          key={activeTab}
          id={`${idPrefix}-${activeTab}-pane`}
          role="tabpanel"
          aria-labelledby={`${idPrefix}-${activeTab}-tab`}
          tabIndex={0}
          className="validation-workspace flex-1 min-h-0 overflow-auto p-4 focus-ring"
          style={paneStyle}
        >
          {intro && (
            <div className="mb-4">
              <h3 className="text-base font-semibold" style={{ color: "var(--text-primary)" }}>
                {intro.title}
              </h3>
              <p className="mt-1 text-xs leading-relaxed" style={{ color: "var(--text-muted)" }}>
                {intro.description}
              </p>
            </div>
          )}
          {children}
        </div>
      </PreviewPanelFrame>
    </ModalShell>
  )
}
