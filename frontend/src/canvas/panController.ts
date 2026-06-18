/**
 * Canvas pan / right-click gesture state machine (pure).
 *
 * React Flow's built-in `panOnDrag` can only pan from the pane background: a
 * node or edge swallows the right button to fire its own context menu, so you
 * can never right-drag-to-pan when the press starts on a node, and a press on
 * an edge falls through to the browser's menu. This controller takes over both
 * panning and context-menu *triggering* so the two can coexist everywhere:
 *
 *   - Middle button pans immediately, from anywhere over the canvas (node,
 *     edge or pane).
 *   - Right button is ambiguous until it resolves: a short debounce after a
 *     still press (or an immediate release without moving) opens the context
 *     menu; any movement past a small threshold cancels the menu and pans from
 *     the current location instead.
 *
 * The reducer is deliberately pure — it consumes pointer/timer events and emits
 * commands (start/cancel the menu timer, begin/step/end a pan, open the menu).
 * The owning hook (`useCanvasPan`) runs the timer, talks to the viewport, and
 * hit-tests the press target. Keeping the machine pure makes the tricky
 * disambiguation fully unit-testable without a DOM.
 */

export interface PanConfig {
  /** Movement (px) past which a held right button becomes a pan, not a menu. */
  dragThresholdPx: number
  /** Delay (ms) after a still right-press before the context menu opens. */
  menuDebounceMs: number
}

export const DEFAULT_PAN_CONFIG: PanConfig = { dragThresholdPx: 4, menuDebounceMs: 150 }

/** PointerEvent.button codes we act on. */
export const MIDDLE_BUTTON = 1
export const RIGHT_BUTTON = 2

export type PanState =
  | { kind: "idle" }
  /** Right button down, deciding between "open menu" and "pan". */
  | { kind: "pendingRight"; originX: number; originY: number }
  /** Actively dragging the viewport (middle button, or a committed right-drag). */
  | { kind: "panning"; lastX: number; lastY: number }
  /** Menu has been opened; swallow further motion until the button releases. */
  | { kind: "menuArmed" }

export type PanEvent =
  | { type: "pointerDown"; button: number; x: number; y: number; overCanvas: boolean }
  | { type: "pointerMove"; x: number; y: number }
  | { type: "pointerUp"; button: number }
  | { type: "menuTimer" }
  | { type: "cancel" }

export type PanCommand =
  | { type: "startMenuTimer" }
  | { type: "cancelMenuTimer" }
  | { type: "beginPan" }
  | { type: "panBy"; dx: number; dy: number }
  | { type: "endPan" }
  | { type: "openMenu"; x: number; y: number }

export const IDLE: PanState = { kind: "idle" }

function distance(ax: number, ay: number, bx: number, by: number): number {
  return Math.hypot(ax - bx, ay - by)
}

export function reducePan(
  state: PanState,
  event: PanEvent,
  config: PanConfig = DEFAULT_PAN_CONFIG,
): { state: PanState; commands: PanCommand[] } {
  switch (state.kind) {
    case "idle": {
      if (event.type === "pointerDown" && event.overCanvas) {
        if (event.button === MIDDLE_BUTTON) {
          return {
            state: { kind: "panning", lastX: event.x, lastY: event.y },
            commands: [{ type: "beginPan" }],
          }
        }
        if (event.button === RIGHT_BUTTON) {
          return {
            state: { kind: "pendingRight", originX: event.x, originY: event.y },
            commands: [{ type: "startMenuTimer" }],
          }
        }
      }
      return { state, commands: [] }
    }

    case "pendingRight": {
      if (event.type === "pointerMove") {
        if (distance(event.x, event.y, state.originX, state.originY) > config.dragThresholdPx) {
          return {
            state: { kind: "panning", lastX: event.x, lastY: event.y },
            commands: [{ type: "cancelMenuTimer" }, { type: "beginPan" }],
          }
        }
        return { state, commands: [] }
      }
      if (event.type === "menuTimer") {
        return {
          state: { kind: "menuArmed" },
          commands: [{ type: "openMenu", x: state.originX, y: state.originY }],
        }
      }
      if (event.type === "pointerUp" && event.button === RIGHT_BUTTON) {
        return {
          state: IDLE,
          commands: [{ type: "cancelMenuTimer" }, { type: "openMenu", x: state.originX, y: state.originY }],
        }
      }
      if (event.type === "cancel") {
        return { state: IDLE, commands: [{ type: "cancelMenuTimer" }] }
      }
      return { state, commands: [] }
    }

    case "panning": {
      if (event.type === "pointerMove") {
        return {
          state: { kind: "panning", lastX: event.x, lastY: event.y },
          commands: [{ type: "panBy", dx: event.x - state.lastX, dy: event.y - state.lastY }],
        }
      }
      if (event.type === "pointerUp" || event.type === "cancel") {
        return { state: IDLE, commands: [{ type: "endPan" }] }
      }
      return { state, commands: [] }
    }

    case "menuArmed": {
      if (event.type === "pointerUp" || event.type === "cancel") {
        return { state: IDLE, commands: [] }
      }
      return { state, commands: [] }
    }
  }
}
