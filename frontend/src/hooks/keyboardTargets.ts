/**
 * Where a keystroke landed, for the window-level shortcut handlers: the pipeline
 * editor's (`useKeyboardShortcuts`) and the workbench's (`useWorkbenchShortcuts`) leave
 * the same keys to the same controls.
 */

/** Ctrl, or Cmd on a Mac. */
export const hasModifier = (event: KeyboardEvent): boolean => event.ctrlKey || event.metaKey

const element = (target: EventTarget | null): Element | null => (target instanceof Element ? target : null)

/** The keystroke landed in a focused modal dialog, which owns its keys, Escape included. */
export function inModalDialog(target: EventTarget | null): boolean {
  return element(target)?.closest('[role="dialog"][aria-modal="true"]') != null
}

/** The keystroke landed in a text field or a text area, which commit on blur. */
export function isTextField(target: EventTarget | null): boolean {
  const tag = element(target)?.tagName
  return tag === "INPUT" || tag === "TEXTAREA"
}

/** The keystroke is typing: in a text field, a text area or a code editor. */
export function isTypingTarget(target: EventTarget | null): boolean {
  return isTextField(target) || element(target)?.closest(".cm-editor") != null
}

/**
 * Run `save` with a focused field's edit included. Editor fields commit on blur, so a
 * focused field is blurred first and the save runs once React has rendered that commit;
 * otherwise the value being typed would be left out of the save.
 */
export function saveCommittingField(target: EventTarget | null, save: () => void): void {
  if (target instanceof HTMLElement && isTextField(target)) {
    target.blur()
    window.setTimeout(save, 0)
    return
  }
  save()
}
