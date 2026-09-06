import { NODE_TYPES } from "../utils/nodeTypes"
import type { NodeTypeValue } from "../utils/nodeTypes"
import type { ExplorePane, ModellingPane } from "../stores/useUIStore"
import {
  ApiInputEditor,
  BandingEditor,
  ConstantEditor,
  DataInputEditor,
  DataOutputEditor,
  EdgeJoinEditor,
  ExploreChartsConfig,
  ExploreCodeEditor,
  ExploreOverviewConfig,
  ExplorePivotsConfig,
  ExternalFileEditor,
  LiveSwitchEditor,
  ModelScoreEditor,
  ModellingConfig,
  OptimiserApplyEditor,
  OptimiserConfig,
  OutputEditor,
  RatingStepEditor,
  ScenarioExpanderEditor,
  SubmodelEditor,
  SubmodelPortEditor,
  TransformEditor,
} from "./LazyNodeEditors"
import type { LoadPivotFilterMembers } from "./editors/ExplorePivotsConfig"
import type { InputSource, OnReplaceConfig, OnUpdateConfig, SimpleNode } from "./editors"

type Column = { name: string; dtype: string }

const EXPLORE_PANES = [
  { key: "code", label: "Polars Code" },
  { key: "overview", label: "Overview" },
  { key: "pivots", label: "Pivots" },
  { key: "charts", label: "Charts" },
  { key: "export", label: "Export" },
] as const satisfies readonly { key: ExplorePane; label: string }[]

export type NodeConfigEditorProps = {
  nodeType: NodeTypeValue
  config: Record<string, unknown>
  configWithNodeId: Record<string, unknown>
  node: SimpleNode
  onUpdateConfig: OnUpdateConfig
  onReplaceConfig: OnReplaceConfig
  inputSources: InputSource[]
  upstreamColumns: Column[]
  pivotColumns: Column[]
  activeExplorePane: ExplorePane
  activeModellingPane: ModellingPane
  onDeleteEdge?: (edgeId: string) => void
  onDeleteSubmodelInputPort?: (portName: string) => void
  onSwapEdgeJoinInputs?: (nodeId: string) => void
  onShowPivots: () => void
  errorLine?: number | null
  previewRows?: Record<string, unknown>[]
  selectedPreviewLoading?: boolean
  loadPivotFilterMembers: LoadPivotFilterMembers
  exploreConfigHash: string | null
  reservedApiInputFrameLabels: Set<string>
  accentColor: string
}

