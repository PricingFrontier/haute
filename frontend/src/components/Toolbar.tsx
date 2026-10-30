import { useMemo, useCallback } from "react"
import { Scan, Network, Timer, HardDrive, FileCode2, Variable, Loader2, Group, Ungroup, Link2 } from "lucide-react"
import type { WsStatus } from "../hooks/useWebSocketSync"
import type { NodeTiming, NodeMemory } from "../api/types"
import { ToolbarBrand, ToolbarButton, ToolbarColumn, UndoRedo, ZoomInOut } from "../haute-ui"
import BreakdownDropdown, { type BreakdownItem } from "./BreakdownDropdown"
import ProjectControls from "./ProjectControls"
import SourcePipelineControls from "./SourcePipelineControls"
import useUIStore from "../stores/useUIStore"
import { formatBytes } from "../utils/formatBytes"
import MlflowSettingsModal from "./MlflowSettingsModal"

function formatTiming(ms: number): string {
  const rounded = Math.round(ms)
  return rounded < 1000 ? `${rounded} ms` : `${(ms / 1000).toFixed(2)} s`
}

interface ToolbarProps {
  nodeCount: number
  canUndo: boolean
  canRedo: boolean
  onUndo: () => void
  onRedo: () => void
  onZoomIn: () => void
  onZoomOut: () => void
  onOpenUtility: () => void
  onOpenConstants: () => void
  /** What the Submodel button does for the current selection: "create"
   *  groups 2+ nodes into a new submodel, and "dissolve" (exactly one
   *  submodel occurrence selected) expands it back into the pipeline. The
   *  caller owns the mode, the availability and the click's policy. */
  submodelAction: "create" | "dissolve"
  canRunSubmodelAction: boolean
  onSubmodelAction: () => void
  /** Reuse the selected supported node by wrapping it, or add another submodel occurrence. */
  canCreateInstance: boolean
  onCreateInstance: () => void
  onCentre: () => void
  onAutoLayout: () => void
  isAutoLayouting: boolean
  onSave: () => void
  onSaveCommit: () => void
  wsStatus: WsStatus
  timings?: NodeTiming[]
  memory?: NodeMemory[]
  editingDisabled?: boolean
  sourceSelectionTrusted?: boolean
}

