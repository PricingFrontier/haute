import type { ComponentProps } from "react"
import { classNames } from "./classNames"

/** How a field is sized: a side panel's field, or `compact`, the same in a dense row. */
interface FieldLook {
  compact?: boolean
}

const look = (compact: boolean, className: string | undefined) => classNames("field", compact && "field-compact", className)

/**
 * A text box in the kit's field look (field.css). A `className` lays it out and never
 * colours it; other props, its `ref` among them, pass through.
 */
export function Input({ compact = false, className, ...props }: ComponentProps<"input"> & FieldLook) {
  return <input {...props} className={look(compact, className)} />
}

/** A text box of several lines, drawn as `Input` is. */
export function TextArea({ compact = false, className, ...props }: ComponentProps<"textarea"> & FieldLook) {
  return <textarea {...props} className={look(compact, className)} />
}

/** A native dropdown, drawn as `Input` is; its `option`s are its children. */
export function Select({ compact = false, className, ...props }: ComponentProps<"select"> & FieldLook) {
  return <select {...props} className={look(compact, className)} />
}
