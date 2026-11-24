import { ArrowLeft, ListChecks, Play, Settings2, SlidersHorizontal, Split } from "lucide-react"
import PipelineSettingsButton from "../components/PipelineSettingsButton"
import ProjectControls from "../components/ProjectControls"
import type { WsStatus } from "../hooks/useWebSocketSync"
import { ToolbarBrand, ToolbarButton, ToolbarColumn } from "../haute-ui"
import useModellingViewStore, { type SettingsPanel } from "../stores/useModellingViewStore"
import useUIStore from "../stores/useUIStore"

interface ModellingToolbarProps {
  /** Save: the host's project save, the one the pipeline toolbar's Save runs. */
  onSave: () => void
  /** Commit: the host's milestone flow, which saves the project first. */
  onCommit: () => void
  /** The server connection, which the Pipeline button shows as Offline while it is away. */
  wsStatus: WsStatus
  /**
   * The pipeline cannot be edited: Save and Commit are off, as are the
   * Assistant unless a turn is running; the settings panels still show.
   */
  editingDisabled?: boolean
}

/**
 * The toolbar while the Modelling view shows (specs/frontend-modelling-optimiser-ui): the
 * brand; **Menu** over the pipeline toolbar's **Pipeline**; **Model Setup**, the settings every model of the open node shares, over **Data
 * Split**; the chosen model's **Features** over **Parameters**, and its **Training**, each
 * picking what the right-hand column shows; and the project's controls, the host's and the same as the pipeline toolbar's. Undo and redo
 * are the keyboard's here; the source and the pipeline's canvas actions have no place.
 */
export default function ModellingToolbar({ onSave, onCommit, wsStatus, editingDisabled = false }: ModellingToolbarProps) {
  const closeModel = useModellingViewStore((s) => s.closeModel)
  const nodeOpen = useModellingViewStore((s) => s.modelId !== null)
  const panel = useModellingViewStore((s) => s.panel)
  const showPanel = useModellingViewStore((s) => s.showPanel)
  const closePanel = useModellingViewStore((s) => s.closePanel)
  // The Git, Files and Assistant panels take the settings' place while they show.
  const otherPanelOpen = useUIStore((s) => s.gitOpen || s.filesPlace !== null || s.assistantOpen)
  const shows = (next: SettingsPanel) => nodeOpen && !otherPanelOpen && panel === next
  const openPanel = (next: SettingsPanel) => {
    // As the pipeline's panel buttons do, asking for the panel that shows closes it.
    if (shows(next)) {
      closePanel()
      return
    }
    // One panel at a time in the right-hand column: a toolbar panel gives way.
    const ui = useUIStore.getState()
    if (ui.gitOpen) ui.setGitOpen(false)
    if (ui.filesPlace !== null) ui.setFilesPlace(null)
    if (ui.assistantOpen) ui.setAssistantOpen(false)
    showPanel(next)
  }
  const panelButton = (next: SettingsPanel) => ({
    "aria-pressed": shows(next),
    disabled: !nodeOpen,
    onClick: () => openPanel(next),
  })
  return (
    <header role="toolbar" aria-label="Modelling toolbar" className="toolbar flex-wrap gap-y-2 [&>div]:shrink-0">
      <ToolbarBrand name="haute" version={__APP_VERSION__} />
      {/* Menu over Pipeline straight after the brand, where the pipeline toolbar's Source
          over Pipeline stands, so its Pipeline button and the actions after it start in
          the same places in either view. */}
      <ToolbarColumn data-testid="modelling-toolbar-menu">
        <ToolbarButton icon={ArrowLeft} disabled={!nodeOpen} onClick={closeModel} title="Return to the modelling main menu">
          Menu
        </ToolbarButton>
        {/* The pipeline toolbar's Pipeline button: its settings apply here too. */}
        <PipelineSettingsButton wsStatus={wsStatus} />
      </ToolbarColumn>
      <div className="ml-2.5 flex flex-wrap items-center gap-2.5" data-testid="modelling-toolbar-actions">
        {/* Model Setup over Data Split: the settings every model of the node shares. */}
        <ToolbarColumn role="group" aria-label="Setup">
          <ToolbarButton icon={Settings2} {...panelButton("setup")} title="The target, objective and metrics every model shares">
            Model Setup
          </ToolbarButton>
          <ToolbarButton icon={Split} {...panelButton("split")} title="The validation and test rows every model is trained and judged on">
            Data Split
          </ToolbarButton>
        </ToolbarColumn>
        {/* The chosen model's own settings. */}
        <ToolbarColumn role="group" aria-label="Model">
          <ToolbarButton icon={ListChecks} {...panelButton("features")} title="The chosen model's factors">
            Features
          </ToolbarButton>
          <ToolbarButton icon={SlidersHorizontal} {...panelButton("params")} title="The chosen model's parameters">
            Parameters
          </ToolbarButton>
        </ToolbarColumn>
        <ToolbarColumn>
          <ToolbarButton icon={Play} {...panelButton("train")} title="Train the chosen model, stage by stage">
            Training
          </ToolbarButton>
        </ToolbarColumn>
      </div>
      <ProjectControls onSave={onSave} onCommit={onCommit} editingDisabled={editingDisabled} />
    </header>
  )
}
