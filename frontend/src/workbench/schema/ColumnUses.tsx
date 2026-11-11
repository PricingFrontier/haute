/**
 * Where a schema column is used: the sheets using it, each with its sections whose own
 * conditions test it and its components using it, by name, kind and way (specs/workbench).
 */
import { type LucideIcon } from "lucide-react"
import type { FormSpec, SchemaColumn, SchemaTable } from "../../api/types"
import { type ColumnWay, columnUses } from "../../utils/workbenchForm"
import { SECTION_KIND, WIDGET_KINDS } from "../widgetKinds"

/** Each way a component uses a column, as its line names it. */
const WAY_LABELS: Record<ColumnWay, string> = {
  shows: "Shows it",
  showWhen: "Show when",
  imports: "Imports it",
  parameter: "Gives a parameter",
  exports: "Exports it",
}

const MUTED = { color: "var(--text-muted)" }

/** A line of a sheet's uses: the holder's mark, its name (a component's an identifier), its kind and its ways. */
function UseLine({ icon: Icon, color, name, code = false, kind, ways }: { icon: LucideIcon; color: string; name: string; code?: boolean; kind: string; ways: string }) {
  return (
    <li className="flex min-w-0 items-center gap-1.5">
      <Icon size={12} aria-hidden="true" className="shrink-0" style={{ color }} />
      <span className={`truncate ${code ? "font-mono" : ""}`.trim()} title={name}>
        {name}
      </span>
      <span className="shrink-0" style={MUTED}>
        {kind} · {ways}
      </span>
    </li>
  )
}

/** The sheets using `column`, under its row, while its eye is open. */
export function ColumnUses({ form, table, column }: { form: FormSpec; table: SchemaTable; column: SchemaColumn }) {
  const sheets = columnUses(form, table.name, column.name)
  return (
    <div
      className="mr-12 ml-7 flex max-w-[32rem] min-w-0 flex-col gap-1.5 rounded-md px-2 py-1.5 text-[11px]"
      style={{ border: "1px solid var(--border)", background: "var(--bg-base)" }}
      data-testid={`schema-column-${column.name}-uses`}
    >
      {sheets.length === 0 ? (
        <p style={MUTED}>Not used on any sheet</p>
      ) : (
        sheets.map(({ page, tested, sections, components }) => (
          <div key={page.id} className="flex min-w-0 flex-col gap-0.5">
            <p className="flex gap-1.5">
              <span className="truncate font-medium" title={page.title}>
                {page.title}
              </span>
              {tested && <span style={MUTED}>{WAY_LABELS.showWhen}</span>}
            </p>
            {(sections.length > 0 || components.length > 0) && (
              <ul aria-label={`Uses on ${page.title}`} className="flex min-w-0 flex-col gap-0.5 pl-2">
                {sections.map((section) => (
                  <UseLine key={section.id} icon={SECTION_KIND.icon} color={SECTION_KIND.color} name={section.title} kind={SECTION_KIND.label} ways={WAY_LABELS.showWhen} />
                ))}
                {components.map(({ widget, ways }) => {
                  const kind = WIDGET_KINDS[widget.type]
                  return (
                    <UseLine
                      key={widget.id}
                      icon={kind.icon}
                      color={kind.color}
                      name={widget.name}
                      code
                      kind={kind.label}
                      ways={ways.map((way) => WAY_LABELS[way]).join(", ")}
                    />
                  )
                })}
              </ul>
            )}
          </div>
        ))
      )}
    </div>
  )
}
