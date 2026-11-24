import { useEffect, useState } from "react"
import { fetchAppForm, fetchAppRelease } from "../api/app"
import { apiErrorMessage } from "../api/errors"
import QuestionDialog from "../components/QuestionDialog"
import ToastContainer from "../components/Toast"
import { useRequestKind } from "../hooks/useRequestKind"
import useWorkbenchPricingStore from "../stores/useWorkbenchPricingStore"
import AppHeader from "./AppHeader"
import NewQuoteWindow from "./NewQuoteWindow"
import { openRelease } from "./openRelease"
import QuotesPage from "./QuotesPage"
import QuoteView from "./QuoteView"
import { ask, newest } from "../utils/answers"
import { followLink, quotesQueryOf, useAppRoute, type AppRoute } from "./route"
import { BUTTON } from "./ui"
import useAppStore from "./useAppStore"
import useNewQuoteStore, { type WindowOrigin } from "./useNewQuoteStore"

type Loaded = { ready: true } | { error: string }

/**
 * The underwriter app (specs/workbench): the release's sheets drawn as Preview draws them,
 * with nothing of the builder. It opens on the Quotes page and opens each quote at its own
 * address whatever its quote store holds: a use of the store it cannot have says why where
 * it was met, naming each table and column that does not fit.
 */
export default function UnderwriterApp() {
  const [loaded, setLoaded] = useState<Loaded | null>(null)
  const opening = useRequestKind()
  useEffect(() => {
    const asked = ask([newest(opening)])
    void asked.answer(Promise.all([fetchAppRelease(), fetchAppForm()]), {
      ok: ([release, { form, revision }]) => {
        openRelease(form, revision, release.id)
        useAppStore.getState().setRelease(release, form, revision)
        setLoaded({ ready: true })
      },
      failed: (error) => setLoaded({ error: apiErrorMessage(error) }),
    })
    return () => {
      asked.release()
      useWorkbenchPricingStore.getState().setQuotePricer(null)
    }
  }, [opening])

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
      ) : (
        <Routed />
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
 * `/quotes/new`, plain or naming a quote to duplicate, renew or make an alternative of: the
 * New quote window opened over the Quotes page, at its default list.
 */
function AskedNewQuote({ reference, option, relation }: { reference: string | null; option: number; relation: WindowOrigin["relation"] }) {
  useEffect(() => {
    const store = useNewQuoteStore.getState()
    if (reference === null) store.openNew({ addressed: true })
    else void store.openDerived({ reference, option, relation }, { addressed: true })
  }, [reference, option, relation])
  return null
}

function RoutedPage({ route }: { route: AppRoute }) {
  if (route.page === "new") {
    const { from } = route
    return (
      <>
        <AppHeader label="Quotes toolbar" />
        <QuotesPage query={quotesQueryOf("")} />
        <AskedNewQuote reference={from?.reference ?? null} option={from?.option ?? 1} relation={from?.relation ?? "duplicate"} />
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
