import type { ElementType, HTMLAttributes, ReactNode } from "react"
import { PanelLeftClose, PanelLeftOpen } from "lucide-react"

/** The left palette's column, on the chrome. Its items scroll within it. */
export function PaletteColumn(props: Omit<HTMLAttributes<HTMLDivElement>, "className">) {
  return <div {...props} className="palette" />
}

/** The palette's title, and the minimiser that collapses it when `onCollapse` is given. */
export function PaletteHeader({ title, onCollapse }: { title: string; onCollapse?: () => void }) {
  return (
    <div className="palette-header">
      <h2 className="palette-title">{title}</h2>
      {onCollapse && (
        <button type="button" onClick={onCollapse} className="palette-collapse" title="Collapse palette">
          <PanelLeftClose size={14} />
        </button>
      )}
    </div>
  )
}

/** The palette's items, below its header. */
export function PaletteItems({ children }: { children: ReactNode }) {
  return <div className="palette-items">{children}</div>
}

export interface PaletteItemProps extends Omit<HTMLAttributes<HTMLDivElement>, "className" | "children"> {
  /** An icon component taking `size` and `style`, drawn on a tint of `color`. */
  icon: ElementType
  label: string
  /** The item's colour, such as its node group's, as a six-digit hex. */
  color: string
  /** Faded, with a not-allowed cursor; the host stops it being dragged or used. */
  disabled?: boolean
}

/**
 * An item in the palette: its icon on a tint of its colour, then its name. The host
 * says how it is used, by making it draggable or giving it a click handler.
 */
export function PaletteItem({ icon: Icon, label, color, disabled = false, ...props }: PaletteItemProps) {
  return (
    <div {...props} aria-disabled={disabled || undefined} className={disabled ? "palette-item palette-item-disabled" : "palette-item"}>
      <div className="palette-item-icon" style={{ background: `${color}18` }}>
        <Icon size={13} style={{ color }} />
      </div>
      <span className="palette-item-label">{label}</span>
    </div>
  )
}

/** What a collapsed palette leaves: a strip whose button, named by `label`, shows the palette again. */
export function PaletteRevealStrip({ onReveal, label = "Show node palette" }: { onReveal: () => void; label?: string }) {
  return (
    <button type="button" onClick={onReveal} aria-label={label} title={label} className="palette-reveal">
      <PanelLeftOpen size={16} />
    </button>
  )
}
