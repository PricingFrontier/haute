import type { ComponentProps } from "react"
import { classNames } from "./classNames"

/**
 * A field's size: `regular`, a side panel's field; `compact`, the same in a dense row;
 * `large`, with more room round its text, for a dialog or a node's editor.
 */
type FieldSize = "compact" | "regular" | "large"

/** How a field is sized. The prop is not `size`, which a text box and a dropdown already have. */
interface FieldLook {
  fieldSize?: FieldSize
}

const look = (size: FieldSize, className: string | undefined) => classNames("field", size !== "regular" && `field-${size}`, className)

/**
 * A text box in the kit's field look (field.css). A `className` lays it out and never
 * colours it; other props, its `ref` among them, pass through.
 */
export function Input({ fieldSize = "regular", className, ...props }: ComponentProps<"input"> & FieldLook) {
  return <input {...props} className={look(fieldSize, className)} />
}

/** A text box of several lines, drawn as `Input` is. */
export function TextArea({ fieldSize = "regular", className, ...props }: ComponentProps<"textarea"> & FieldLook) {
  return <textarea {...props} className={look(fieldSize, className)} />
}

/** A native dropdown, drawn as `Input` is; its `option`s are its children. */
export function Select({ fieldSize = "regular", className, ...props }: ComponentProps<"select"> & FieldLook) {
  return <select {...props} className={look(fieldSize, className)} />
}
