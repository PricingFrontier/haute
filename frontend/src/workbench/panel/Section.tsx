/** A titled part of a side panel's settings (specs/workbench). */
import type { ReactNode } from "react"
import { EditorLabel } from "../../components/form"

/** A setting chosen from a few: its label, then a select of the choices. */
export function Section({ title, aside, children }: { title: string; aside?: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-1.5" aria-label={title}>
      <div className="flex items-baseline justify-between">
        <EditorLabel as="div">{title}</EditorLabel>
        {aside && (
          <span className="text-[11px]" style={{ color: "var(--text-muted)" }}>
            {aside}
          </span>
        )}
      </div>
      {children}
    </section>
  )
}
