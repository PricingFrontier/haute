import { lazy, Suspense, type ReactNode } from "react"

export const TransformEditor = lazy(() => import("./editors/TransformEditor"))
export const EdgeJoinEditor = lazy(() => import("./editors/EdgeJoinEditor"))
export const ExploreCodeEditor = lazy(() => import("./editors/ExploreCodeEditor"))
export const ExploreOverviewConfig = lazy(() => import("./editors/ExploreOverviewConfig"))
export const ExplorePivotsConfig = lazy(() => import("./editors/ExplorePivotsConfig"))
export const ExploreChartsConfig = lazy(() => import("./editors/ExploreChartsConfig"))
export const ModelScoreEditor = lazy(() => import("./editors/ModelScoreEditor"))
export const BandingEditor = lazy(() => import("./editors/BandingEditor"))
export const RatingStepEditor = lazy(() => import("./editors/RatingStepEditor"))
export const OutputEditor = lazy(() => import("./editors/OutputEditor"))
export const ExternalFileEditor = lazy(() => import("./editors/ExternalFileEditor"))
export const ApiInputEditor = lazy(() => import("./editors/ApiInputEditor"))
export const LiveSwitchEditor = lazy(() => import("./editors/LiveSwitchEditor"))
export const DataInputEditor = lazy(() => import("./editors/DataInputEditor"))
export const DataOutputEditor = lazy(() => import("./editors/DataOutputEditor"))
export const ScenarioExpanderEditor = lazy(() => import("./editors/ScenarioExpanderEditor"))
export const OptimiserApplyEditor = lazy(() => import("./editors/OptimiserApplyEditor"))
export const ConstantEditor = lazy(() => import("./editors/ConstantEditor"))
export const SubmodelEditor = lazy(() => import("./editors/SubmodelEditor"))
export const ColumnsTab = lazy(() => import("./editors/ColumnsTab"))
export const PolarsCodePanel = lazy(() => import("./editors/shared/PolarsCodePanel"))
export const ModellingConfig = lazy(() => import("./ModellingConfig"))
export const OptimiserConfig = lazy(() => import("./OptimiserConfig"))

function EditorLoadingFallback() {
  return (
    <div className="px-4 py-3" role="status" aria-live="polite" aria-label="Loading editor">
      <div
        data-testid="editor-loading"
        className="h-7 rounded-md animate-pulse"
        style={{ background: "var(--bg-hover)" }}
      />
    </div>
  )
}

export function LazyEditorBoundary({ children }: { children: ReactNode }) {
  return (
    <Suspense fallback={<EditorLoadingFallback />}>
      {children}
    </Suspense>
  )
}
