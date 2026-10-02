import { vi } from "vitest"
import { VALUE_COLOR_TOKENS } from "../panels/modelling/beeswarm"

export type PaintedDot = { x: number; y: number; fill: string }

/** The value colour tokens as index.css defines them. */
export const BEESWARM_TOKENS = { low: "#008bfb", high: "#ff0051", neutral: "#7c3aed" } as const

/**
 * Stub the 2D canvas context jsdom lacks, recording each painted dot with the
 * fill colour current when it was drawn, and set the beeswarm's colour tokens
 * on the document root. `clearRect` starts a new painting. Restore with
 * `vi.restoreAllMocks()`.
 */
export function stubBeeswarmCanvas(): { dots: PaintedDot[] } {
  const painting = { dots: [] as PaintedDot[] }
  const context = {
    fillStyle: "",
    globalAlpha: 1,
    setTransform: vi.fn(),
    clearRect: () => {
      painting.dots = []
    },
    beginPath: vi.fn(),
    moveTo: vi.fn(),
    arc(x: number, y: number) {
      painting.dots.push({ x, y, fill: this.fillStyle })
    },
    fill: vi.fn(),
  }
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(
    context as unknown as CanvasRenderingContext2D,
  )
  const root = document.documentElement.style
  root.setProperty(VALUE_COLOR_TOKENS.low, BEESWARM_TOKENS.low)
  root.setProperty(VALUE_COLOR_TOKENS.high, BEESWARM_TOKENS.high)
  root.setProperty(VALUE_COLOR_TOKENS.neutral, BEESWARM_TOKENS.neutral)
  return painting
}
