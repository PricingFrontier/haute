import { describe, it, expect } from "vitest"
import {
  reducePan,
  IDLE,
  DEFAULT_PAN_CONFIG,
  MIDDLE_BUTTON,
  RIGHT_BUTTON,
  type PanState,
  type PanEvent,
  type PanCommand,
} from "../panController"

/** Feed a sequence of events through the reducer, collecting every command. */
function run(events: PanEvent[], start: PanState = IDLE) {
  let state = start
  const commands: PanCommand[] = []
  for (const event of events) {
    const next = reducePan(state, event)
    state = next.state
    commands.push(...next.commands)
  }
  return { state, commands }
}

const LEFT_BUTTON = 0

describe("reducePan", () => {
  it("middle button begins a pan immediately, anywhere over the canvas", () => {
    const { state, commands } = run([
      { type: "pointerDown", button: MIDDLE_BUTTON, x: 10, y: 10, overCanvas: true },
    ])
    expect(state).toEqual({ kind: "panning", lastX: 10, lastY: 10 })
    expect(commands).toEqual([{ type: "beginPan" }])
  })

  it("middle button steps the viewport by the move delta", () => {
    const { commands } = run([
      { type: "pointerDown", button: MIDDLE_BUTTON, x: 10, y: 10, overCanvas: true },
      { type: "pointerMove", x: 25, y: 5 },
      { type: "pointerMove", x: 30, y: 5 },
    ])
    expect(commands).toEqual([
      { type: "beginPan" },
      { type: "panBy", dx: 15, dy: -5 },
      { type: "panBy", dx: 5, dy: 0 },
    ])
  })

  it("middle pan ends on pointer up", () => {
    const { state, commands } = run([
      { type: "pointerDown", button: MIDDLE_BUTTON, x: 0, y: 0, overCanvas: true },
      { type: "pointerMove", x: 5, y: 5 },
      { type: "pointerUp", button: MIDDLE_BUTTON },
    ])
    expect(state).toEqual(IDLE)
    expect(commands).toContainEqual({ type: "endPan" })
  })

  it("ignores presses outside the canvas", () => {
    const { state, commands } = run([
      { type: "pointerDown", button: MIDDLE_BUTTON, x: 10, y: 10, overCanvas: false },
    ])
    expect(state).toEqual(IDLE)
    expect(commands).toEqual([])
  })

  it("ignores the left button (left-drag is React Flow's marquee select)", () => {
    const { state, commands } = run([
      { type: "pointerDown", button: LEFT_BUTTON, x: 10, y: 10, overCanvas: true },
    ])
    expect(state).toEqual(IDLE)
    expect(commands).toEqual([])
  })

  it("right press then still release opens the menu at the press origin", () => {
    const { state, commands } = run([
      { type: "pointerDown", button: RIGHT_BUTTON, x: 40, y: 60, overCanvas: true },
      { type: "pointerUp", button: RIGHT_BUTTON },
    ])
    expect(state).toEqual(IDLE)
    expect(commands).toEqual([
      { type: "startMenuTimer" },
      { type: "cancelMenuTimer" },
      { type: "openMenu", x: 40, y: 60 },
    ])
  })

  it("right press held still past the debounce opens the menu (timer)", () => {
    const { state, commands } = run([
      { type: "pointerDown", button: RIGHT_BUTTON, x: 40, y: 60, overCanvas: true },
      { type: "menuTimer" },
    ])
    expect(state).toEqual({ kind: "menuArmed" })
    expect(commands).toEqual([
      { type: "startMenuTimer" },
      { type: "openMenu", x: 40, y: 60 },
    ])
  })

  it("a tiny jitter under the threshold still opens the menu (does not pan)", () => {
    const { state, commands } = run([
      { type: "pointerDown", button: RIGHT_BUTTON, x: 40, y: 60, overCanvas: true },
      { type: "pointerMove", x: 42, y: 61 }, // dist ~2.2 < 4px threshold
      { type: "pointerUp", button: RIGHT_BUTTON },
    ])
    expect(state).toEqual(IDLE)
    expect(commands).toContainEqual({ type: "openMenu", x: 40, y: 60 })
    expect(commands).not.toContainEqual({ type: "beginPan" })
  })

  it("right press that moves past the threshold pans and cancels the menu", () => {
    const { state, commands } = run([
      { type: "pointerDown", button: RIGHT_BUTTON, x: 40, y: 60, overCanvas: true },
      { type: "pointerMove", x: 60, y: 60 }, // dist 20 > threshold
      { type: "pointerMove", x: 70, y: 60 },
      { type: "pointerUp", button: RIGHT_BUTTON },
    ])
    expect(state).toEqual(IDLE)
    expect(commands).toEqual([
      { type: "startMenuTimer" },
      { type: "cancelMenuTimer" },
      { type: "beginPan" },
      { type: "panBy", dx: 10, dy: 0 },
      { type: "endPan" },
    ])
    expect(commands).not.toContainEqual({ type: "openMenu", x: 40, y: 60 })
  })

  it("once the right-drag commits to a pan, a late timer cannot open a menu", () => {
    const { commands } = run([
      { type: "pointerDown", button: RIGHT_BUTTON, x: 40, y: 60, overCanvas: true },
      { type: "pointerMove", x: 80, y: 60 }, // commit to pan
      { type: "menuTimer" }, // stray timer (hook should have cleared it; reducer must ignore)
      { type: "pointerUp", button: RIGHT_BUTTON },
    ])
    expect(commands).not.toContainEqual({ type: "openMenu", x: 40, y: 60 })
  })

  it("threshold is exclusive — exactly at the threshold does not yet pan", () => {
    const { state } = run([
      { type: "pointerDown", button: RIGHT_BUTTON, x: 0, y: 0, overCanvas: true },
      { type: "pointerMove", x: DEFAULT_PAN_CONFIG.dragThresholdPx, y: 0 },
    ])
    expect(state.kind).toBe("pendingRight")
  })

  it("cancel during a pending right press drops the gesture and clears the timer", () => {
    const { state, commands } = run([
      { type: "pointerDown", button: RIGHT_BUTTON, x: 0, y: 0, overCanvas: true },
      { type: "cancel" },
    ])
    expect(state).toEqual(IDLE)
    expect(commands).toContainEqual({ type: "cancelMenuTimer" })
    expect(commands).not.toContainEqual({ type: "openMenu", x: 0, y: 0 })
  })

  it("after the menu is armed, motion is swallowed until release", () => {
    const { state, commands } = run([
      { type: "pointerDown", button: RIGHT_BUTTON, x: 5, y: 5, overCanvas: true },
      { type: "menuTimer" },
      { type: "pointerMove", x: 80, y: 80 },
      { type: "pointerUp", button: RIGHT_BUTTON },
    ])
    expect(state).toEqual(IDLE)
    expect(commands).not.toContainEqual({ type: "beginPan" })
    expect(commands.filter((c) => c.type === "panBy")).toHaveLength(0)
  })
})
