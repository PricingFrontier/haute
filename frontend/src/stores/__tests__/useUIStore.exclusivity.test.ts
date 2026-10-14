/**
 * Right-panel mutual exclusivity (specs/frontend-assistant-ui &
 * frontend-shared): utility / imports / git / assistant are exclusive BY
 * CONSTRUCTION — every setter clears the other three, so the App cascade
 * can never render two panels.
 */

import { beforeEach, describe, expect, it } from "vitest"

import useUIStore from "../useUIStore"

function reset() {
  useUIStore.setState({
    utilityOpen: false,
    constantsOpen: false,
    gitOpen: false,
    assistantOpen: false,
    filesPlace: null,
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
