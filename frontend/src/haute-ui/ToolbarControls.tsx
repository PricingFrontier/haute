import type { AnchorHTMLAttributes, ButtonHTMLAttributes, HTMLAttributes } from "react"
import { Redo2, Undo2, ZoomIn, ZoomOut, type LucideIcon } from "lucide-react"
import { classNames } from "./classNames"

/** The product's name, lowercase, over its version: the toolbar's first column. */
export function ToolbarBrand({ name, version }: { name: string; version: string }) {
  return (
    <div className="toolbar-brand" data-testid="toolbar-brand">
      <div className="toolbar-brand-mark">
        <h1 className="toolbar-brand-name">{name}</h1>
        <span className="toolbar-brand-version">v{version}</span>
      </div>
    </div>
  )
}

/** Two controls stacked: the toolbar's two rows. */
export function ToolbarColumn({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div {...props} className={classNames("toolbar-column", className)} />
}

/** What a toolbar button and a link drawn as one share. */
interface ToolbarAction {
  icon?: LucideIcon
  /** Narrower side padding, as the zoom buttons have. */
  compact?: boolean
}

export interface ToolbarButtonProps extends ButtonHTMLAttributes<HTMLButtonElement>, ToolbarAction {}

/** The toolbar's raised surface in a toolbar button's shape; a caller's layout classes follow. */
const action = (compact: boolean, className: string | undefined) =>
  classNames("toolbar-btn toolbar-action", compact && "toolbar-action-compact", className)

/** A labelled button on the toolbar's raised surface, as wide as its column. */
export function ToolbarButton({ icon: Icon, compact = false, className, children, ...props }: ToolbarButtonProps) {
  return (
    <button type="button" {...props} className={action(compact, className)}>
      {Icon && <Icon size={13} aria-hidden="true" />}
      {children}
    </button>
  )
}

/** A link drawn as a toolbar button, for a control that goes somewhere. */
export function ToolbarLink({
  icon: Icon,
  compact = false,
  className,
  children,
  ...props
}: AnchorHTMLAttributes<HTMLAnchorElement> & ToolbarAction & { href: string }) {
  return (
    <a {...props} className={action(compact, className)}>
      {Icon && <Icon size={13} aria-hidden="true" />}
      {children}
    </a>
  )
}

/**
 * A status in a toolbar button's shape, so it lines up with the buttons beside it: a label,
 * not a control. It is plain text, with no fill or outline and nothing under the pointer, so
 * it is not taken for a button. Something to press inside it shows that itself, as an
 * underlined word does.
 */
export function ToolbarStatus({ className, ...props }: HTMLAttributes<HTMLSpanElement>) {
  return <span {...props} className={classNames("toolbar-status toolbar-action", className)} />
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
