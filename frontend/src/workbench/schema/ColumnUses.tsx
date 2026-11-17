/**
 * Where a schema column is used: the sheets using it, each with its sections whose own
 * conditions test it and its components using it, each by its name beside its kind's mark
 * (specs/workbench).
 */
import { type LucideIcon } from "lucide-react"
import type { FormSpec, SchemaColumn, SchemaTable } from "../../api/types"
import { columnUses } from "../../utils/workbenchForm"
import { SECTION_KIND, WIDGET_KINDS } from "../widgetKinds"

/** A line of a sheet's uses: the holder's mark and its name, a component's an identifier. */
function UseLine({ icon: Icon, color, name, code = false }: { icon: LucideIcon; color: string; name: string; code?: boolean }) {
  return (
    <li className="flex min-w-0 items-center gap-1.5">
      <Icon size={12} aria-hidden="true" className="shrink-0" style={{ color }} />
      <span className={`truncate ${code ? "font-mono" : ""}`.trim()} title={name}>
        {name}
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
        <p style={{ color: "var(--text-muted)" }}>Not used on any sheet</p>
      ) : (
        sheets.map(({ page, sections, components }) => (
          <div key={page.id} className="flex min-w-0 flex-col gap-0.5">
            <p className="truncate font-medium" title={page.title}>
              {page.title}
            </p>
            {(sections.length > 0 || components.length > 0) && (
              <ul aria-label={`Uses on ${page.title}`} className="flex min-w-0 flex-col gap-0.5 pl-2">
                {sections.map((section) => (
                  <UseLine key={section.id} icon={SECTION_KIND.icon} color={SECTION_KIND.color} name={section.title} />
                ))}
                {components.map((widget) => (
                  <UseLine key={widget.id} icon={WIDGET_KINDS[widget.type].icon} color={WIDGET_KINDS[widget.type].color} name={widget.name} code />
                ))}
              </ul>
            )}
          </div>
        ))
      )}
    </div>
  )
}
