import type { ReactNode } from "react"

type Props = {
  title: ReactNode
  children: ReactNode
  ariaLabel?: string
}

/** Open heading-and-fields layout shared by configuration panes. */
export default function ConfigSection({ title, children, ariaLabel }: Props) {
  return (
    <section aria-label={ariaLabel}>
      <h3
        className="text-[14px] font-semibold"
        style={{ color: "var(--text-primary)" }}
      >
        {title}
      </h3>
      <div className="mt-1.5">{children}</div>
    </section>
  )
}
