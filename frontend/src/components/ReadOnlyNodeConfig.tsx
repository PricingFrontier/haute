/**
 * Read-only render of a node's REAL config editor for the comparison view (S11).
 *
 * Renders the same editor components the live NodePanel uses, but with no-op
 * handlers and empty live context (no upstream columns / input sources / preview
 * / schema fetches) — so the user sees the configuration the way they author it,
 * not raw metadata. The consumer wraps this in an `inert` container to block all
 * interaction; the no-op `onUpdate` is a second guard so nothing can mutate.
 *
 * This deliberately mirrors NodePanel.renderEditor rather than sharing it: the
 * prop-set here is the read-only counterpart (no-ops, empty context), and keeping
 * it separate leaves the central, heavily-tested NodePanel untouched. A new node
 * type must be added in both — the `default` falls back to a plain config dump so
 * an unmapped type degrades gracefully rather than rendering nothing.
 */
import {
  DataSourceEditor,
  TransformEditor,
  ModelScoreEditor,
  BandingEditor,
  RatingStepEditor,
  OutputEditor,
  ExternalFileEditor,
  ApiInputEditor,
  LiveSwitchEditor,
  SinkEditor,
  ScenarioExpanderEditor,
  OptimiserApplyEditor,
  ConstantEditor,
  SubmodelEditor,
  ModellingConfig,
  OptimiserConfig,
  LazyEditorBoundary,
} from "../panels/LazyNodeEditors"
import { GraphProvider } from "../panels/GraphContext"
import { NODE_TYPES, nodeTypeColors } from "../utils/nodeTypes"

const noop = () => {}
const EMPTY: never[] = []

interface ReadOnlyNodeConfigProps {
  nodeType: string
  config: Record<string, unknown>
  nodeId: string
}

export default function ReadOnlyNodeConfig({ nodeType, config, nodeId }: ReadOnlyNodeConfigProps) {
  const accentColor = nodeTypeColors[nodeType] ?? "var(--accent)"
  const configWithNodeId = { ...config, _nodeId: nodeId }

  const editor = (() => {
    switch (nodeType) {
      case NODE_TYPES.API_INPUT:
        return <ApiInputEditor config={config} onUpdate={noop} accentColor={accentColor} />
      case NODE_TYPES.LIVE_SWITCH:
        return (
          <LiveSwitchEditor
            config={config}
            onUpdate={noop}
            inputSources={EMPTY}
            accentColor={accentColor}
          />
        )
      case NODE_TYPES.DATA_SOURCE:
        return <DataSourceEditor config={config} onUpdate={noop} accentColor={accentColor} errorLine={null} />
      case NODE_TYPES.DATA_SINK:
        return <SinkEditor config={config} onUpdate={noop} nodeId={nodeId} accentColor={accentColor} />
      case NODE_TYPES.EXTERNAL_FILE:
        return (
          <ExternalFileEditor
            config={config}
            onUpdate={noop}
            inputSources={EMPTY}
            onDeleteInput={noop}
            errorLine={null}
            accentColor={accentColor}
          />
        )
      case NODE_TYPES.OUTPUT:
        return <OutputEditor config={config} onUpdate={noop} nodeId={nodeId} />
      case NODE_TYPES.BANDING:
        return (
          <BandingEditor
            config={config}
            onUpdate={noop}
            inputSources={EMPTY}
            onDeleteInput={noop}
            upstreamColumns={EMPTY}
            accentColor={accentColor}
          />
        )
      case NODE_TYPES.SCENARIO_EXPANDER:
        return (
          <ScenarioExpanderEditor
            config={config}
            onUpdate={noop}
            inputSources={EMPTY}
            onDeleteInput={noop}
            upstreamColumns={EMPTY}
            errorLine={null}
          />
        )
      case NODE_TYPES.RATING_STEP:
        return (
          <RatingStepEditor
            config={config}
            onUpdate={noop}
            inputSources={EMPTY}
            onDeleteInput={noop}
            upstreamColumns={EMPTY}
            accentColor={accentColor}
            errorLine={null}
            nodeId={nodeId}
          />
        )
      case NODE_TYPES.MODEL_SCORE:
        return (
          <ModelScoreEditor
            config={config}
            onUpdate={noop}
            inputSources={EMPTY}
            onDeleteInput={noop}
            errorLine={null}
            accentColor={accentColor}
          />
        )
      case NODE_TYPES.MODELLING:
        return <ModellingConfig config={configWithNodeId} onUpdate={noop} upstreamColumns={EMPTY} />
      case NODE_TYPES.OPTIMISER:
        return (
          <OptimiserConfig
            config={configWithNodeId}
            onUpdate={noop}
            upstreamColumns={EMPTY}
            accentColor={accentColor}
          />
        )
      case NODE_TYPES.OPTIMISER_APPLY:
        return (
          <OptimiserApplyEditor
            config={config}
            onUpdate={noop}
            inputSources={EMPTY}
            onDeleteInput={noop}
            accentColor={accentColor}
          />
        )
      case NODE_TYPES.CONSTANT:
        return <ConstantEditor config={config} onUpdate={noop} />
      case NODE_TYPES.POLARS:
        return (
          <TransformEditor
            config={config}
            onUpdate={noop}
            inputSources={EMPTY}
            onDeleteInput={noop}
            errorLine={null}
            upstreamColumns={EMPTY}
            hasApiInputUpstream={false}
          />
        )
      case NODE_TYPES.SUBMODEL:
        return <SubmodelEditor config={config} accentColor={accentColor} />
      default:
        return (
          <pre
            data-testid="readonly-config-fallback"
            className="text-[11px] font-mono whitespace-pre-wrap break-all px-3 py-2"
            style={{ color: "var(--text-secondary)" }}
          >
            {JSON.stringify(config, null, 2)}
          </pre>
        )
    }
  })()

  // Several editors (Output, Sink, RatingStep, Modelling, Optimiser) read graph
  // context via useGraph(), which throws without a provider. Supply an EMPTY
  // graph: the node's own configured values still render; upstream-derived UI
  // (column pickers, etc.) is simply empty — fine for a read-only single node.
  return (
    <GraphProvider allNodes={EMPTY} edges={EMPTY} submodels={{}} preamble="">
      <LazyEditorBoundary>{editor}</LazyEditorBoundary>
    </GraphProvider>
  )
}
