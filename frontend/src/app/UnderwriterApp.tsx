import { Calculator, Eraser } from "lucide-react"
import { useEffect, useState } from "react"
import { fetchAppForm, fetchAppRelease } from "../api/app"
import { apiErrorMessage } from "../api/errors"
import type { WorkbenchAppReleaseResponse } from "../api/types"
import { ToolbarButton, ToolbarColumn, ZoomInOut } from "../haute-ui"
import useWorkbenchFormStore from "../stores/useWorkbenchFormStore"
import useWorkbenchPreviewStore from "../stores/useWorkbenchPreviewStore"
import useWorkbenchPricingStore from "../stores/useWorkbenchPricingStore"
import useWorkbenchViewStore, { ZOOM_STEP } from "../stores/useWorkbenchViewStore"
import { quoteFilled } from "../utils/sheetValues"
import { quotedSchema, sheetVisibility } from "../utils/sheetVisibility"
import PageTabs from "../workbench/PageTabs"
import SheetCanvas from "../workbench/SheetCanvas"
import { openRelease } from "./openRelease"

type Loaded = { release: WorkbenchAppReleaseResponse } | { error: string }

const BRAND_STYLE = { width: "auto", minWidth: 165, maxWidth: 360, paddingRight: 8 } as const

/**
 * The underwriter app (specs/workbench): the release's sheets drawn as Preview draws them,
 * with nothing of the builder, under a bar holding the pipeline and the release, Price over
 * Clear, the zoom, why the quote has no price, and who is signed in.
 */
export default function UnderwriterApp() {
  const [loaded, setLoaded] = useState<Loaded | null>(null)
  useEffect(() => {
    let live = true
    Promise.all([fetchAppRelease(), fetchAppForm()])
      .then(([release, { form, revision }]) => {
        if (!live) return
        openRelease(form, revision)
        setLoaded({ release })
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
      ) : (
        <>
          <AppBar release={loaded.release} />
          <main className="relative flex min-h-0 min-w-0 flex-1 flex-col" aria-label="Sheets">
            <div className="px-4 pt-2">
              <PageTabs readOnly />
            </div>
            <SheetCanvas />
          </main>
        </>
      )}
    </div>
  )
}

function AppBar({ release }: { release: WorkbenchAppReleaseResponse }) {
  const form = useWorkbenchFormStore((s) => s.form)
  const quote = useWorkbenchPreviewStore((s) => s.quote)
  const pricing = useWorkbenchPreviewStore((s) => s.pricing)
  const error = useWorkbenchPreviewStore((s) => s.error)
  const priceQuote = useWorkbenchPreviewStore((s) => s.priceQuote)
  const clear = useWorkbenchPreviewStore((s) => s.clear)
  const zoomBy = useWorkbenchViewStore((s) => s.zoomBy)
  // Judged as Preview's toolbar judges them: nothing the sheets show typed is nothing to price.
  const empty = form === null || !quoteFilled(form.schema, quote)
  const blank = form === null || !quoteFilled(quotedSchema(form.schema, sheetVisibility(form, quote).hidden), quote)

  return (
    <header role="toolbar" aria-label="Quote toolbar" className="toolbar flex-wrap gap-y-2 [&>div]:shrink-0">
      {/* As wide as the pipeline's name, unlike Haute's own brand, within bounds. */}
      <div className="toolbar-brand" style={BRAND_STYLE} data-testid="app-release">
        <div className="toolbar-brand-mark min-w-0">
          <h1 className="toolbar-brand-name max-w-full truncate" style={{ lineHeight: 1.2 }} title={release.pipeline_name}>
            {release.pipeline_name || "Quote"}
          </h1>
          <span className="toolbar-brand-version" title={`Built ${release.created_at} by Haute ${release.haute_version}`}>
            release {release.id}
          </span>
        </div>
      </div>
      <div className="ml-2.5 flex flex-wrap items-center gap-2.5">
        <ToolbarColumn role="group" aria-label="Quote">
          <ToolbarButton
            icon={Calculator}
            onClick={() => { void priceQuote() }}
            disabled={pricing || blank}
            title={blank ? "Type the quote first" : "Check the quote and price it"}
          >
            Price
          </ToolbarButton>
          <ToolbarButton
            icon={Eraser}
            onClick={() => { if (window.confirm("Clear the quote? Every cell will be emptied.")) clear() }}
            disabled={empty}
            title="Start a new quote"
          >
            Clear
          </ToolbarButton>
        </ToolbarColumn>
        <ZoomInOut onZoomIn={() => zoomBy(ZOOM_STEP)} onZoomOut={() => zoomBy(-ZOOM_STEP)} />
        {error !== null && (
          <span data-testid="app-pricing-error" className="max-w-96 truncate text-xs" style={{ color: "var(--warning)" }} title={error}>
            {error}
          </span>
        )}
      </div>
      <span className="ml-auto pr-3 text-xs" style={{ color: "var(--text-muted)" }} data-testid="app-principal">
        Signed in as {release.principal}
      </span>
    </header>
  )
}
