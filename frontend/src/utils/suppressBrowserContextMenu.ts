import { isTypingTarget } from "../hooks/keyboardTargets"

/**
 * The right-clicks the browser's own menu keeps: in a text field, a text area or a code
 * editor (cut, copy, paste, spelling) and on a link (copy its address, open it in a new tab).
 */
export function keepsBrowserContextMenu(target: EventTarget | null): boolean {
  return isTypingTarget(target) || (target instanceof Element && target.closest("a[href]") !== null)
}

/**
 * Keeps the browser's own right-click menu from opening over the pipeline editor, except
 * where `keepsBrowserContextMenu` leaves it.
 *
 * Elsewhere a right-click opens one of the editor's menus or does nothing. The listener is
 * on the document in the bubble phase, so it runs after an editor menu's own handler and
 * after CodeMirror's, which read `defaultPrevented`, and it also covers surfaces with no menu
 * of their own and anything portalled into the body.
 */
export function suppressBrowserContextMenu(target: Document = document): () => void {
  const cancel = (event: Event) => {
    if (!keepsBrowserContextMenu(event.target)) event.preventDefault()
  }
  target.addEventListener("contextmenu", cancel)
  return () => target.removeEventListener("contextmenu", cancel)
}
