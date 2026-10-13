import { useEffect, useState } from "react"
import { fetchAppForm, fetchAppRelease } from "../api/app"
import { apiErrorMessage } from "../api/errors"
import ToastContainer from "../components/Toast"
import { ZoomInOut } from "../haute-ui"
import useWorkbenchPreviewStore from "../stores/useWorkbenchPreviewStore"
import useWorkbenchPricingStore from "../stores/useWorkbenchPricingStore"
import useWorkbenchViewStore, { ZOOM_STEP } from "../stores/useWorkbenchViewStore"
import PageTabs from "../workbench/PageTabs"
import SheetCanvas from "../workbench/SheetCanvas"
import AppHeader from "./AppHeader"
import { openRelease } from "./openRelease"
import QuotesPage from "./QuotesPage"
import QuoteView from "./QuoteView"
import { followLink, useAppRoute } from "./route"
import { BUTTON, Notice, QuestionDialog } from "./ui"
import useAppStore from "./useAppStore"

type Loaded = { quotes: boolean } | { error: string }

/**
 * The underwriter app (specs/workbench): the release's sheets drawn as Preview draws them,
 * with nothing of the builder. With a quote store it opens on the Quotes page and opens each
 * quote at its own address; without one it is one quote, priced as it is typed, keeping
 * nothing.
 */
export default function UnderwriterApp() {
  const [loaded, setLoaded] = useState<Loaded | null>(null)
  useEffect(() => {
    let live = true
    Promise.all([fetchAppRelease(), fetchAppForm()])
      .then(([release, { form, revision }]) => {
        if (!live) return
        openRelease(form, revision, release.id)
        useAppStore.getState().setRelease(release, form, revision)
        setLoaded({ quotes: release.quotes })
      })
      .catch((error: unknown) => {
        if (live) setLoaded({ error: apiErrorMessage(error) })
      })
    return () => {
      live = false
      useWorkbenchPricingStore.getState().setQuotePricer(null)
    }
  }, [])

  return (
    <div className="flex h-screen flex-col" style={{ background: "var(--bg-base)" }}>
      {loaded === null ? (
        <div role="status" className="flex flex-1 items-center justify-center text-[13px]" style={{ color: "var(--text-muted)" }}>
          Loading the sheets…
        </div>
      ) : "error" in loaded ? (
        <div role="alert" className="flex flex-1 items-center justify-center p-8 text-[13px]" style={{ color: "var(--danger)" }}>
          Could not open the sheets: {loaded.error}
        </div>
      ) : loaded.quotes ? (
        <Routed />
      ) : (
        <OneQuote />
      )}
      <QuestionDialog />
      <ToastContainer />
    </div>
  )
}

/** The page the address names. */
function Routed() {
  const route = useAppRoute()
  if (route.page === "quotes") {
    return (
      <>
        <AppHeader label="Quotes toolbar" />
        <QuotesPage query={route.query} />
      </>
    )
  }
  if (route.page === "missing") {
    return (
      <>
        <AppHeader label="Quotes toolbar" />
        <div role="alert" className="flex flex-1 flex-col items-center justify-center gap-2 text-[13px]" style={{ color: "var(--text-secondary)" }}>
          There is no page at this address.
          <a href="/" onClick={(event) => followLink(event, "/")} className={BUTTON}>
            Go to Quotes
          </a>
        </div>
      </>
    )
  }
  return <QuoteView route={route} />
}

/** An app with no quote store: one quote, priced as it is typed, kept nowhere. */
function OneQuote() {
  const error = useWorkbenchPreviewStore((s) => s.error)
  const zoomBy = useWorkbenchViewStore((s) => s.zoomBy)

  return (
    <>
      <AppHeader label="Quote toolbar">
        <ZoomInOut onZoomIn={() => zoomBy(ZOOM_STEP)} onZoomOut={() => zoomBy(-ZOOM_STEP)} />
        {error !== null && (
          <span data-testid="app-pricing-error" className="max-w-96 truncate text-xs" style={{ color: "var(--warning)" }} title={error}>
            {error}
          </span>
        )}
      </AppHeader>
      <main className="relative flex min-h-0 min-w-0 flex-1 flex-col" aria-label="Sheets">
        <Notice tone="info" testId="notice-no-store">
          This app keeps no quotes: it was started without a quote store. A quote priced here is not saved.
        </Notice>
        <div className="px-4 pt-2">
          <PageTabs readOnly />
        </div>
        <SheetCanvas />
      </main>
    </>
  )
}
