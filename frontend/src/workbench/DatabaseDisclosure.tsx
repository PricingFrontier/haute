import { ChevronRight } from "lucide-react"
import type { ReactNode } from "react"
import type { WorkbenchStoreExtra } from "../api/types"

export function DatabaseExtras({ extras }: { extras: WorkbenchStoreExtra[] }) {
  return extras.map((each) => <div key={`${each.table}.${each.column}`}>
    <code>{each.column ?? each.table}</code><p>{each.retention}</p>
    <p>{each.quotes === null ? "Quote count unavailable" : `${each.quotes} quotes with values`}</p>
  </div>)
}

/** Native disclosure behavior with the schema tables' chevron and focus treatment. */
export default function DatabaseDisclosure({ title, children, className = "" }: { title: ReactNode; children: ReactNode; className?: string }) {
  return <details className={`[&[open]>summary>svg]:rotate-90 ${className}`}>
    <summary className="focus-ring hover-bg flex cursor-pointer list-none items-center gap-1.5 rounded-md px-1 py-1.5 font-medium [&::-webkit-details-marker]:hidden">
      <ChevronRight size={12} aria-hidden="true" className="shrink-0 transition-transform" style={{ color: "var(--text-muted)" }} />
      <span className="min-w-0 break-words">{title}</span>
    </summary>
    <div className="mt-1 pl-[22px]">{children}</div>
  </details>
}
