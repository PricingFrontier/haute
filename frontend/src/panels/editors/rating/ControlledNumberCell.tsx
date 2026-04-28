import { useState, useEffect } from "react"

/** Controlled number input that syncs from prop and commits on blur. */
export function ControlledNumberCell({ val, onCommit, ...rest }: {
  val: number
  onCommit: (v: string) => void
} & Omit<React.InputHTMLAttributes<HTMLInputElement>, "value" | "onChange" | "onBlur" | "type" | "inputMode" | "step">) {
  const [local, setLocal] = useState(String(val))
  // eslint-disable-next-line react-hooks/set-state-in-effect -- sync controlled value from prop
  useEffect(() => setLocal(String(val)), [val])
  return (
    <input
      type="text"
      inputMode="decimal"
      value={local}
      onChange={(e) => setLocal(e.target.value)}
      onBlur={() => onCommit(local)}
      {...rest}
    />
  )
}
