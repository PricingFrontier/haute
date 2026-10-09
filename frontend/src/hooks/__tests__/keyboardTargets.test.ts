import { afterEach, describe, expect, it, vi } from "vitest"
import { hasModifier, inModalDialog, isTextField, isTypingTarget, saveCommittingField } from "../keyboardTargets"

function element(html: string): HTMLElement {
  document.body.innerHTML = html
  const target = document.body.querySelector<HTMLElement>("[data-target]")
  if (target === null) throw new Error("no target")
  return target
}

describe("keyboardTargets", () => {
  afterEach(() => {
    document.body.innerHTML = ""
    vi.useRealTimers()
  })

  it("reads Ctrl, or Cmd on a Mac, as the modifier", () => {
    expect(hasModifier(new KeyboardEvent("keydown", { key: "s", ctrlKey: true }))).toBe(true)
    expect(hasModifier(new KeyboardEvent("keydown", { key: "s", metaKey: true }))).toBe(true)
    expect(hasModifier(new KeyboardEvent("keydown", { key: "s", shiftKey: true }))).toBe(false)
  })

  it("tells a keystroke in a modal dialog, in a text field and in a code editor apart", () => {
    expect(inModalDialog(element('<div role="dialog" aria-modal="true"><button data-target /></div>'))).toBe(true)
    expect(inModalDialog(element('<div role="dialog"><button data-target /></div>'))).toBe(false)
    expect(inModalDialog(null)).toBe(false)

    expect(isTextField(element("<input data-target />"))).toBe(true)
    expect(isTextField(element("<textarea data-target></textarea>"))).toBe(true)
    expect(isTextField(element("<button data-target />"))).toBe(false)

    expect(isTypingTarget(element('<div class="cm-editor"><div data-target contenteditable="true"></div></div>'))).toBe(true)
    expect(isTypingTarget(element("<input data-target />"))).toBe(true)
    expect(isTypingTarget(element("<button data-target />"))).toBe(false)
    expect(isTypingTarget(window)).toBe(false)
  })

  it("saves at once from anywhere but a text field, which it blurs first and saves after", () => {
    vi.useFakeTimers()
    const save = vi.fn()

    saveCommittingField(element("<button data-target />"), save)
    expect(save).toHaveBeenCalledTimes(1)

    const field = element("<input data-target />")
    field.focus()
    saveCommittingField(field, save)
    expect(document.activeElement).not.toBe(field)
    expect(save).toHaveBeenCalledTimes(1)
    vi.runAllTimers()
    expect(save).toHaveBeenCalledTimes(2)
  })
})
