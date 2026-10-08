import type { HTMLAttributes } from "react"
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

/** What a collapsed palette leaves: a strip whose button shows the palette again. */
export function PaletteRevealStrip({ onReveal }: { onReveal: () => void }) {
  return (
    <button type="button" onClick={onReveal} aria-label="Show node palette" title="Show node palette" className="palette-reveal">
      <PanelLeftOpen size={16} />
    </button>
  )
}
