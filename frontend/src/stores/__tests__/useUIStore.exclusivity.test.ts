/**
 * Right-panel mutual exclusivity (specs/frontend-assistant-ui &
 * frontend-shared): utility / imports / git / assistant are exclusive BY
 * CONSTRUCTION — every setter clears the other three, so the App cascade
 * can never render two panels.
 */

import { beforeEach, describe, expect, it, vi } from "vitest"

import useUIStore from "../useUIStore"

function reset() {
  useUIStore.setState({
    utilityOpen: false,
    constantsOpen: false,
    gitOpen: false,
    assistantOpen: false,
    filesPlace: null,
    utilityUnsaved: false,
    utilityLeave: null,
    utilityLeaveActions: [],
  })
}

const FLAGS = ["utilityOpen", "constantsOpen", "gitOpen", "assistantOpen"] as const
type Flag = (typeof FLAGS)[number]

const SETTERS: Record<Flag, (open: boolean) => void> = {
  utilityOpen: (open) => useUIStore.getState().setUtilityOpen(open),
  constantsOpen: (open) => useUIStore.getState().setConstantsOpen(open),
  gitOpen: (open) => useUIStore.getState().setGitOpen(open),
  assistantOpen: (open) => useUIStore.getState().setAssistantOpen(open),
}

describe("useUIStore right-panel exclusivity", () => {
  beforeEach(reset)

  it.each(FLAGS)("opening %s clears every other panel flag", (flag) => {
    for (const other of FLAGS.filter((candidate) => candidate !== flag)) {
      SETTERS[other](true)
    }
    SETTERS[flag](true)

    const state = useUIStore.getState()
    expect(state[flag]).toBe(true)
    for (const other of FLAGS.filter((candidate) => candidate !== flag)) {
      expect(state[other]).toBe(false)
    }
  })

  it.each(FLAGS)("the Files panel and %s clear each other", (flag) => {
    useUIStore.getState().setFilesPlace("pipeline")
    SETTERS[flag](true)
    expect(useUIStore.getState().filesPlace).toBeNull()

    useUIStore.getState().setFilesPlace("local")
    const state = useUIStore.getState()
    expect(state.filesPlace).toBe("local")
    for (const other of FLAGS) expect(state[other]).toBe(false)
  })

  it("asking the assistant to fix a node closes the Files panel", () => {
    useUIStore.getState().setFilesPlace("local")
    useUIStore.getState().askAssistantToFix("node_1")
    expect(useUIStore.getState().filesPlace).toBeNull()
  })

  it("closing the assistant panel leaves the others closed", () => {
    SETTERS.assistantOpen(true)
    SETTERS.assistantOpen(false)
    const state = useUIStore.getState()
    for (const flag of FLAGS) expect(state[flag]).toBe(false)
  })
})

describe("the utility panel holding an unsaved edit", () => {
  beforeEach(() => {
    reset()
    useUIStore.getState().setUtilityOpen(true)
    useUIStore.getState().setUtilityUnsaved(true)
  })

  it.each([
    ["closing it", () => useUIStore.getState().setUtilityOpen(false)],
    ["opening Git", () => useUIStore.getState().setGitOpen(true)],
    ["opening Files", () => useUIStore.getState().setFilesPlace("pipeline")],
    ["opening constants", () => useUIStore.getState().setConstantsOpen(true)],
    ["opening the assistant", () => useUIStore.getState().setAssistantOpen(true)],
    ["asking the assistant to fix a node", () => useUIStore.getState().askAssistantToFix("node_1")],
  ])("holds back %s until the edit is saved", (_name, change) => {
    change()

    const held = useUIStore.getState()
    expect(held.utilityOpen).toBe(true)
    expect(held.gitOpen || held.constantsOpen || held.assistantOpen || held.filesPlace !== null).toBe(false)
    expect(held.utilityLeave).not.toBeNull()

    useUIStore.getState().completeUtilityLeave()
    const done = useUIStore.getState()
    expect(done.utilityOpen).toBe(false)
    expect(done.utilityLeave).toBeNull()
    expect(done.utilityUnsaved).toBe(false)
  })

  it("applies every change held back, together", () => {
    useUIStore.getState().setUtilityOpen(false)
    useUIStore.getState().setGitOpen(true)

    useUIStore.getState().completeUtilityLeave()

    expect(useUIStore.getState().gitOpen).toBe(true)
    expect(useUIStore.getState().utilityOpen).toBe(false)
  })

  it("drops the held change when the save is refused", () => {
    useUIStore.getState().setGitOpen(true)

    useUIStore.getState().cancelUtilityLeave()

    const state = useUIStore.getState()
    expect(state.utilityLeave).toBeNull()
    expect(state.utilityOpen).toBe(true)
    expect(state.gitOpen).toBe(false)
  })

  it("holds an action that hides the panel, another view's, until the edit is saved", () => {
    const showView = vi.fn()

    useUIStore.getState().afterUtilitySaved(showView)

    expect(showView).not.toHaveBeenCalled()
    expect(useUIStore.getState().utilityLeave).not.toBeNull()
    useUIStore.getState().completeUtilityLeave()
    expect(showView).toHaveBeenCalledOnce()
    expect(useUIStore.getState().utilityLeaveActions).toEqual([])
  })

  it("drops a held action when the save is refused", () => {
    const showView = vi.fn()
    useUIStore.getState().afterUtilitySaved(showView)

    useUIStore.getState().cancelUtilityLeave()
    useUIStore.getState().setUtilityUnsaved(false)
    useUIStore.getState().completeUtilityLeave()

    expect(showView).not.toHaveBeenCalled()
  })

  it("runs an action at once while nothing is unsaved", () => {
    useUIStore.getState().setUtilityUnsaved(false)
    const showView = vi.fn()

    useUIStore.getState().afterUtilitySaved(showView)

    expect(showView).toHaveBeenCalledOnce()
    expect(useUIStore.getState().utilityLeave).toBeNull()
  })

  it("holds nothing back once the edit is saved", () => {
    useUIStore.getState().setUtilityUnsaved(false)
    useUIStore.getState().setGitOpen(true)

    expect(useUIStore.getState().gitOpen).toBe(true)
    expect(useUIStore.getState().utilityLeave).toBeNull()
  })
})
