import { useEffect, useId, useRef, useState, type ButtonHTMLAttributes, type HTMLAttributes } from "react"
import { Redo2, Undo2, ZoomIn, ZoomOut, type LucideIcon } from "lucide-react"

const classNames = (...names: (string | false | undefined)[]) => names.filter(Boolean).join(" ")

/** When a build was made and from which commit; the commit is unknown outside a git checkout. */
export interface BuildInfo {
  builtAt: string
  commit: string | null
  commitDate: string | null
}

/** An ISO timestamp as a date and time in the viewer's locale and time zone, or as given if unparseable. */
function formatBuildDate(iso: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  return date.toLocaleString(undefined, { day: "numeric", month: "long", year: "numeric", hour: "2-digit", minute: "2-digit" })
}

/**
 * The product's name, lowercase, over its version: the toolbar's first column.
 *
 * Given `build`, the version is a button that opens the build's details: the version,
 * when it was built, and the commit it was built from with that commit's date. Escape
 * or a press outside closes them.
 */
export function ToolbarBrand({ name, version, build }: { name: string; version: string; build?: BuildInfo }) {
  const [open, setOpen] = useState(false)
  const markRef = useRef<HTMLDivElement>(null)
  const detailsId = useId()

  useEffect(() => {
    if (!open) return
    const onPointerDown = (event: PointerEvent) => {
      if (!markRef.current?.contains(event.target as Node)) setOpen(false)
    }
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false)
    }
    document.addEventListener("pointerdown", onPointerDown)
    document.addEventListener("keydown", onKeyDown)
    return () => {
      document.removeEventListener("pointerdown", onPointerDown)
      document.removeEventListener("keydown", onKeyDown)
    }
  }, [open])

  return (
    <div className="toolbar-brand" data-testid="toolbar-brand">
      <div className="toolbar-brand-mark" ref={markRef}>
        <h1 className="toolbar-brand-name">{name}</h1>
        {build ? (
          <button
            type="button"
            className="toolbar-brand-version"
            aria-expanded={open}
            aria-controls={open ? detailsId : undefined}
            title="Version details"
            onClick={() => setOpen((wasOpen) => !wasOpen)}
          >
            v{version}
          </button>
        ) : (
          <span className="toolbar-brand-version">v{version}</span>
        )}
        {build && open && (
          <dl id={detailsId} className="toolbar-brand-details" data-testid="toolbar-brand-details">
            <dt>Version</dt>
            <dd>{version}</dd>
            <dt>Built</dt>
            <dd>{formatBuildDate(build.builtAt)}</dd>
            <dt>Commit</dt>
            <dd title={build.commit ?? undefined}>{build.commit ? build.commit.slice(0, 10) : "unknown"}</dd>
            <dt>Commit date</dt>
            <dd>{build.commitDate ? formatBuildDate(build.commitDate) : "unknown"}</dd>
          </dl>
        )}
      </div>
    </div>
  )
}

/** Two controls stacked: the toolbar's two rows. */
export function ToolbarColumn({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div {...props} className={classNames("toolbar-column", className)} />
}

export interface ToolbarButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  icon?: LucideIcon
  /** Narrower side padding, as the zoom buttons have. */
  compact?: boolean
}

/** A labelled button on the toolbar's raised surface, as wide as its column. */
export function ToolbarButton({ icon: Icon, compact = false, className, children, ...props }: ToolbarButtonProps) {
  return (
    <button
      type="button"
      {...props}
      className={classNames("toolbar-btn toolbar-action", compact && "toolbar-action-compact", className)}
    >
      {Icon && <Icon size={13} aria-hidden="true" />}
      {children}
    </button>
  )
}

interface UndoRedoProps {
  canUndo: boolean
  canRedo: boolean
  onUndo: () => void
  onRedo: () => void
}

/** Undo over Redo, each disabled while there is nothing to undo or redo. */
export function UndoRedo({ canUndo, canRedo, onUndo, onRedo }: UndoRedoProps) {
  return (
    <ToolbarColumn data-testid="toolbar-undo-redo">
      <ToolbarButton data-testid="toolbar-undo" icon={Undo2} onClick={onUndo} disabled={!canUndo} aria-label="Undo" title="Undo (Ctrl+Z)">
        Undo
      </ToolbarButton>
      <ToolbarButton data-testid="toolbar-redo" icon={Redo2} onClick={onRedo} disabled={!canRedo} aria-label="Redo" title="Redo (Ctrl+Shift+Z)">
        Redo
      </ToolbarButton>
    </ToolbarColumn>
  )
}

/** Zoom In over Zoom Out. */
export function ZoomInOut({ onZoomIn, onZoomOut }: { onZoomIn: () => void; onZoomOut: () => void }) {
  return (
    <ToolbarColumn>
      <ToolbarButton compact data-testid="toolbar-zoom-in" icon={ZoomIn} onClick={onZoomIn} aria-label="Zoom in" title="Zoom in">
        Zoom In
      </ToolbarButton>
      <ToolbarButton compact data-testid="toolbar-zoom-out" icon={ZoomOut} onClick={onZoomOut} aria-label="Zoom out" title="Zoom out">
        Zoom Out
      </ToolbarButton>
    </ToolbarColumn>
  )
}

interface SaveCommitProps {
  onSave: () => void
  /** Without it there is no Commit button: the host has nothing to commit to. */
  onCommit?: () => void
  disabled?: boolean
}

/** Save and Commit: filled, side by side and of equal width. */
export function SaveCommit({ onSave, onCommit, disabled = false }: SaveCommitProps) {
  return (
    <div className="toolbar-save-commit">
      <button
        type="button"
        data-testid="toolbar-save"
        onClick={onSave}
        disabled={disabled}
        className="toolbar-fill toolbar-fill-save"
        title="Save - Ctrl+S"
      >
        Save
      </button>
      {onCommit && (
        <button
          type="button"
          data-testid="toolbar-save-commit"
          onClick={onCommit}
          disabled={disabled}
          className="toolbar-fill toolbar-fill-commit"
          title="Commit - record a milestone on your working branch"
        >
          Commit
        </button>
      )}
    </div>
  )
}
