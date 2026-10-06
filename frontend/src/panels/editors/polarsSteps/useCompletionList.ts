import { useId, useState, type KeyboardEvent } from "react"

/**
 * One entry of a completion list: `value` identifies it when accepted,
 * `label` is the name shown (and matched), `note` sits at the right (a
 * column's type, `new`, a function's description) and `mark` before it.
 */
export type Completion = { value: string; label: string; note?: string | null; mark?: string }

/** Plain names as completions, with an optional note per name. */
export function namesAsCompletions(names: readonly string[], note: (name: string) => string | null = () => null): Completion[] {
  return names.map((name) => ({ value: name, label: name, note: note(name) }))
}

/**
 * The completion list behind a text box. `candidates` are the entries that
 * match what is typed right now; they are listed only while the list is
 * open. Focusing the box opens the list with nothing active; typing makes
 * the first entry active unless the typed text is already an exact name
 * (`exact`); Up/Down move through the entries. Tab and Enter accept the
 * active entry; with none active they are left to the box (Tab moves on,
 * Enter commits what was typed). Escape closes the list. `inputProps` wire
 * the box up as a combobox and `listProps` feed a `CompletionList`.
 */
export function useCompletionList(candidates: Completion[], exact: string | null = null) {
  const [open, setOpen] = useState(false)
  // An entry chosen with the arrows, and whether typing activates the first.
  const [cursor, setCursor] = useState<number | null>(null)
  const [typing, setTyping] = useState(false)
  const listId = useId()
  const matches = open ? candidates : []
  const count = matches.length
  const activeIndex: number | null =
    count === 0 ? null : cursor !== null ? Math.min(cursor, count - 1) : typing && exact === null ? 0 : null
  /** Open on focus: every candidate listed, none active. */
  const show = () => {
    setOpen(true)
    setCursor(null)
    setTyping(false)
  }
  /** Open while typing: the first candidate becomes active. */
  const typed = () => {
    setOpen(true)
    setCursor(null)
    setTyping(true)
  }
  const hide = () => setOpen(false)
  /** Handle a key in the box; true when the list consumed it. */
  const onKeyDown = (event: KeyboardEvent<HTMLElement>, accept: (entry: Completion) => void): boolean => {
    if (count > 0 && event.key === "ArrowDown") {
      event.preventDefault()
      setCursor(activeIndex === null ? 0 : (activeIndex + 1) % count)
    } else if (count > 0 && event.key === "ArrowUp") {
      event.preventDefault()
      setCursor(activeIndex === null ? count - 1 : (activeIndex - 1 + count) % count)
    } else if (activeIndex !== null && (event.key === "Tab" || event.key === "Enter")) {
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
    "aria-expanded": count > 0,
    "aria-controls": listId,
    "aria-activedescendant": activeIndex !== null ? `${listId}-${activeIndex}` : undefined,
    autoComplete: "off",
    spellCheck: false,
  }
  const listProps = { id: listId, matches, activeIndex, matched: open ? exact : null, onHover: (index: number) => setCursor(index) }
  return { open, matches, activeIndex, show, typed, hide, onKeyDown, inputProps, listProps }
}
