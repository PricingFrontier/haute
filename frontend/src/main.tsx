import { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import "./index.css"
import App from "./App"
import { bootstrapHauteSession } from "./api/client"
import { ErrorBoundary } from "./components/ErrorBoundary"

const root = createRoot(document.getElementById("root")!)

function renderApplication() {
  root.render(
    <StrictMode>
      <ErrorBoundary name="Root">
        <App />
      </ErrorBoundary>
    </StrictMode>,
  )
}

function renderBootstrapFailure() {
  root.render(
    <main className="flex min-h-screen items-center justify-center bg-slate-950 p-6 text-slate-100">
      <section className="max-w-md rounded-lg border border-slate-700 bg-slate-900 p-6 text-center">
        <h1 className="text-lg font-semibold">Could not start the local Haute session</h1>
        <p className="mt-2 text-sm text-slate-300">
          Check that the local server is running, then reload this page.
        </p>
        <button
          className="mt-4 rounded bg-sky-600 px-4 py-2 text-sm font-medium hover:bg-sky-500"
          onClick={() => window.location.reload()}
          type="button"
        >
          Reload
        </button>
      </section>
    </main>,
  )
}

void bootstrapHauteSession().then(renderApplication).catch(renderBootstrapFailure)
