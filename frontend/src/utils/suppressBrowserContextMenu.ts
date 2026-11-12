/**
 * Keeps the browser's own right-click menu from ever opening over the app.
 *
 * A right-click either opens one of the app's menus or does nothing. The listener is
 * on the document, so it also covers surfaces with no menu of their own and anything
 * portalled into the body; an app menu's own handler has already run by then, and
 * cancelling the event a second time changes nothing.
 */
export function suppressBrowserContextMenu(target: Document = document): () => void {
  const cancel = (event: Event) => event.preventDefault()
  target.addEventListener("contextmenu", cancel)
  return () => target.removeEventListener("contextmenu", cancel)
}
