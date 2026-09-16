import { useId, useState, type KeyboardEvent } from "react"

/**
 * The completion list behind a text box. `candidates` are the names that
 * match what is typed right now; they are listed only while the list is
 * open. Up/Down move through them, Tab accepts the active one, Escape closes
 * the list; every other key is left to the box. `inputProps` wire the box
 * up as a combobox and `listProps` feed a `CompletionList`.
 */
export function useCompletionList(candidates: string[]) {
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(0)
  const listId = useId()
  const matches = open ? candidates : []
  const activeIndex = Math.min(active, Math.max(matches.length - 1, 0))
  const show = () => {
    setOpen(true)
    setActive(0)
  }
  const hide = () => setOpen(false)
  /** Handle a key in the box; true when the list consumed it. */
  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>, accept: (name: string) => void): boolean => {
    if (matches.length > 0 && event.key === "ArrowDown") {
      event.preventDefault()
      setActive((activeIndex + 1) % matches.length)
    } else if (matches.length > 0 && event.key === "ArrowUp") {
      event.preventDefault()
      setActive((activeIndex - 1 + matches.length) % matches.length)
    } else if (matches.length > 0 && event.key === "Tab") {
      event.preventDefault()
      hide()
      accept(matches[activeIndex])
    } else if (open && event.key === "Escape") {
      event.preventDefault()
      event.stopPropagation()
      hide()
    } else {
      return false
    }
    return true
  }
  const inputProps = {
    role: "combobox" as const,
    "aria-autocomplete": "list" as const,
    "aria-expanded": matches.length > 0,
    "aria-controls": listId,
    "aria-activedescendant": matches.length > 0 ? `${listId}-${activeIndex}` : undefined,
    autoComplete: "off",
    spellCheck: false,
  }
  const listProps = { id: listId, matches, activeIndex, onHover: setActive }
  return { open, matches, show, hide, onKeyDown, inputProps, listProps }
}