export default function Toolbar({
  nodeCount,
  canUndo, canRedo, onUndo, onRedo,
  onZoomIn, onZoomOut,
  onOpenUtility, onOpenConstants,
  submodelAction, canRunSubmodelAction, onSubmodelAction,
  canCreateInstance, onCreateInstance,
  onCentre, onAutoLayout,
  isAutoLayouting,
  onSave,
  onSaveCommit,
  wsStatus, timings, memory,
  editingDisabled = false,
  sourceSelectionTrusted = true,
}: ToolbarProps) {
  const dissolvesSubmodel = submodelAction === "dissolve"

  const mlflowSettingsOpen = useUIStore((s) => s.mlflowSettingsOpen)
  const setMlflowSettingsOpen = useUIStore((s) => s.setMlflowSettingsOpen)
  const closeMlflowSettings = useCallback(() => setMlflowSettingsOpen(false), [setMlflowSettingsOpen])

  const timingItems: BreakdownItem[] = useMemo(
    () => (timings ?? []).map((t) => ({ node_id: t.node_id, label: t.label, value: t.timing_ms })),
    [timings],
  )

  const memoryItems: BreakdownItem[] = useMemo(
    () => (memory ?? []).map((m) => ({ node_id: m.node_id, label: m.label, value: m.memory_bytes })),
    [memory],
  )

  // The brand column is as wide as the node palette, less the bar's padding, so the
  // Source selector starts over the palette's edge (haute-ui/toolbar.css).
  const brand = <ToolbarBrand name="haute" version={__APP_VERSION__} />

  return (
    <header role="toolbar" aria-label="Pipeline toolbar" className="toolbar flex-wrap gap-y-2 [&>div]:shrink-0">
      {brand}
      <SourcePipelineControls wsStatus={wsStatus} editingDisabled={editingDisabled} sourceSelectionTrusted={sourceSelectionTrusted} />
      {/* Canvas actions (Undo/Redo through Utility/Constants) sit on the left,
          between the Source/Pipeline column and the Timing/Memory readouts.
          Undo/Redo and the zoom lead, where the workbench's toolbar has them. */}
      <div className="ml-2.5 flex flex-wrap items-center gap-2.5" data-testid="toolbar-canvas-actions">
        {/* Undo / Redo column — Undo at the top, Redo underneath, leading the canvas action group */}
        <UndoRedo
          canUndo={!editingDisabled && canUndo}
          canRedo={!editingDisabled && canRedo}
          onUndo={onUndo}
          onRedo={onRedo}
        />
        {/* Zoom controls column — Zoom In on top of Zoom Out */}
        <ZoomInOut onZoomIn={onZoomIn} onZoomOut={onZoomOut} />
        {/* Centre and Layout column — Centre on top of Layout */}
        <ToolbarColumn>
          <ToolbarButton compact icon={Scan} data-testid="toolbar-centre" onClick={onCentre} disabled={nodeCount === 0} title="Fit all nodes in view">
            Centre
          </ToolbarButton>
          <ToolbarButton
            compact
            data-testid="toolbar-layout"
            onClick={onAutoLayout}
            disabled={editingDisabled || nodeCount === 0 || isAutoLayouting}
            aria-busy={isAutoLayouting}
            /* The visible label stays "Layout" in both states — swapping it to
               "Laying out" is what previously forced a fixed 104px width.  The
               running state is carried by the spinner, ``aria-busy``, the title
               and an ``aria-label`` that keeps the accessible name honest
               without costing any width. */
            aria-label={isAutoLayouting ? "Laying out" : "Layout"}
            className="relative whitespace-nowrap"
            title={isAutoLayouting ? "Auto-arranging nodes" : "Auto-arrange nodes"}
          >
            {/* Keep the icon and label in flow while the spinner overlays them
                so the toolbar never reflows when auto-layout starts. */}
            <span className={`inline-flex items-center gap-1${isAutoLayouting ? " invisible" : ""}`}>
              <Network size={13} aria-hidden="true" />
              Layout
            </span>
            {isAutoLayouting && (
              <Loader2
                size={13}
                aria-hidden="true"
                className="animate-spin absolute inset-0 m-auto"
              />
            )}
          </ToolbarButton>
        </ToolbarColumn>
        {/* Selection actions.  These use ``aria-disabled`` rather than the
            ``disabled`` attribute: a disabled button is removed from the tab
            order AND swallows pointer events, so its title never appears -
            precisely when the user most needs to know why it's unavailable.
            The click is NOT guarded here: the handler owns the policy and
            answers an unavailable click with the same toast Ctrl+G gives, so
            the reason reaches the keyboard and touch users a `title` cannot.
            ``can*`` therefore drives presentation only. */}
        {/* Submodel and Instance column — Submodel on top of Instance.  Submodel
            keeps its own grid of two stacked labels, which the kit's flex
            action rule would override. */}
        <ToolbarColumn>
          <button
            data-testid="toolbar-submodel"
            onClick={onSubmodelAction}
            aria-disabled={!canRunSubmodelAction}
            aria-label={dissolvesSubmodel ? "Dissolve" : "Submodel"}
            className="toolbar-btn px-2.5 py-1 text-[12px] font-medium rounded-md grid w-full"
            title={dissolvesSubmodel
              ? "Dissolve the selected submodel back into its nodes"
              : "Group the selected nodes into a submodel - select 2 or more (Ctrl+G)"}
          >
            {/* Both labels share one grid cell and the inactive one is only
                hidden, so the button keeps the wider label's width and
                selecting a submodel never reflows the toolbar. */}
            <span className={`col-start-1 row-start-1 flex items-center justify-center gap-1${dissolvesSubmodel ? " invisible" : ""}`}>
              <Group size={13} aria-hidden="true" />
              Submodel
            </span>
            <span className={`col-start-1 row-start-1 flex items-center justify-center gap-1${dissolvesSubmodel ? "" : " invisible"}`}>
              <Ungroup size={13} aria-hidden="true" />
              Dissolve
            </span>
          </button>
          <ToolbarButton
            icon={Link2}
            data-testid="toolbar-instance"
            onClick={onCreateInstance}
            aria-disabled={!canCreateInstance}
            title="Create a linked instance of the selected node - select exactly one"
          >
            Instance
          </ToolbarButton>
        </ToolbarColumn>
        {/* Utility and Constants column — Utility on top of Constants */}
        <ToolbarColumn>
          <ToolbarButton icon={FileCode2} data-testid="toolbar-utility" onClick={onOpenUtility} disabled={editingDisabled} title="Utility scripts - reusable functions">
            Utility
          </ToolbarButton>
          <ToolbarButton
            icon={Variable}
            data-testid="toolbar-constants"
            onClick={onOpenConstants}
            disabled={editingDisabled}
            title="Global constants - values every node can read, per source"
          >
            Constants
          </ToolbarButton>
        </ToolbarColumn>
      </div>
      {/* Timing + memory breakdowns column — Timing at the top, Memory at the bottom */}
      <div className="ml-2.5 flex flex-col gap-1 w-fit" data-testid="toolbar-breakdowns">
        <BreakdownDropdown
          icon={Timer}
          title="Pipeline Timing"
          items={timingItems}
          formatValue={formatTiming}
        />
        <BreakdownDropdown
          icon={HardDrive}
          title="Pipeline Memory"
          items={memoryItems}
          formatValue={formatBytes}
          valueWidth="w-14"
        />
      </div>
      {/* The project's controls (Assistant, Help, the branch, Save and Commit), the
          same ones the workbench's toolbar ends with. */}
      <ProjectControls onSave={onSave} onCommit={onSaveCommit} editingDisabled={editingDisabled} />
      {mlflowSettingsOpen && <MlflowSettingsModal onClose={closeMlflowSettings} />}
    </header>
  )
}
