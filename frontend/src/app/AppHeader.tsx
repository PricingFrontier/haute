import type { ReactNode } from "react"
import { followLink } from "./route"
import useAppStore from "./useAppStore"

const BRAND_STYLE = { width: "auto", minWidth: 165, maxWidth: 360, paddingRight: 8 } as const

/**
 * The underwriter app's bar (specs/workbench): the pipeline's name over the release, a link
 * to the Quotes page when the app keeps quotes, what the page showing puts there, and who
 * is signed in.
 */
export default function AppHeader({ children, label }: { children?: ReactNode; label: string }) {
  const release = useAppStore((s) => s.release)
  if (release === null) return null
  return (
    <header role="toolbar" aria-label={label} className="toolbar flex-wrap gap-y-2 [&>div]:shrink-0">
      {/* As wide as the pipeline's name, unlike Haute's own brand, within bounds. */}
      <div className="toolbar-brand" style={BRAND_STYLE} data-testid="app-release">
        <div className="toolbar-brand-mark min-w-0">
          <h1 className="toolbar-brand-name max-w-full truncate" style={{ lineHeight: 1.2 }} title={release.pipeline_name}>
            {release.quotes ? (
              <a href="/" onClick={(event) => followLink(event, "/")} style={{ color: "inherit" }}>
                {release.pipeline_name || "Quotes"}
              </a>
            ) : (
              release.pipeline_name || "Quote"
            )}
          </h1>
          <span className="toolbar-brand-version" title={`Built ${release.created_at} by Haute ${release.haute_version}`}>
            release {release.id}
          </span>
        </div>
      </div>
      <div className="ml-2.5 flex min-w-0 flex-wrap items-center gap-2.5">{children}</div>
      <span className="ml-auto pr-3 text-xs" style={{ color: "var(--text-muted)" }} data-testid="app-principal">
        Signed in as {release.principal}
      </span>
    </header>
  )
}
