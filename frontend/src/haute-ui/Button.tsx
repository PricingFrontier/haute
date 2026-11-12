import type { ComponentProps } from "react"
import { classNames } from "./classNames"

/**
 * How a button looks. On the raised surface it is as wide as its label, or, `iconOnly`,
 * an icon in square padding. A button that is, or may be, an icon alone needs a name, so
 * `iconOnly` other than a plain `false` requires an `aria-label`. `fill` is a page's main
 * action, filled in the accent, and never an icon alone.
 */
type Look =
  | { variant?: "surface"; iconOnly?: false }
  | { variant?: "surface"; iconOnly: boolean; "aria-label": string }
  | { variant: "fill"; iconOnly?: never }

function look(variant: "surface" | "fill", iconOnly: boolean, className: string | undefined): string {
  if (variant === "fill") return classNames("toolbar-fill toolbar-fill-save btn-fill", className)
  return classNames("toolbar-btn", iconOnly ? "btn-icon" : "btn", className)
}

export type ButtonProps = ComponentProps<"button"> & Look

/**
 * A button outside the toolbar, with the toolbar button's surface and states (button.css).
 * It is a `type="button"` unless told otherwise; a `className` lays it out and never
 * colours it; other props pass through.
 */
export function Button({ variant = "surface", iconOnly = false, className, type = "button", ...props }: ButtonProps) {
  return <button type={type} {...props} className={look(variant, iconOnly, className)} />
}

export type ButtonLinkProps = ComponentProps<"a"> & { href: string } & Look

/** A link drawn as a `Button`: somewhere to go, its `href`, where a button is something to do. */
export function ButtonLink({ variant = "surface", iconOnly = false, className, ...props }: ButtonLinkProps) {
  return <a {...props} className={look(variant, iconOnly, className)} />
}
