/**
 * Right-panel mutual exclusivity (docs/specs/frontend-assistant-ui &
 * frontend-shared): utility / imports / git / assistant are exclusive BY
 * CONSTRUCTION — every setter clears the other three, so the App cascade
 * can never render two panels.
 */

import { beforeEach, describe, expect, it } from "vitest"

import useUIStore from "../useUIStore"

function reset() {
  useUIStore.setState({
    utilityOpen: false,
    importsOpen: false,
    gitOpen: false,
    assistantOpen: false,
  })
}

const FLAGS = ["utilityOpen", "importsOpen", "gitOpen", "assistantOpen"] as const
type Flag = (typeof FLAGS)[number]

const SETTERS: Record<Flag, (open: boolean) => void> = {
  utilityOpen: (open) => useUIStore.getState().setUtilityOpen(open),
  importsOpen: (open) => useUIStore.getState().setImportsOpen(open),
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

  it("closing the assistant panel leaves the others closed", () => {
    SETTERS.assistantOpen(true)
    SETTERS.assistantOpen(false)
    const state = useUIStore.getState()
    for (const flag of FLAGS) expect(state[flag]).toBe(false)
  })
})
