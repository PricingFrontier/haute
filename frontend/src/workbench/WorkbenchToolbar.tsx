import ProjectControls from "../components/ProjectControls"
import { ToolbarBrand, UndoRedo } from "../haute-ui"
import useWorkbenchFormStore from "../stores/useWorkbenchFormStore"

declare const __APP_VERSION__: string

/**
 * The toolbar while the workbench's view shows (specs/workbench): the brand, the form's
 * Undo and Redo, and the project's controls, whose Save saves the form. There is no Commit
 * until the form is on the save ledger; the Git panel's own Commit records the pipeline.
 */
export default function WorkbenchToolbar() {
  const ready = useWorkbenchFormStore((s) => s.status === "ready")
  const canUndo = useWorkbenchFormStore((s) => s.undoStack.length > 0)
  const canRedo = useWorkbenchFormStore((s) => s.redoStack.length > 0)
  const saving = useWorkbenchFormStore((s) => s.saving)
  const undo = useWorkbenchFormStore((s) => s.undo)
  const redo = useWorkbenchFormStore((s) => s.redo)
  const save = useWorkbenchFormStore((s) => s.save)

  return (
    <header role="toolbar" aria-label="Workbench toolbar" className="toolbar flex-wrap gap-y-2 [&>div]:shrink-0">
      <ToolbarBrand name="haute" version={__APP_VERSION__} />
      <div className="ml-2.5 flex flex-wrap items-center gap-2.5" data-testid="workbench-toolbar-actions">
        <UndoRedo canUndo={ready && canUndo} canRedo={ready && canRedo} onUndo={undo} onRedo={redo} />
      </div>
      <ProjectControls onSave={() => { void save() }} saveDisabled={!ready || saving} />
    </header>
  )
}
