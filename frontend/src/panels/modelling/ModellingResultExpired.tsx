/**
 * The results panel of a Model Training node whose remembered result is gone
 * from the server: training results are held in the server's memory only, so
 * the panel says why they are missing and what to do, instead of nothing.
 */
import useUIStore from "../../stores/useUIStore"
import { NODE_TYPES } from "../../utils/nodeTypes"
import PreviewPanelFrame from "../PreviewPanelFrame"

export function ModellingResultExpired({ nodeLabel }: { nodeLabel: string }) {
  const initialHeight = useUIStore((s) => s.modellingPreviewHeight)
  const rememberHeight = useUIStore((s) => s.setModellingPreviewHeight)
  return (
    <PreviewPanelFrame
      nodeLabel={nodeLabel}
      nodeType={NODE_TYPES.MODELLING}
      initialHeight={initialHeight}
      onHeightChange={rememberHeight}
      data-testid="modelling-result-expired"
    >
      <div className="flex flex-1 items-center justify-center px-6">
        <p
          role="status"
          className="max-w-xl text-center text-xs leading-5"
          style={{ color: "var(--text-muted)" }}
        >
          The last training result for this node is no longer available (the server restarted
          or it expired). Training results are not kept across a server restart: train this
          model again to see them, or open its MLflow run if it was logged.
        </p>
      </div>
    </PreviewPanelFrame>
  )
}
