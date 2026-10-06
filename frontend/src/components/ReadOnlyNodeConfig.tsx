/**
 * Read-only render of a node's REAL config editor for the comparison view (S11).
 *
 * Renders `NodeConfigEditor` — the same dispatcher the live NodePanel uses — in
 * its read-only mode, with no-op handlers and empty live context (no upstream
 * columns / input sources / preview / schema fetches), so the user sees the
 * configuration the way they author it, not raw metadata. The consumer wraps
 * this in an `inert` container to block all interaction; the no-op `onUpdate` is
 * a second guard so nothing can mutate. A node type without a read-only editor
 * falls back to a plain config dump rather than rendering nothing.
 */
import { useMemo } from "react"

import { LazyEditorBoundary } from "../panels/LazyNodeEditors"
import { GraphProvider } from "../panels/GraphContext"
import { NodeConfigEditor } from "../panels/NodeConfigEditor"
import type { OnUpdateConfigResult } from "../panels/editors/_shared"
import useDocumentStatusStore from "../stores/useDocumentStatusStore"
import { nodeTypeColors, type NodeTypeValue } from "../utils/nodeTypes"

const noop = (): OnUpdateConfigResult => ({ ok: true })
const doNothing = () => {}
// The comparison view renders no Explore editor, so nothing asks for members.
const noFilterMembers = () => Promise.reject(new Error("The comparison view does not load filter members."))
const EMPTY: never[] = []

interface ReadOnlyNodeConfigProps {
  nodeType: string
  config: Record<string, unknown>
  nodeId: string
}

export default function ReadOnlyNodeConfig({ nodeType, config, nodeId }: ReadOnlyNodeConfigProps) {
  const accentColor = nodeTypeColors[nodeType] ?? "var(--accent)"
  // Select the stored array (or undefined) so the snapshot stays reference-
  // stable while capabilities are absent; a `?? []` inside the selector minted
  // a fresh array per snapshot and looped useSyncExternalStore forever.
  const reservedLabelList = useDocumentStatusStore(
    (state) => state.capabilities?.reserved_api_input_frame_labels,
  )
  const reservedFrameLabels = useMemo(
    () => new Set(reservedLabelList ?? []),
    [reservedLabelList],
  )

  // Several editors (Output, Sink, RatingStep, Modelling, Optimiser) read graph
  // context via useGraph(), which throws without a provider. Supply an EMPTY
  // graph: the node's own configured values still render; upstream-derived UI
  // (column pickers, etc.) is simply empty — fine for a read-only single node.
  return (
    <GraphProvider allNodes={EMPTY} edges={EMPTY} submodels={{}} preamble="">
      <LazyEditorBoundary>
        <NodeConfigEditor
          readOnly
          nodeType={nodeType as NodeTypeValue}
          config={config}
          configWithNodeId={{ ...config, _nodeId: nodeId }}
          node={{ id: nodeId, data: { label: "", description: "", nodeType, config } }}
          onUpdateConfig={noop}
          onReplaceConfig={noop}
          onDeleteEdge={noop}
          inputSources={EMPTY}
          upstreamColumns={EMPTY}
          pivotColumns={EMPTY}
          activeExplorePane="overview"
          activeModellingPane="target"
          activeOptimiserPane="data"
          onShowPivots={doNothing}
          loadPivotFilterMembers={noFilterMembers}
          exploreConfigHash={null}
          reservedApiInputFrameLabels={reservedFrameLabels}
          accentColor={accentColor}
        />
      </LazyEditorBoundary>
    </GraphProvider>
  )
}