/** Pure known-node editor switch used by the node panel. */
export function NodeConfigEditor({
  nodeType,
  config,
  configWithNodeId,
  node,
  onUpdateConfig,
  onReplaceConfig,
  inputSources,
  upstreamColumns,
  pivotColumns,
  activeExplorePane,
  activeModellingPane,
  onDeleteEdge,
  onDeleteSubmodelInputPort,
  onSwapEdgeJoinInputs,
  onShowPivots,
  errorLine,
  previewRows,
  selectedPreviewLoading,
  loadPivotFilterMembers,
  exploreConfigHash,
  reservedApiInputFrameLabels,
  accentColor,
}: NodeConfigEditorProps) {
  const activeExplorePaneMeta = EXPLORE_PANES.find((pane) => pane.key === activeExplorePane) ?? EXPLORE_PANES[0]
  const nodeColumns = (node.data._columns as Column[] | undefined) ?? []
  const effectiveColumns = upstreamColumns.length > 0 ? upstreamColumns : nodeColumns

  switch (nodeType) {
    case NODE_TYPES.API_INPUT:
      return (
        <ApiInputEditor
          config={config}
          onUpdate={onUpdateConfig}
          accentColor={accentColor}
          configPath={typeof node.data._configReference === "string" ? node.data._configReference : undefined}
          reservedFrameLabels={reservedApiInputFrameLabels}
        />
      )

    case NODE_TYPES.LIVE_SWITCH:
      return <LiveSwitchEditor config={config} onUpdate={onUpdateConfig} inputSources={inputSources} accentColor={accentColor} />

    case NODE_TYPES.DATA_INPUT:
      return <DataInputEditor config={config} onUpdate={onUpdateConfig} onReplaceConfig={onReplaceConfig} accentColor={accentColor} errorLine={errorLine} />

    case NODE_TYPES.DATA_OUTPUT:
      return <DataOutputEditor config={config} onUpdate={onUpdateConfig} onReplaceConfig={onReplaceConfig} nodeId={node.id} accentColor={accentColor} />

    case NODE_TYPES.EXPLORE:
      if (activeExplorePane === "code") {
        return (
          <div id="explore-code-pane" role="tabpanel" aria-labelledby="explore-code-tab" data-testid="explore-code-pane" className="h-full min-h-0 flex flex-col">
            <ExploreCodeEditor config={config} onUpdate={onUpdateConfig} inputSources={inputSources} onDeleteInput={onDeleteEdge} errorLine={errorLine} upstreamColumns={upstreamColumns} />
          </div>
        )
      }
      return (
        <div id={`explore-${activeExplorePaneMeta.key}-pane`} role="tabpanel" aria-labelledby={`explore-${activeExplorePaneMeta.key}-tab`} data-testid={`explore-${activeExplorePaneMeta.key}-pane`} className="h-full">
          {activeExplorePane === "overview" && <ExploreOverviewConfig config={config} onUpdate={onUpdateConfig} />}
          {activeExplorePane === "pivots" && <ExplorePivotsConfig config={config} onUpdate={onUpdateConfig} nodeId={node.id} upstreamColumns={pivotColumns} loadFilterMembers={loadPivotFilterMembers} currentConfigHash={exploreConfigHash} />}
          {activeExplorePane === "charts" && <ExploreChartsConfig config={config} onUpdate={onUpdateConfig} nodeId={node.id} currentConfigHash={exploreConfigHash} onShowPivots={onShowPivots} />}
        </div>
      )

    case NODE_TYPES.EXTERNAL_FILE:
      return <ExternalFileEditor config={config} onUpdate={onUpdateConfig} inputSources={inputSources} onDeleteInput={onDeleteEdge} errorLine={errorLine} accentColor={accentColor} />

    case NODE_TYPES.OUTPUT:
      return <OutputEditor config={config} onUpdate={onUpdateConfig} nodeId={node.id} />

    case NODE_TYPES.BANDING:
      return <BandingEditor config={config} onUpdate={onUpdateConfig} inputSources={inputSources} onDeleteInput={onDeleteEdge} upstreamColumns={upstreamColumns} accentColor={accentColor} previewRows={previewRows} />

    case NODE_TYPES.SCENARIO_EXPANDER:
      return <ScenarioExpanderEditor config={config} onUpdate={onUpdateConfig} inputSources={inputSources} onDeleteInput={onDeleteEdge} upstreamColumns={upstreamColumns} />

    case NODE_TYPES.RATING_STEP:
      return <RatingStepEditor config={config} onUpdate={onUpdateConfig} inputSources={inputSources} onDeleteInput={onDeleteEdge} upstreamColumns={upstreamColumns} previewRows={previewRows} accentColor={accentColor} errorLine={errorLine} nodeId={node.id} />

    case NODE_TYPES.MODEL_SCORE:
      return <ModelScoreEditor config={config} onUpdate={onUpdateConfig} inputSources={inputSources} onDeleteInput={onDeleteEdge} errorLine={errorLine} accentColor={accentColor} />

    case NODE_TYPES.MODELLING:
      return <ModellingConfig config={configWithNodeId} onUpdate={onUpdateConfig} upstreamColumns={effectiveColumns} activePane={activeModellingPane} />

    case NODE_TYPES.OPTIMISER:
      return <OptimiserConfig config={configWithNodeId} onUpdate={onUpdateConfig} upstreamColumns={effectiveColumns} accentColor={accentColor} deferColumnFetch={selectedPreviewLoading} />

    case NODE_TYPES.OPTIMISER_APPLY:
      return <OptimiserApplyEditor config={config} onUpdate={onUpdateConfig} inputSources={inputSources} onDeleteInput={onDeleteEdge} accentColor={accentColor} />

    case NODE_TYPES.CONSTANT:
      return <ConstantEditor config={config} onUpdate={onUpdateConfig} />

    case NODE_TYPES.POLARS:
      return <TransformEditor config={config} onUpdate={onUpdateConfig} inputSources={inputSources} onDeleteInput={onDeleteEdge} errorLine={errorLine} upstreamColumns={upstreamColumns} />

    case NODE_TYPES.EDGE_JOIN:
      return <EdgeJoinEditor config={config} onUpdate={onUpdateConfig} nodeId={node.id} accentColor={accentColor} onDeleteInput={onDeleteEdge} onSwapInputs={onSwapEdgeJoinInputs ? () => onSwapEdgeJoinInputs(node.id) : undefined} />

    case NODE_TYPES.SUBMODEL:
      return <SubmodelEditor config={config} accentColor={accentColor} />

    case NODE_TYPES.SUBMODEL_PORT:
      return (
        <SubmodelPortEditor
          node={node}
          onDeleteInputPort={onDeleteSubmodelInputPort}
        />
      )

    default:
      return null
  }
}
