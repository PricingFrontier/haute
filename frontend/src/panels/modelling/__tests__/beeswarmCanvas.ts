import { vi } from "vitest"
import { SHAP_VALUE_TOKENS } from "../beeswarm"

export type PaintedDot = { x: number; y: number; fill: string }

/** The SHAP value colour tokens as index.css resolves them. */
export const BEESWARM_TOKENS = { low: "#008bfb", high: "#ff0051", none: "#828899" } as const

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
  root.setProperty(SHAP_VALUE_TOKENS.low, BEESWARM_TOKENS.low)
  root.setProperty(SHAP_VALUE_TOKENS.high, BEESWARM_TOKENS.high)
  root.setProperty(SHAP_VALUE_TOKENS.none, BEESWARM_TOKENS.none)
  return painting
}
