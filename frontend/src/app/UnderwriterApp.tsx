import { useEffect, useState } from "react"
import { fetchAppForm, fetchAppRelease, watchQuotesClosed } from "../api/app"
import { apiErrorMessage } from "../api/errors"
import QuestionDialog from "../components/QuestionDialog"
import ToastContainer from "../components/Toast"
import { waitForJob } from "../hooks/jobPollingController"
import useWorkbenchPricingStore from "../stores/useWorkbenchPricingStore"
import AppHeader from "./AppHeader"
import NewQuoteWindow from "./NewQuoteWindow"
import { openRelease } from "./openRelease"
import { quotesClosedBy } from "./quoteValues"
import QuotesPage from "./QuotesPage"
import QuoteView from "./QuoteView"
import type { WorkbenchAppQuoteStore } from "../api/types"
import { followLink, quotesQueryOf, useAppRoute, type AppRoute } from "./route"
import { BUTTON, Notice } from "./ui"
import useAppStore from "./useAppStore"
import useNewQuoteStore from "./useNewQuoteStore"

type Loaded = { ready: true } | { error: string }

/** While the quotes are closed, the release is asked for again this often. */
export const QUOTES_RECHECK_MS = 10_000

/**
 * The underwriter app (specs/workbench): the release's sheets drawn as Preview draws them,
 * with nothing of the builder. While its quotes are open it opens on the Quotes page and
 * opens each quote at its own address; while they are closed it opens no quote, new or
 * saved, saying they are unavailable, until the release says they are open again.
 */
export default function UnderwriterApp() {
  const [loaded, setLoaded] = useState<Loaded | null>(null)
  const open = useAppStore((s) => s.quotes?.open === true)
  useEffect(() => {
    let live = true
    // Whichever of the app's calls meets a closed store, the quotes close.
    watchQuotesClosed((error) => {
      if (quotesClosedBy(error) === null) return
      // Text typed into a field and not yet left is committed as leaving it commits it, so
      // the draft the open quote keeps as it closes holds it.
      const typed = document.activeElement
      if (typed instanceof HTMLElement) typed.blur()
      useAppStore.getState().noteQuotesClosed(error)
    })
    Promise.all([fetchAppRelease(), fetchAppForm()])
      .then(([release, { form, revision }]) => {
        if (!live) return
        openRelease(form, revision, release.id)
        useAppStore.getState().setRelease(release, form, revision)
        setLoaded({ ready: true })
      })
      .catch((error: unknown) => {
        if (live) setLoaded({ error: apiErrorMessage(error) })
      })
    return () => {
      live = false
      watchQuotesClosed(null)
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
      ) : open ? (
        <Routed />
      ) : (
        <QuotesClosed />
      )}
      <QuestionDialog />
      <ToastContainer />
    </div>
  )
}

/**
 * The page the address names, and the New quote window over it while one is open. A
 * creation kept in the tab, its answer lost, is offered back as the app opens.
 */
function Routed() {
  const route = useAppRoute()
  useEffect(() => {
    useNewQuoteStore.getState().offerKept()
  }, [])
  return (
    <>
      <RoutedPage route={route} />
      <NewQuoteWindow />
    </>
  )
}

/**
 * `/quotes/new`, plain or naming a quote to duplicate or renew: the New quote window opened
 * over the Quotes page, at its default list.
 */
function AskedNewQuote({ reference, option, relation }: { reference: string | null; option: number; relation: "duplicate" | "renewal" }) {
  useEffect(() => {
    const store = useNewQuoteStore.getState()
    if (reference === null) store.openNew({ addressed: true })
    else void store.openDerived({ reference, option, relation }, { addressed: true })
  }, [reference, option, relation])
  return null
}

function RoutedPage({ route }: { route: AppRoute }) {
  if (route.page === "new" && route.from?.relation !== "alternative") {
    const { from } = route
    return (
      <>
        <AppHeader label="Quotes toolbar" />
        <QuotesPage query={quotesQueryOf("")} />
        <AskedNewQuote
          reference={from?.reference ?? null}
          option={from?.option ?? 1}
          relation={from?.relation === "renewal" ? "renewal" : "duplicate"}
        />
      </>
    )
  }
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

/**
 * The quotes closed: no quote opens, new or saved, while the store's tables do not fit, it
 * cannot be reached or no environment is named, the release asked for again every
 * `QUOTES_RECHECK_MS` until it says they are open, when the page it is on shows.
 */
function QuotesClosed() {
  const message = useAppStore((s) => s.quotes?.message ?? null)
  useEffect(() => {
    const ended = new AbortController()
    const note = (quotes: WorkbenchAppQuoteStore | null) => {
      if (quotes !== null) useAppStore.getState().setQuotes(quotes)
    }
    waitForJob({
      // A release that cannot be read now is asked for again on the next tick.
      poll: () => fetchAppRelease().then((release) => release.quotes, () => null),
      isTerminal: (quotes) => quotes?.open === true,
      intervalMs: QUOTES_RECHECK_MS,
      signal: ended.signal,
      onStatus: note,
    })
      .then(note)
      // Ended as the view goes, or past the polling's lifetime: the page stays as it is.
      .catch(() => undefined)
    return () => ended.abort()
  }, [])
  return (
    <>
      <AppHeader label="Quotes toolbar" />
      <main className="flex flex-1 items-start justify-center p-8" aria-label="Quotes">
        <Notice tone="warning" testId="notice-quotes-closed">
          Quotes are unavailable{message === null ? "." : `: ${message}`}
        </Notice>
      </main>
    </>
  )
}
