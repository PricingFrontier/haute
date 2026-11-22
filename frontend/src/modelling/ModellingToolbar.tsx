import { ArrowLeft, Settings2, Split } from "lucide-react"
import ProjectControls from "../components/ProjectControls"
import { ToolbarBrand, ToolbarButton, ToolbarColumn } from "../haute-ui"
import useModellingViewStore, { type SettingsPanel } from "../stores/useModellingViewStore"
import useUIStore from "../stores/useUIStore"

interface ModellingToolbarProps {
  /** Save: the host's project save, the one the pipeline toolbar's Save runs. */
  onSave: () => void
  /** Commit: the host's milestone flow, which saves the project first. */
  onCommit: () => void
  /**
   * The pipeline cannot be edited: Save and Commit are off, as are the
   * Assistant unless a turn is running; Model Setup and Training Split still show.
   */
  editingDisabled?: boolean
}

/**
 * The toolbar while the Modelling view shows (specs/frontend-modelling-optimiser-ui): the
 * brand; **Model Setup**, the settings every model of the open node shares, and **Training
 * Split**, each opening in the right-hand column in place of the chosen model's settings;
 * and the
 * project's controls, the host's and the same as the pipeline toolbar's. Undo and redo
 * are the keyboard's here; the source and the pipeline's canvas actions have no place.
 */
export default function ModellingToolbar({ onSave, onCommit, editingDisabled = false }: ModellingToolbarProps) {
  const closeModel = useModellingViewStore((s) => s.closeModel)
  const nodeOpen = useModellingViewStore((s) => s.modelId !== null)
  const panel = useModellingViewStore((s) => s.panel)
  const togglePanel = useModellingViewStore((s) => s.togglePanel)
  const openPanel = (next: SettingsPanel) => {
    // One panel at a time in the right-hand column: a toolbar panel gives way.
    const ui = useUIStore.getState()
    if (ui.gitOpen) ui.setGitOpen(false)
    if (ui.filesPlace !== null) ui.setFilesPlace(null)
    if (ui.assistantOpen) ui.setAssistantOpen(false)
    togglePanel(next)
  }
  return (
    <header role="toolbar" aria-label="Modelling toolbar" className="toolbar flex-wrap gap-y-2 [&>div]:shrink-0">
      <ToolbarBrand name="haute" version={__APP_VERSION__} />
      <div className="ml-2.5 flex flex-wrap items-center gap-2.5" data-testid="modelling-toolbar-actions">
        <ToolbarColumn>
          <ToolbarButton icon={ArrowLeft} disabled={!nodeOpen} onClick={closeModel} title="Return to the modelling main menu">
            Main Menu
          </ToolbarButton>
        </ToolbarColumn>
        {/* Model Setup over Training Split: the settings every model of the node shares. */}
        <ToolbarColumn role="group" aria-label="Setup">
          <ToolbarButton
            icon={Settings2}
            aria-pressed={nodeOpen && panel === "setup"}
            disabled={!nodeOpen}
            onClick={() => openPanel("setup")}
            title="The target, objective and metrics every model shares"
          >
            Model Setup
          </ToolbarButton>
          <ToolbarButton
            icon={Split}
            aria-pressed={nodeOpen && panel === "split"}
            disabled={!nodeOpen}
            onClick={() => openPanel("split")}
            title="The validation and test rows every model is trained and judged on"
          >
            Training Split
          </ToolbarButton>
        </ToolbarColumn>
      </div>
      <ProjectControls onSave={onSave} onCommit={onCommit} editingDisabled={editingDisabled} />
    </header>
  )
}
