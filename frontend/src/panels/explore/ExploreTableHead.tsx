import type { CSSProperties, ReactNode } from "react"
import { LABEL_CLASS, SECONDARY_STYLE } from "./exploreTableStyles"

const STICKY_STYLE: CSSProperties = {
  ...SECONDARY_STYLE,
  background: "var(--bg-elevated)",
  position: "sticky",
  top: 0,
  zIndex: 1,
}

/**
 * An Explore report table's header row. An empty label is an unlabelled,
 * `aria-hidden` spacer column (such as a row's expand toggle); `dense` tightens
 * the padding for a table nested in a row; `sticky` pins the header while the
 * table scrolls under it.
 */
export default function ExploreTableHead({
  labels,
  dense = false,
  sticky = false,
}: {
  labels: ReactNode[]
  dense?: boolean
  sticky?: boolean
}) {
  return (
    <thead>
      <tr>
        {labels.map((label, index) => {
          const spacer = label === ""
          return (
            <th
              key={index}
              scope={spacer ? undefined : "col"}
              aria-hidden={spacer ? true : undefined}
              className={`${LABEL_CLASS} text-left px-2 ${dense ? "py-1" : "py-1.5"}`}
              style={sticky ? STICKY_STYLE : SECONDARY_STYLE}
            >
              {label}
            </th>
          )
        })}
      </tr>
    </thead>
  )
}
