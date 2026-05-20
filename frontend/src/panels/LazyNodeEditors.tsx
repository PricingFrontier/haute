import { lazy, Suspense, type ReactNode } from "react"

export const DataSourceEditor = lazy(() => import("./editors/DataSourceEditor"))
export const TransformEditor = lazy(() => import("./editors/TransformEditor"))
export const ExploreCodeEditor = lazy(() => import("./editors/ExploreCodeEditor"))
export const ExploreOverviewConfig = lazy(() => import("./editors/ExploreOverviewConfig"))
export const ModelScoreEditor = lazy(() => import("./editors/ModelScoreEditor"))
export const BandingEditor = lazy(() => import("./editors/BandingEditor"))
export const RatingStepEditor = lazy(() => import("./editors/RatingStepEditor"))
export const OutputEditor = lazy(() => import("./editors/OutputEditor"))
export const ExternalFileEditor = lazy(() => import("./editors/ExternalFileEditor"))
export const ApiInputEditor = lazy(() => import("./editors/ApiInputEditor"))
export const LiveSwitchEditor = lazy(() => import("./editors/LiveSwitchEditor"))
export const SinkEditor = lazy(() => import("./editors/SinkEditor"))
export const ScenarioExpanderEditor = lazy(() => import("./editors/ScenarioExpanderEditor"))
export const OptimiserApplyEditor = lazy(() => import("./editors/OptimiserApplyEditor"))
export const ConstantEditor = lazy(() => import("./editors/ConstantEditor"))
export const SubmodelEditor = lazy(() => import("./editors/SubmodelEditor"))
export const ColumnsTab = lazy(() => import("./editors/ColumnsTab"))
export const GroupedColumnsTab = lazy(() => import("./editors/GroupedColumnsTab"))
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
